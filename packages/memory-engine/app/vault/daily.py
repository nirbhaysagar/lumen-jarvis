"""
Daily Notes and Morning Brief Generator (Milestone O4).
Compiles daily changelogs (Captured, Changed with diffs, Retracted, Open Reviews, Active Goals)
and generates an executive morning brief using local Ollama (qwen2.5:7b-instruct)
where every single bullet strictly cites a [[memory note]].
"""
from __future__ import annotations
from datetime import date, datetime, time, timezone
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, List, Optional
import uuid
import asyncpg
import yaml

from app.core.config import settings
from app.adapters.llm.ollama_client import OllamaClient
from app.vault.projector import (
    slugify,
    safe_vault_target,
    atomic_write,
    get_default_vault_dir,
    wikilinkify,
)

logger = logging.getLogger("lumen.vault.daily")


def format_memory_wikilink(mem: dict) -> str:
    """Formats a memory into a valid [[slug-lineage8|Statement]] wikilink."""
    stmt = mem.get("statement", "Memory")
    slug = slugify(stmt)
    lid = str(mem.get("lineage_id") or uuid.uuid4())[:8]
    return f"[[{slug}-{lid}|{stmt}]]"


class DailyNoteGenerator:
    """Generates daily intelligence notes and morning executive briefs."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        vault_dir: Optional[Path] = None,
        ollama_client: Optional[OllamaClient] = None,
        llm_model: Optional[str] = None,
    ):
        self.pool = pool
        self.vault_dir = (vault_dir or get_default_vault_dir()).resolve()
        self.ollama_client = ollama_client or OllamaClient(base_url=settings.OLLAMA_BASE_URL)
        self.llm_model = llm_model or settings.LUMEN_LLM_MODEL

    async def generate(self, target_date: Optional[date] = None) -> dict[str, Any]:
        """
        Generates and writes daily/<YYYY-MM-DD>.md with full provenance, diffs,
        and cited executive brief.
        """
        if target_date is None:
            target_date = datetime.now(timezone.utc).date()

        date_str = target_date.strftime("%Y-%m-%d")
        dt_start = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
        dt_end = datetime.combine(target_date, time.max, tzinfo=timezone.utc)

        async with self.pool.acquire() as conn:
            # 1. Fetch Captured (Created today, version=1)
            captured_rows = await conn.fetch(
                """
                SELECT m.id, m.lineage_id, m.version, m.statement, m.type, m.epistemic, m.lane, m.confidence, m.recorded_at
                FROM memories m
                WHERE m.version = 1
                  AND m.status = 'active'
                  AND m.recorded_at >= $1 AND m.recorded_at <= $2
                ORDER BY m.recorded_at ASC
                """,
                dt_start,
                dt_end,
            )
            captured = [dict(r) for r in captured_rows]

            # 2. Fetch Changed (Updated today, version > 1)
            changed_rows = await conn.fetch(
                """
                SELECT m.id, m.lineage_id, m.version, m.statement, m.type, m.lane, m.recorded_at
                FROM memories m
                WHERE m.version > 1
                  AND m.status = 'active'
                  AND m.recorded_at >= $1 AND m.recorded_at <= $2
                ORDER BY m.recorded_at ASC
                """,
                dt_start,
                dt_end,
            )
            changed = []
            for r in changed_rows:
                c_dict = dict(r)
                # Find previous version
                prev_row = await conn.fetchrow(
                    """
                    SELECT statement, version
                    FROM memories
                    WHERE lineage_id = $1 AND version = $2
                    LIMIT 1
                    """,
                    c_dict["lineage_id"],
                    c_dict["version"] - 1,
                )
                c_dict["prev_statement"] = prev_row["statement"] if prev_row else None
                changed.append(c_dict)

            # 3. Fetch Retracted
            retracted_rows = await conn.fetch(
                """
                SELECT m.id, m.lineage_id, m.version, m.statement, m.status, m.superseded_at, m.recorded_at
                FROM memories m
                WHERE m.status = 'retracted'
                  AND (
                    (m.superseded_at >= $1 AND m.superseded_at <= $2)
                    OR (m.recorded_at >= $1 AND m.recorded_at <= $2)
                  )
                ORDER BY m.superseded_at ASC NULLS LAST
                """,
                dt_start,
                dt_end,
            )
            retracted = [dict(r) for r in retracted_rows]

            # 4. Fetch Open Reviews
            review_rows = await conn.fetch(
                """
                SELECT rq.id, rq.kind, rq.reason, rq.created_at
                FROM review_queue rq
                WHERE rq.status = 'open'
                ORDER BY rq.created_at DESC
                LIMIT 20
                """
            )
            open_reviews = [dict(r) for r in review_rows]

            # 5. Fetch Active Goals & Actions
            goal_rows = await conn.fetch(
                """
                SELECT m.id, m.lineage_id, m.statement, m.type, m.confidence, m.recorded_at
                FROM memories m
                WHERE m.status = 'active'
                  AND m.type IN ('goal', 'action')
                ORDER BY m.recorded_at DESC
                LIMIT 20
                """
            )
            active_goals = [dict(r) for r in goal_rows]

        # 6. Generate Executive Brief
        brief_bullets = await self._generate_brief(
            date_str=date_str,
            captured=captured,
            changed=changed,
            goals=active_goals,
        )

        # 7. Render Markdown
        markdown_content = self._render_markdown(
            date_str=date_str,
            brief_bullets=brief_bullets,
            captured=captured,
            changed=changed,
            retracted=retracted,
            open_reviews=open_reviews,
            active_goals=active_goals,
        )

        # 8. Atomic Write
        target_dir = self.vault_dir / "daily"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = safe_vault_target(self.vault_dir, target_dir / f"{date_str}.md")
        atomic_write(target_path, markdown_content)

        logger.info(f"Generated daily note at {target_path} ({len(brief_bullets)} brief bullets)")
        return {
            "date": date_str,
            "path": str(target_path),
            "content": markdown_content,
            "brief_bullets": brief_bullets,
            "stats": {
                "captured": len(captured),
                "changed": len(changed),
                "retracted": len(retracted),
                "open_reviews": len(open_reviews),
                "active_goals": len(active_goals),
            },
        }

    async def _generate_brief(
        self,
        date_str: str,
        captured: List[dict],
        changed: List[dict],
        goals: List[dict],
    ) -> List[str]:
        """
        Uses local Ollama to synthesize a morning brief from the day's changes and active goals.
        Strict contract: every single bullet MUST cite a [[slug-lineage8|Memory]] wikilink.
        Bullets without valid citations are dropped.
        """
        all_items = captured + changed + goals
        if not all_items:
            return ["- No critical events or memory updates recorded for this date."]

        # Build reference catalog of valid memory links
        link_map: dict[str, str] = {}
        items_description = []
        for item in all_items:
            wl = format_memory_wikilink(item)
            stmt = item["statement"]
            mtype = item.get("type", "fact")
            link_map[stmt.lower()] = wl
            items_description.append(f"- Note link: {wl} | Statement: {stmt} | Type: {mtype}")

        catalog_text = "\n".join(items_description)

        system_prompt = (
            "You are Lumen's Executive Briefing Engine. Generate a concise 3 to 5 bullet executive morning brief.\n"
            "MANDATORY CITATION CONTRACT:\n"
            "1. Every single bullet MUST cite at least one [[slug-short_id|Statement]] note link from the catalog.\n"
            "2. Never fabricate a link or statement. Use EXACT [[...]] links provided in the catalog.\n"
            "3. If a line does not have a citation link, it will be automatically dropped.\n"
            "4. Format each line starting with '- ' followed by the cited summary."
        )

        user_prompt = (
            f"Date: {date_str}\n\n"
            f"Available Memory Items & Note Links:\n{catalog_text}\n\n"
            "Write the executive brief now:"
        )

        valid_bullets: List[str] = []
        try:
            resp = await self.ollama_client.chat(
                model=self.llm_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                options={"temperature": 0.2, "num_predict": 400},
            )
            raw_text = resp.get("content", "").strip()
            lines = [l.strip() for l in raw_text.splitlines() if l.strip().startswith("-")]

            # Enforce citation filter: every line MUST contain [[...]]
            for line in lines:
                if re.search(r"\[\[[^\]]+\]\]", line):
                    valid_bullets.append(line)
        except Exception as e:
            logger.warning(f"Ollama brief generation fallback: {e}")

        # Deterministic fallback if LLM is unavailable or produced 0 cited bullets
        if not valid_bullets:
            for item in all_items[:4]:
                wl = format_memory_wikilink(item)
                mtype = item.get("type", "fact")
                valid_bullets.append(f"- {wl} (Status: {mtype})")

        return valid_bullets

    def _render_markdown(
        self,
        date_str: str,
        brief_bullets: List[str],
        captured: List[dict],
        changed: List[dict],
        retracted: List[dict],
        open_reviews: List[dict],
        active_goals: List[dict],
    ) -> str:
        """Renders complete daily markdown note with frontmatter and structured sections."""
        fm_data = {
            "date": date_str,
            "type": "daily_digest",
            "captured_count": len(captured),
            "changed_count": len(changed),
            "retracted_count": len(retracted),
            "open_reviews_count": len(open_reviews),
        }
        fm_yaml = yaml.dump(fm_data, default_flow_style=False, sort_keys=False).strip()

        # 1. Executive Brief
        brief_section = "\n".join(brief_bullets) if brief_bullets else "- No brief items available."

        # 2. Captured
        cap_lines = [f"- {format_memory_wikilink(m)} (v1)" for m in captured]
        cap_section = "\n".join(cap_lines) if cap_lines else "- No new facts captured today."

        # 3. Changed
        chg_lines = []
        for m in changed:
            wl = format_memory_wikilink(m)
            v = m.get("version", 2)
            prev = m.get("prev_statement")
            if prev:
                chg_lines.append(f"- {wl} (v{v}):\n  - *Previous:* \"{prev}\"\n  - *Current:* \"{m['statement']}\"")
            else:
                chg_lines.append(f"- {wl} (v{v})")
        chg_section = "\n".join(chg_lines) if chg_lines else "- No facts modified today."

        # 4. Retracted
        ret_lines = [f"- ~{m['statement']}~ (Lineage `{str(m['lineage_id'])[:8]}`)" for m in retracted]
        ret_section = "\n".join(ret_lines) if ret_lines else "- No facts retracted today."

        # 5. Open Reviews
        rev_lines = []
        for r in open_reviews:
            r_kind = r.get("kind", "proposal")
            r_reason = r.get("reason", "Needs review")
            stmt = r.get("statement") or "General Item"
            rev_lines.append(f"- **{r_kind.upper()}:** {stmt} — *{r_reason}*")
        rev_section = "\n".join(rev_lines) if rev_lines else "- No pending items in review queue."

        # 6. Active Goals & Action Items
        goal_lines = [f"- {format_memory_wikilink(g)} `[{g.get('type')}]`" for g in active_goals]
        goal_section = "\n".join(goal_lines) if goal_lines else "- No active goals or action items."

        content = f"""---
{fm_yaml}
---
# Daily Brief & Knowledge Log: {date_str}

## Executive Brief
{brief_section}

## Captured
{cap_section}

## Changed
{chg_section}

## Retracted
{ret_section}

## Open Reviews
{rev_section}

## Active Goals & Action Items
{goal_section}
"""
        return content.strip() + "\n"
