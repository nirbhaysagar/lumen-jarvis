"""
Conversation Session & Context Orchestrator.
Glues together Memory Engine retrieval, LiteLLM routing, and True Concurrency Task Supervisor.
"""
from __future__ import annotations
import uuid
from typing import Any, AsyncGenerator, Callable, Coroutine
from ..concurrency.models import TaskCheckpoint
from ..concurrency.supervisor import TaskSupervisor
from ..compactor.compactor import ContextCompactor
from ..vault.vault_sync import ObsidianVaultSync
from ..memory_client.client import MemoryServiceClient
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


class ConversationSession:
    def __init__(
        self,
        conversation_id: str | None = None,
        memory_client: MemoryServiceClient | None = None,
        router: ModelRouter | None = None,
        supervisor: TaskSupervisor | None = None,
        vault_sync: ObsidianVaultSync | None = None,
        compactor: ContextCompactor | None = None,
    ):
        self.conversation_id = conversation_id or f"conv_{uuid.uuid4().hex[:8]}"
        self.memory = memory_client or MemoryServiceClient()
        self.router = router or ModelRouter()
        self.supervisor = supervisor or TaskSupervisor()
        self.vault = vault_sync or ObsidianVaultSync()
        self.compactor = compactor or ContextCompactor(vault_sync=self.vault)
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

        # 8. Automated Context Compaction (Evacuates turns if high watermark is reached)
        if self.compactor.should_compact(self.history):
            self.history, _ = self.compactor.compact(self.conversation_id, self.history)
