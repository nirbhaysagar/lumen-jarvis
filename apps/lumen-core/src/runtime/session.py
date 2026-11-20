"""
Conversation Session & Context Orchestrator.
Glues together Memory Engine retrieval, LiteLLM routing, and True Concurrency Task Supervisor.
"""
from __future__ import annotations
import os
import re
import sys
import uuid
from typing import Any, AsyncGenerator, Callable, Coroutine
from ..concurrency.models import TaskCheckpoint
from ..concurrency.supervisor import TaskSupervisor
from ..compactor.compactor import ContextCompactor
from ..vault.vault_sync import ObsidianVaultSync
from ..memory_client.client import MemoryServiceClient
from ..tools.runner import SandboxedRunner
from ..tools.approval import ApprovalManager
from ..tools.models import CommandRiskLevel
from ..persona.jarvis import format_context_with_memories
from ..router.model_router import ModelRouter
from ..consolidation.daemon import ConsolidationDaemon
from ..concurrency.models import TaskCheckpoint, CancellationToken
from ..research.agent import DeepResearchAgent
from ..voice.gateway import VoiceGateway
from ..voice.pipeline import VoicePipelineCoordinator
from ..voice.daemon import WakeWordDaemon
from ..voice.stt import WhisperSTTProvider, MockSTTProvider
from ..voice.tts import NativeMacTTS, MockNeuralTTS
from ..sentinel.daemon import SentinelDaemon
from ..analytics.engine import AnalystEngine
from ..router.agent_loop import LumenAgentLoop


def classify_preference(text: str) -> tuple[str, str]:
    """Categorizes user decisions into domain-specific entities to preserve orthogonal bi-temporal lineages."""
    lowered = text.lower()
    if any(k in lowered for k in ["database", "db", "postgres", "postgresql", "mysql", "mongodb", "sqlite", "redis"]):
        return "user_preference", "database"
    if any(k in lowered for k in ["editor", "ide", "vscode", "vs code", "cursor", "vim", "neovim", "sublime"]):
        return "user_preference", "editor"
    if any(k in lowered for k in ["language", "python", "typescript", "javascript", "rust", "golang", "go", "c++"]):
        return "user_preference", "programming_language"
    if any(k in lowered for k in ["framework", "fastapi", "react", "nextjs", "django", "vue"]):
        return "user_preference", "framework"
    if any(k in lowered for k in ["theme", "dark mode", "light mode", "color", "font", "ui"]):
        return "user_preference", "ui_theme"
    return "user_preference", "general"


def extract_triples_from_utterance(text: str) -> list[tuple[str, str, str]]:
    """Extracts candidate relational triples (Subject, Predicate, Object) from conversational text."""
    lowered = text.lower().strip()
    triples = []

    m_pref = re.search(r'\bi prefer\s+([a-zA-Z0-9_\-\.\s]+?)(?:$|\.|\,)', lowered)
    if m_pref:
        triples.append(("User", "PREFERS", m_pref.group(1).strip().rstrip('.,;!?').title()))

    m_dis = re.search(r'\b(?:i dislike|never use)\s+([a-zA-Z0-9_\-\.\s]+?)(?:$|\.|\,)', lowered)
    if m_dis:
        triples.append(("User", "DISLIKES", m_dis.group(1).strip().rstrip('.,;!?').title()))

    m_f = re.search(r'\bi (?:founded|started)\s+([a-zA-Z0-9_\-\.\s]+?)(?:$|\.|\,)', lowered)
    if m_f:
        triples.append(("User", "FOUNDED", m_f.group(1).strip().rstrip('.,;!?').title()))

    m_tech = re.search(r'\b(?:we use|we are using|uses)\s+([a-zA-Z0-9_\-\.]+)', lowered)
    if m_tech:
        triples.append(("Project Lumen", "USES", m_tech.group(1).rstrip('.,;!?').title()))

    return triples


