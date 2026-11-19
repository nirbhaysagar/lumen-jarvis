"""
Context Compaction Engine.
Monitors conversation context watermarks, distills historical turns into structured episodes,
and syncs episodic memory to the Obsidian Knowledge Vault.
"""
from __future__ import annotations
import logging
from typing import Sequence
from ..vault.vault_sync import ObsidianVaultSync

logger = logging.getLogger("lumen.compactor")


class ContextCompactor:
    def __init__(
        self,
        high_watermark_turns: int = 6,
        low_watermark_turns: int = 2,
        vault_sync: ObsidianVaultSync | None = None,
    ) -> None:
        self.high_watermark_turns = high_watermark_turns
        self.low_watermark_turns = low_watermark_turns
        self.vault = vault_sync or ObsidianVaultSync()

    def should_compact(self, history: Sequence[dict[str, str]]) -> bool:
        """Determines if the active turn history exceeds the high watermark."""
        return len(history) >= self.high_watermark_turns

    def compact(
        self,
        session_id: str,
        history: list[dict[str, str]],
        force: bool = False,
    ) -> tuple[list[dict[str, str]], str]:
        """
        Executes semantic context compaction:
        1. Slices off the oldest turns, leaving `low_watermark_turns` recent turns.
        2. Distills the evacuated turns into an episodic narrative.
        3. Exports the episode to the Obsidian vault (`vault/episodes/`).
        4. Injects a compact summary anchor and returns the pruned history.
        """
        if not force and not self.should_compact(history):
            return history, ""

        if len(history) <= self.low_watermark_turns:
            return history, ""

        cutoff = len(history) - self.low_watermark_turns
        evacuated = history[:cutoff]
        recent = history[cutoff:]

        # Distill key topics and actions from evacuated turns
        summary_points = []
        for turn in evacuated:
            role = turn.get("role", "")
            content = turn.get("content", "").strip()
            if role == "user":
                summary_points.append(f"User inquired or stated: '{content[:60]}...'")
            elif role == "assistant":
                summary_points.append(f"JARVIS responded regarding: '{content[:60]}...'")

        summary = "Previously in this session: " + "; ".join(summary_points[:4])

        # Write episode to Obsidian vault
        try:
            self.vault.export_episode(
                session_id=session_id,
                summary=summary,
                turn_count=len(evacuated),
                evacuated_turns=evacuated,
            )
            logger.info(f"Compacted {len(evacuated)} turns into Obsidian episode for session {session_id}.")
        except Exception as e:
            logger.error(f"Failed to export episode to Obsidian: {e}")

        # Assemble compact history
        compacted_history: list[dict[str, str]] = [
            {"role": "system", "content": f"[PRIOR CONTEXT COMPACTED: {summary}]"}
        ]
        compacted_history.extend(recent)

        return compacted_history, summary
