"""
Conversation Session & Context Orchestrator.
Glues together Memory Engine retrieval, LiteLLM routing, and True Concurrency Task Supervisor.
"""
from __future__ import annotations
import re
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
    ):
        self.conversation_id = conversation_id or f"conv_{uuid.uuid4().hex[:8]}"
        self.memory = memory_client or MemoryServiceClient()
        self.router = router or ModelRouter()
        self.supervisor = supervisor or TaskSupervisor()
        self.vault = vault_sync or ObsidianVaultSync()
        self.compactor = compactor or ContextCompactor(vault_sync=self.vault)
        self.runner = runner or SandboxedRunner()
        self.approvals = approvals or ApprovalManager()
        self.history: list[dict[str, str]] = []

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
                f"• Canonical Memory Notes: {stats['memories']}\n"
                f"• Compacted Episodes: {stats['episodes']}\n"
                f"• Total Markdown Cards: {stats['total_notes']}\n\n"
                f"You can open '{self.vault.vault_path}' in Obsidian to view your visual knowledge graph!"
            )
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

        # 1. Announce any completed or failed background tasks
        notifications = await self.poll_notifications()
        if notifications:
            announcements = []
            for n in notifications:
                announcements.append(f"⚡ [System Notice] Background task '{n.name}' ({n.task_id}) reached state: {n.state.value}.")
            yield "\n".join(announcements) + "\n\n"

        # 2. Parallel Memory Retrieval (Tuned for pgvector HNSW search)
        memories = await self.memory.search_memories(
            query=user_text,
            limit=4,
            threshold=0.01,
            trace_id=trace_id,
        )

        # 3. Append User Message
        self.history.append({"role": "user", "content": user_text})

        # 4. Assemble Context Window
        full_context = format_context_with_memories(memories, self.history)

        # 5. Stream LLM Tokens
        full_assistant_reply = []
        async for token in self.router.stream_completion(full_context):
            full_assistant_reply.append(token)
            yield token

        assistant_text = "".join(full_assistant_reply)
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
                fact_res = await self.memory.save_fact(
                    entity=entity,
                    attribute=attribute,
                    value=user_text,
                    source_event_ids=[event_id],
                    memory_type="decision",
                    epistemic_type="decision",
                    trace_id=trace_id,
                )
                if fact_res:
                    self.vault.export_fact({
                        "id": fact_res.get("id"),
                        "entity": entity,
                        "attribute": attribute,
                        "value": user_text,
                        "memory_type": "decision",
                        "epistemic_type": "decision",
                    })

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