class ConversationSession:
    def __init__(
        self,
        conversation_id: str | None = None,
        memory_client: MemoryServiceClient | None = None,
        router: ModelRouter | None = None,
        supervisor: TaskSupervisor | None = None,
        vault_sync: ObsidianVaultSync | None = None,
        compactor: ContextCompactor | None = None,
        runner: SandboxedRunner | None = None,
        approvals: ApprovalManager | None = None,
        consolidation_daemon: ConsolidationDaemon | None = None,
        research_agent: DeepResearchAgent | None = None,
        voice_gateway: VoiceGateway | None = None,
        wake_word_daemon: WakeWordDaemon | None = None,
        sentinel_daemon: SentinelDaemon | None = None,
        analyst_engine: AnalystEngine | None = None,
    ):
        self.conversation_id = conversation_id or f"conv_{uuid.uuid4().hex[:8]}"
        self.memory = memory_client or MemoryServiceClient()
        self.router = router or ModelRouter()
        self.supervisor = supervisor or TaskSupervisor()
        self.vault = vault_sync or ObsidianVaultSync()
        self.compactor = compactor or ContextCompactor(vault_sync=self.vault)
        self.runner = runner or SandboxedRunner()
        self.approvals = approvals or ApprovalManager()
        self.consolidation = consolidation_daemon or ConsolidationDaemon(
            memory_client=self.memory,
            vault_sync=self.vault,
            supervisor=self.supervisor,
        )
        self.research = research_agent or DeepResearchAgent()
        stt_instance = WhisperSTTProvider() if (os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY")) else MockSTTProvider()
        tts_instance = NativeMacTTS() if sys.platform == "darwin" else MockNeuralTTS()
        self.voice = voice_gateway or VoiceGateway(
            coordinator=VoicePipelineCoordinator(
                turn_handler=self.process_turn,
                stt=stt_instance,
                tts=tts_instance,
            )
        )
        self.wake_daemon = wake_word_daemon or WakeWordDaemon(
            coordinator=self.voice.coordinator
        )
        self.sentinel = sentinel_daemon or SentinelDaemon(vault_sync=self.vault)
        self.analyst = analyst_engine or AnalystEngine(router=self.router, research_agent=self.research)
        self.agent_loop = LumenAgentLoop(session=self, router=self.router, max_steps=8, history_turn_limit=15)
        self.history: list[dict[str, str]] = []
        self._last_turn_was_analyst: bool = False

    async def spawn_background_task(
        self,
        name: str,
        coro_fn: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """Dispatches an asynchronous task to the concurrency supervisor under this conversation."""
        return await self.supervisor.spawn_task(
            name,
            coro_fn,
            self.conversation_id,
            *args,
            **kwargs,
        )

    async def cancel_task(self, task_id: str) -> bool:
        """Cancels a running background task."""
        return await self.supervisor.cancel_task(task_id)

    async def list_tasks(self) -> list[TaskCheckpoint]:
        """Lists tasks associated with this conversation session."""
        return await self.supervisor.list_tasks(self.conversation_id)

    async def poll_notifications(self) -> list[TaskCheckpoint]:
        """Drains any available background task notifications without blocking."""
        notifications: list[TaskCheckpoint] = []
        while True:
            notif = await self.supervisor.get_next_notification(timeout=0.001)
            if not notif:
                break
            notifications.append(notif)
        return notifications

    async def process_turn(self, user_text: str) -> AsyncGenerator[str, None]:
        """
        Executes one full conversation turn:
        1. Checks for concurrency supervisor commands (/tasks, /cancel).
        2. Drains background task notifications and announces them.
        3. Retrieves relevant past memories in parallel.
        4. Formats context with JARVIS persona.
        5. Streams response tokens.
        6. Persists interaction event and extracted facts.
        """
        trace_id = f"tr_{uuid.uuid4().hex[:12]}"

        # 0. Fast-path built-in concurrency commands
        trimmed = user_text.strip()
        if trimmed == "/tasks":
            tasks = await self.list_tasks()
            if not tasks:
                yield "No active or recorded background tasks for this session, sir."
                return
            lines = ["Current background tasks status:"]
            for t in tasks:
                lines.append(f"• [{t.task_id}] {t.name}: {t.state.value} ({int(t.progress * 100)}%) - {t.status_message or 'N/A'}")
            yield "\n".join(lines)
            return

        if trimmed.startswith("/task-cancel "):
            target_id = trimmed.split(" ", 1)[1].strip()
            cancelled = await self.cancel_task(target_id)
            if cancelled:
                yield f"Task {target_id} has been successfully signalled for cooperative termination."
            else:
                yield f"Could not find an active task matching {target_id}."
            return

        if trimmed == "/vault":
            stats = self.vault.get_vault_stats()
            yield (
                f"📚 Obsidian Knowledge Vault Status:\n"
                f"• Vault Path: {self.vault.vault_path}\n"
                f"• Entity Cards: {stats.get('entities', 0)}\n"
                f"• Canonical Memory Notes: {stats['memories']}\n"
                f"• Compacted Episodes: {stats['episodes']}\n"
                f"• Total Markdown Cards: {stats['total_notes']}\n\n"
                f"You can open '{self.vault.vault_path}' in Obsidian to view your visual knowledge graph!"
            )
            return

        if trimmed == "/outbox":
            outbox_stats = await self.memory.get_outbox_stats()
            stats = self.vault.get_vault_stats()
            if outbox_stats:
                yield (
                    f"📮 Transactional Outbox (PostgreSQL ↔ Obsidian):\n"
                    f"• Pending Sync Queue: {outbox_stats.get('pending', 0)}\n"
                    f"• Successfully Synced: {outbox_stats.get('processed', 0)}\n"
                    f"• Failed Syncs: {outbox_stats.get('failed', 0)}\n"
                    f"• Total Outbox Events: {outbox_stats.get('total', 0)}\n\n"
                    f"📚 Obsidian Vault Notes:\n"
                    f"• Entity Cards: {stats.get('entities', 0)}\n"
                    f"• Memory Notes: {stats.get('memories', 0)}\n"
                    f"• Episode Synopses: {stats.get('episodes', 0)}\n"
                    f"• Total Markdown Notes: {stats.get('total_notes', 0)}"
                )
            else:
                yield "Could not retrieve outbox statistics from memory engine."
            return

        if trimmed == "/consolidate":
            yield "🌅 Initializing autonomous knowledge consolidation patrol...\n"
            result = await self.consolidation.run_consolidation_job()
            rep = result.get("report", {})
            yield (
                f"✅ Overnight Consolidation Patrol Completed:\n"
                f"• Latent Conflicts / Collisions: {rep.get('conflicts_detected', 0)}\n"
                f"• High-Similarity Alias Candidates: {rep.get('alias_candidates_found', 0)}\n"
                f"• Graph Orphan Entities (degree <= 1): {rep.get('orphans_detected', 0)}\n"
                f"• New Review Proposals Staged: {rep.get('proposals_staged', 0)}\n"
                f"• Morning Intelligence Digest: {result.get('digest_path')}\n\n"
                f"Pending proposals can be reviewed anytime via `/proposals` and approved via `/approve-proposal <id>`."
            )
            return

        if trimmed == "/digest":
            if self.consolidation.last_digest_path and self.consolidation.last_digest_path.exists():
                content = self.consolidation.last_digest_path.read_text(encoding="utf-8")
                yield f"📄 Latest Intelligence Briefing ({self.consolidation.last_digest_path.name}):\n\n{content}"
            else:
                yield "No digest cached yet for today. Triggering on-demand consolidation...\n"
                result = await self.consolidation.run_consolidation_job()
                import pathlib
                digest_file = pathlib.Path(result["digest_path"])
                content = digest_file.read_text(encoding="utf-8")
                yield f"📄 Morning Intelligence Briefing Generated ({digest_file.name}):\n\n{content}"
            return

        if trimmed.startswith("/search "):
            query = trimmed.split(" ", 1)[1].strip()
            yield f"🔍 Executing Unified Hybrid Search (BM25 + pgvector + Graph RRF) for: '{query}'...\n\n"
            results = await self.memory.search(query=query, limit=5, mode="hybrid")
            if not results:
                yield "No relevant memories found across lexical, vector, or graph channels."
                return

            lines = ["Ranked Hybrid Search Results (Reciprocal Rank Fusion):"]
            for idx, r in enumerate(results, 1):
                entity = r.get("entity", "")
                attr = r.get("attribute", "")
                val = r.get("value", "")
                score = r.get("rrf_score", 0.0)
                ranks = r.get("channel_ranks", {})
                ranks_str = ", ".join([f"{k}: #{v}" for k, v in ranks.items()])
                lines.append(
                    f"{idx}. [{r.get('memory_type', 'fact').upper()}] {entity} ❯ {attr}: {val}\n"
                    f"   • RRF Score: {score:.6f} ({ranks_str})\n"
                    f"   • Fact ID: `{r.get('id')}`"
                )
            yield "\n".join(lines)
            return

        if trimmed.startswith("/research "):
            topic = trimmed.split(" ", 1)[1].strip()
            if not topic:
                yield "Please specify a research topic, Sir. Example: `/research PostgreSQL 1536 vector latency`"
                return

            async def _research_worker(t: str, cancellation_token: CancellationToken = None, progress_reporter: Any = None):
                dossier = await self.research.run_research(t, token=cancellation_token, checkpoint_cb=progress_reporter)
                path = self.vault.export_research_dossier(dossier)
                # Stage top claims into review queue as Tier-B knowledge proposals
                for c in dossier.claims[:4]:
                    try:
                        await self.memory.propose_triple(
                            subject=t.title()[:50],
                            predicate="EVIDENCE_FOR",
                            object_=c.claim[:100],
                            source_event_id=f"research_{path.stem}",
                        )
                    except Exception:
                        pass
                return f"Research dossier compiled at {path}"

            task_id = await self.spawn_background_task(
                f"Deep Research: {topic[:30]}",
                _research_worker,
                topic,
            )
            yield (
                f"👁️ [Lumen 'The Eyes'] Dispatched autonomous deep research task for: '{topic}'\n"
                f"• Task ID: `{task_id}`\n"
                f"• Search Surfaces: Web (DuckDuckGo), Developer Debates (Hacker News, Reddit), Primary Science (arXiv)\n"
                f"• Exploration Depth: 2-hop recursive citation graph traversal into un-SEO resources\n"
                f"• Quarantine Shield: Active (neutralizing prompt injection attempts)\n\n"
                f"You can continue chatting immediately, Sir. Track live progress anytime via `/tasks`."
            )
            return

        if trimmed == "/dossiers":
            files = list(self.vault.research_dir.glob("*.md"))
            if not files:
                yield "No research dossiers have been generated yet, Sir. Use `/research <topic>` to dispatch an investigation."
                return
            lines = [f"📚 Obsidian Research Dossiers ({len(files)} compiled):"]
            for f in sorted(files, key=lambda x: x.stat().st_mtime, reverse=True):
                lines.append(f"• `{f.stem}`: {f.name} (file://{f.resolve()})")
            lines.append("\nRead full dossier anytime via `/dossier <name>`.")
            yield "\n".join(lines)
            return

        if trimmed.startswith("/dossier "):
            raw_slug = trimmed.split(" ", 1)[1].strip().lower().replace(".md", "")
            # Security Sanitize: Strip any directory traversal tokens (../, /, \, etc.)
            clean_slug = re.sub(r"[^a-zA-Z0-9_\-]+", "_", raw_slug).strip("_")
            if not clean_slug:
                yield "Invalid dossier name specified, Sir."
                return

            research_root = self.vault.research_dir.resolve()
            target = (research_root / f"{clean_slug}.md").resolve()

            # Path Traversal Guard: Ensure target cannot escape research_dir
            if not target.is_relative_to(research_root):
                yield f"⚠️ Security violation: Invalid path traversal detected for dossier '{raw_slug}'."
                return

            if not target.exists():
                matches = [
                    m for m in research_root.glob(f"*{clean_slug}*.md")
                    if m.resolve().is_relative_to(research_root)
                ]
                if matches:
                    target = matches[0]
                else:
                    yield f"Could not find a research dossier matching '{clean_slug}'. Use `/dossiers` to list available notes."
                    return
            content = target.read_text(encoding="utf-8")
            yield f"📄 Research Dossier: {target.name}\n\n{content}"
            return

        if trimmed.startswith("/analyze ") or trimmed.startswith("/plot "):
            query = trimmed.split(" ", 1)[1].strip()
            if not query:
                yield "Please specify an analytical query, Sir. Example: `/analyze rockstar games annual revenue trajectory`"
                return
            async for token in self.analyst.analyze_and_plot(query, preview=True):
                yield token
            return

        if trimmed == "/approvals":
            pending = self.approvals.list_pending()
            if not pending:
                yield "No commands currently awaiting approval, Sir."
                return
            lines = ["Pending Authorization Requests (Two-Man Rule Gate):"]
            for req in pending:
                lines.append(f"• [{req.approval_id}] Command: `{req.command}`\n  Reason: {req.reason}")
            lines.append("\nApprove via `/approve <id>` or cancel via `/reject <id>`.")
            yield "\n".join(lines)
            return

        if trimmed.startswith("/approve "):
            target_id = trimmed.split(" ", 1)[1].strip()
            req = self.approvals._pending.get(target_id)
            if not req:
                yield f"No pending approval found for ID: {target_id}"
                return
            yield f"🔓 Authorization granted for `{req.command}`. Executing in sandbox...\n"
            self.approvals.approve(target_id)
            res = await self.runner.execute(req.command, bypass_gatekeeper=True, timeout=30.0)
            status_tag = "SUCCESS" if res.is_success else f"FAILED (Exit {res.exit_code})"
            yield (
                f"[{status_tag} in {res.duration_ms}ms]\n"
                f"{res.stdout or res.stderr or '(no output produced)'}"
            )
            return

        if trimmed.startswith("/reject "):
            target_id = trimmed.split(" ", 1)[1].strip()
            rejected = self.approvals.reject(target_id)
            if rejected:
                yield f"❌ Request {target_id} has been rejected and cancelled by operator."
            else:
                yield f"No pending approval found for ID: {target_id}"
            return

        if trimmed.startswith("/exec "):
            cmd_to_run = trimmed.split(" ", 1)[1].strip()
            evaluation = self.runner.gatekeeper.evaluate(cmd_to_run)

            if evaluation.risk_level == CommandRiskLevel.FORBIDDEN:
                yield (
                    f"🚫 [SECURITY BLOCK - AST GATEKEEPER]\n"
                    f"Command strictly prohibited by security policy.\n"
                    f"• Offending Command: `{cmd_to_run}`\n"
                    f"• Violation: {evaluation.reason}"
                )
                return

            if evaluation.risk_level == CommandRiskLevel.CAUTION:
                pending_req = self.approvals.request_approval(cmd_to_run, evaluation.reason)
                yield (
                    f"⚠️ [Two-Man Rule Authorization Gate]\n"
                    f"The requested command modifies system state or environment.\n"
                    f"• Command: `{cmd_to_run}`\n"
                    f"• Risk Rationale: {evaluation.reason}\n\n"
                    f"To authorize execution, reply with:\n"
                    f"  /approve {pending_req.approval_id}\n\n"
                    f"To deny and cancel, reply with:\n"
                    f"  /reject {pending_req.approval_id}"
                )
                return

            # Risk level is SAFE -> Auto-execute in sandbox
            yield f"⚡ [Safe Command] Executing `{cmd_to_run}`...\n\n"
            res = await self.runner.execute(cmd_to_run, timeout=15.0)
            yield (
                f"[Output: Exit {res.exit_code} in {res.duration_ms}ms]\n"
                f"{res.stdout or res.stderr or '(no output produced)'}"
            )
            return

        if trimmed == "/compact":
            if len(self.history) <= self.compactor.low_watermark_turns:
                yield "Conversation context is currently minimal. No compaction required, Sir."
                return
            old_count = len(self.history)
            self.history, summary = self.compactor.compact(self.conversation_id, self.history, force=True)
            yield (
                f"🧹 Context Compaction Complete:\n"
                f"• Pruned and condensed {old_count - len(self.history)} older turns.\n"
                f"• Exported Episode note to: vault/episodes/\n"
                f"• Active context refreshed with summary anchor."
            )
            return

        if trimmed == "/proposals":
            pending = await self.memory.list_review_queue(status="PENDING", trace_id=trace_id)
            if not pending:
                yield "The Review Queue is currently clean, Sir. No pending memory proposals."
                return
            lines = ["📋 Staged Memory Proposals in Review Queue (Two-Man Rule Gate):"]
            for p in pending:
                tier_badge = "⚠️ [CONTRADICTION - TIER B]" if p.get("tier") == "TIER_B" else "ℹ️ [TIER A]"
                conflict = f"\n  Reason: {p['conflict_reason']}" if p.get("conflict_reason") else ""
                lines.append(
                    f"• [{p['id']}] {tier_badge}\n"
                    f"  Proposal: `{p['subject_raw']}` ──[{p['predicate_raw']}]──► `{p['object_raw']}`"
                    f"{conflict}\n"
                    f"  Action: /approve-proposal {p['id']}  or  /reject-proposal {p['id']}"
                )
            yield "\n\n".join(lines)
            return

        if trimmed.startswith("/approve-proposal "):
            prop_id = trimmed.split(" ", 1)[1].strip()
            res = await self.memory.resolve_review_proposal(prop_id, decision="APPROVED", trace_id=trace_id)
            if res:
                yield f"✅ Proposal [{prop_id}] approved! Canonical relationship activated and superseding executed."
            else:
                yield f"Could not approve proposal [{prop_id}]. Ensure ID is valid."
            return

        if trimmed.startswith("/reject-proposal "):
            prop_id = trimmed.split(" ", 1)[1].strip()
            res = await self.memory.resolve_review_proposal(prop_id, decision="REJECTED", trace_id=trace_id)
            if res:
                yield f"❌ Proposal [{prop_id}] rejected and dismissed by operator."
            else:
                yield f"Could not reject proposal [{prop_id}]. Ensure ID is valid."
            return

        if trimmed.startswith("/graph"):
            parts = trimmed.split(" ", 1)
            target_entity = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "User"
            graph_data = await self.memory.traverse_graph(target_entity, max_depth=2, trace_id=trace_id)
            if not graph_data or not graph_data.get("edges"):
                yield f"No active graph relationships found connected to '{target_entity}', Sir."
                return
            lines = [f"🌐 Knowledge Graph Neighborhood for '{target_entity}':"]
            for edge in graph_data["edges"]:
                indent = "  " * edge["depth"]
                lines.append(f"{indent}└─ [{edge['depth']}] `{edge['subject_name']}` ──[{edge['predicate']}]──► `{edge['object_name']}`")
            yield "\n".join(lines)
            return

        if trimmed.startswith("/trace "):
            fact_id = trimmed.split(" ", 1)[1].strip()
            trace_data = await self.memory.get_fact_trace(fact_id, trace_id=trace_id)
            if not trace_data:
                yield f"Could not find provenance trace for fact ID: {fact_id}."
                return
            fact = trace_data["fact"]
            events = trace_data.get("source_events", [])
            lineage = trace_data.get("history_lineage", [])
            
            lines = [
                f"🔎 Provenance Trace Graph for Fact [{fact['id']}]",
                f"• Entity: {fact['entity']} | Attribute: {fact['attribute']}",
                f"• Active Value: {fact['value']}",
                f"• Validity Window: {fact['valid_from']} ──► {fact['valid_to'] or 'PRESENT (ACTIVE)'}",
                "",
                "📜 Source Utterance Receipts (L0 Events):"
            ]
            for e in events:
                payload = e.get("payload", {})
                lines.append(f"  └─ [{e['created_at']}] Source: {e['source']} | Content: {payload}")
                
            if lineage and len(lineage) > 1:
                lines.append("")
                lines.append("⏳ Historical Evolution Lineage (Bi-Temporal Superseding):")
                for idx, item in enumerate(lineage, 1):
                    status = "ACTIVE" if item.get("valid_to") is None else f"SUPERSEDED at {item.get('valid_to')}"
                    lines.append(f"  Version {idx}: {item['value']} [{status}] (from: {item['valid_from']})")
                    
            yield "\n".join(lines)
            return

        if trimmed == "/voice-status":
            m = self.voice.coordinator.metrics
            running_str = f"RUNNING on ws://{self.voice.host}:{self.voice.port}/voice" if self.voice._is_running else "STANDBY"
            yield (
                f"🎙️ [Voice Gateway & Barge-In Metrics]\n"
                f"• Server Status: {running_str}\n"
                f"• Total Audio Frames Processed: {m.total_frames_processed}\n"
                f"• Speech Frames Detected: {m.speech_frames_detected}\n"
                f"• Sub-50ms Barge-In Events: {m.barge_in_events}\n"
                f"• Average VAD Latency: {m.average_vad_latency_ms:.2f}ms\n"
                f"• Time-To-First-Audio (TTFA): {m.time_to_first_audio_ms:.2f}ms\n"
                f"• Active Output Queue: {self.voice.coordinator.player.queued_frames_count} frames"
            )
            return

        if trimmed == "/voice":
            if not self.voice._is_running:
                try:
                    await self.voice.start()
                    yield (
                        f"🎙️ Sub-Second Voice Gateway is now ACTIVE!\n"
                        f"• Listening on: `ws://{self.voice.host}:{self.voice.port}/voice`\n"
                        f"• Protocol: Bidirectional 16-bit 16kHz Linear PCM\n"
                        f"• Barge-In: Active (preempts LLM and TTS under 50ms upon speech onset)\n"
                        f"• Echo Gate: Dynamic sensitivity modulation active"
                    )
                except Exception as e:
                    yield f"⚠️ Could not start Voice Gateway: {e}"
            else:
                yield f"🎙️ Voice Gateway is already running at `ws://{self.voice.host}:{self.voice.port}/voice`."
            return

        if trimmed == "/voice-stop":
            if self.voice._is_running:
                await self.voice.stop()
                yield "🎙️ Voice Gateway has been stopped."
            else:
                yield "Voice Gateway is not currently running."
            return

        if trimmed == "/listen":
            if not self.wake_daemon.is_running:
                try:
                    await self.wake_daemon.start()
                    yield (
                        f"🎙️ [Hands-Free Ambient Wake-Word Active]\n"
                        f"• Status: Continuous microphone listening engaged\n"
                        f"• Wake Phrase: Say 'Hey Jarvis' or 'Hey Lumen' to speak\n"
                        f"• Earcons: Ascending chime on wake, descending chime on sleep\n"
                        f"• Full-Duplex: Active (Sub-50ms Barge-In enabled)\n"
                        f"• Idle Timeout: Auto-sleeps after {self.wake_daemon.idle_timeout_sec}s of silence"
                    )
                except Exception as e:
                    yield f"⚠️ Could not start Wake-Word Daemon: {e}"
            else:
                yield f"🎙️ Wake-Word Daemon is already listening in state: `{self.wake_daemon.state.value}`."
            return

        if trimmed == "/listen-status":
            d = self.wake_daemon
            yield (
                f"🎙️ [Hands-Free Wake-Word Status]\n"
                f"• Daemon State: {d.state.value.upper()}\n"
                f"• Target Wake Word: {d.detector.model_name}\n"
                f"• Detection Threshold: {d.detector.threshold:.2f}\n"
                f"• Total Wake Triggers: {d.triggers_count}\n"
                f"• Uptime: {d.uptime_seconds:.1f}s\n"
                f"• Idle Sleep Timeout: {d.idle_timeout_sec}s"
            )
            return

        if trimmed == "/listen-stop":
            if self.wake_daemon.is_running:
                await self.wake_daemon.stop()
                yield "🎙️ Hands-Free Wake-Word Daemon stopped. Microphone released."
            else:
                yield "Wake-Word Daemon is not currently active."
            return

        if trimmed == "/sentinel":
            if not self.sentinel.is_running:
                try:
                    await self.sentinel.start()
                    yield (
                        f"🛡️ [Autonomous Sentinel Daemon Engaged ('The Watchman')]\n"
                        f"• Status: Background system patrol active (Cadence: {self.sentinel.interval_sec}s)\n"
                        f"• Monitored Vectors: Host Disk, RAM, CPU, Docker Containers, Git Workspace\n"
                        f"• Proactive Defense: Deduplicated alerts will be announced on discovery\n"
                        f"• Vault Archive: Reports stored at vault/sentinel/latest.md"
                    )
                except Exception as e:
                    yield f"⚠️ Could not start Sentinel Daemon: {e}"
            else:
                yield f"🛡️ Sentinel Daemon is already running in state: `{self.sentinel.state.value}`."
            return

        if trimmed == "/sentinel-status":
            yield self.sentinel.get_summary_text()
            return

        if trimmed == "/sentinel-check":
            yield "🛡️ Initiating on-demand full-system diagnostic patrol...\n\n"
            report = await self.sentinel.run_patrol()
            yield self.sentinel.get_summary_text()
            return

        if trimmed == "/sentinel-stop":
            if self.sentinel.is_running:
                await self.sentinel.stop()
                yield "🛡️ Sentinel Daemon has been stopped."
            else:
                yield "Sentinel Daemon is not currently active."
            return

        # 1. Announce any proactive Sentinel alerts
        sentinel_alerts = await self.sentinel.poll_pending_alerts()
        if sentinel_alerts:
            alert_lines = ["🛡️ [Sentinel Proactive Security & Health Warning]"]
            for a in sentinel_alerts:
                badge = "🚨 [CRITICAL]" if a.severity.value == "critical" else (
                    "⚠️ [WARNING]" if a.severity.value == "warning" else "ℹ️ [INFO]"
                )
                alert_lines.append(f"• {badge} {a.title}: {a.details}")
                if a.recommendation:
                    alert_lines.append(f"  👉 Action: {a.recommendation}")
            yield "\n".join(alert_lines) + "\n\n"

        # 2. Announce any completed or failed background tasks
        notifications = await self.poll_notifications()
        if notifications:
            announcements = []
            for n in notifications:
                announcements.append(f"⚡ [System Notice] Background task '{n.name}' ({n.task_id}) reached state: {n.state.value}.")
            yield "\n".join(announcements) + "\n\n"

        # 3. Unified Hybrid Memory Retrieval (BM25 + pgvector + Graph RRF)
        memories = await self.memory.search(
            query=user_text,
            limit=4,
            trace_id=trace_id,
        )

        # 4. Autonomous Agent Loop (ReAct: max 8 steps, context last ~15 turns, direct tool resolution)
        full_assistant_reply = []
        async for token in self.agent_loop.run(
            user_input=user_text,
            retrieved_memories=memories,
            trace_id=trace_id,
        ):
            full_assistant_reply.append(token)
            yield token

        assistant_text = "".join(full_assistant_reply)
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": assistant_text})

        # 6. Asynchronously persist interaction to Memory Engine (L0 Event)
        event_res = await self.memory.save_event(
            source="user_cli",
            payload={"user": user_text, "assistant": assistant_text},
            conversation_id=self.conversation_id,
            idempotency_key=f"turn_{self.conversation_id}_{len(self.history)//2}",
            trace_id=trace_id,
        )

        # 7. Auto-detect explicit user decisions or preferences to store as L1 Facts
        if event_res and any(k in user_text.lower() for k in ["i prefer", "we decided", "use ", "always ", "never "]):
            event_id = event_res.get("id")
            if event_id:
                entity, attribute = classify_preference(user_text)
                await self.memory.save_fact(
                    entity=entity,
                    attribute=attribute,
                    value=user_text,
                    source_event_ids=[event_id],
                    memory_type="decision",
                    epistemic_type="decision",
                    trace_id=trace_id,
                )

        # 7b. Submit extracted relationship triples to Knowledge Graph & Review Queue
        extracted_triples = extract_triples_from_utterance(user_text)
        if event_res and extracted_triples:
            event_id = event_res.get("id")
            for sub, pred, obj in extracted_triples:
                prop_res = await self.memory.propose_triple(
                    subject=sub,
                    predicate=pred,
                    object_=obj,
                    source_event_id=event_id,
                    trace_id=trace_id,
                )
                if prop_res and prop_res.get("action_taken") == "STAGED_FOR_REVIEW":
                    proposal = prop_res.get("proposal", {})
                    yield (
                        f"\n\n⚡ [Review Queue Alert - Two-Man Rule]\n"
                        f"A proposed belief update contradicts an existing active belief:\n"
                        f"• {proposal.get('conflict_reason')}\n"
                        f"To authorize this update, reply with: /approve-proposal {proposal.get('id')}\n"
                        f"To reject and keep prior belief: /reject-proposal {proposal.get('id')}"
                    )

        # 8. Automated Context Compaction (Evacuates turns if high watermark is reached)
        if self.compactor.should_compact(self.history):
            self.history, _ = self.compactor.compact(self.conversation_id, self.history)
