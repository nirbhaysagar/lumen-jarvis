"""
Conversational Intent Detection & Target Resolution (Milestones F1, F2, D1).
Parses conversational turns for:
1. 'forget': Retraction / deletion of previously stated facts ("forget that", "delete what I said about X").
2. 'undo': Restoring the most recently retracted memory.
3. 'correct': Lineage modification ("actually, it's X", "no, I meant Y").
4. 'directive': Procedural behavioral instructions ("always do X", "never suggest Y", "I prefer short answers").
5. 'none': Regular information query or conversation.
"""
from __future__ import annotations
import json
import logging
import re
from typing import Any, List, Optional, Tuple, Literal
import uuid
import asyncpg
import httpx

from app.core.config import settings
from app.domain.errors import NotFoundError, ConflictError
from app.services.lineage_service import LineageService
from app.services.directives import DirectivesService
from app.adapters.storage.pg_recall import PostgresRecallAdapter

logger = logging.getLogger("lumen.intents")

IntentType = Literal["forget", "undo", "correct", "directive", "none"]


class IntentResult:
    def __init__(
        self,
        intent: IntentType,
        target_query: Optional[str] = None,
        new_value: Optional[str] = None,
        directive_type: Optional[str] = None,
        priority: int = 5,
        confidence: float = 1.0,
        raw_text: str = "",
    ):
        self.intent = intent
        self.target_query = target_query.strip() if target_query else None
        self.new_value = new_value.strip() if new_value else None
        self.directive_type = directive_type
        self.priority = priority
        self.confidence = confidence
        self.raw_text = raw_text

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "target_query": self.target_query,
            "new_value": self.new_value,
            "directive_type": self.directive_type,
            "priority": self.priority,
            "confidence": self.confidence,
            "raw_text": self.raw_text,
        }


class IntentClassifier:
    """Classifies user conversational turns using deterministic regex rules with LLM fallback."""

    # ── Rule-Based Regex Patterns ──
    # Undo patterns
    _RE_UNDO = re.compile(
        r"^(?:please\s+)?(?:undo(?:\s+that|\s+last)?|restore(?:\s+that)?)$",
        re.IGNORECASE,
    )

    # Forget patterns
    _RE_FORGET = re.compile(
        r"^(?:please\s+)?(?:forget|delete|remove|drop|erase)\s+(?:what\s+i\s+said\s+about\s+|that\s+i\s+|about\s+|my\s+)?(.+)$",
        re.IGNORECASE,
    )

    # Simple "forget that" / "delete that"
    _RE_FORGET_THAT = re.compile(
        r"^(?:please\s+)?(?:forget\s+that|delete\s+that|remove\s+that|drop\s+that)$",
        re.IGNORECASE,
    )

    # Correction patterns: "actually it's X", "no, it's X", "correction: X", "I meant X"
    _RE_CORRECT_PREFIX = re.compile(
        r"^(?:actually,?\s*|no,?\s*it'?s\s+|i\s+meant\s+|correction:\s*|correction\s+is\s+|not\s+.+?\s+but\s+)(.+)$",
        re.IGNORECASE,
    )

    # Directives patterns: "remember that I always...", "always do X", "never do Y", "I prefer X"
    _RE_DIRECTIVE = re.compile(
        r"^(?:remember\s+that\s+i\s+(?:always|never|prefer)|always\s+|never\s+|i\s+prefer\s+|from\s+now\s+on\s+|rule:\s*)(.+)$",
        re.IGNORECASE,
    )

    def classify_rules(self, text: str) -> Optional[IntentResult]:
        """Fast-path regex rule evaluation (0ms)."""
        clean = text.strip()
        if not clean:
            return IntentResult(intent="none", raw_text=text)

        # 1. Undo
        if self._RE_UNDO.match(clean):
            return IntentResult(intent="undo", confidence=1.0, raw_text=clean)

        # 2. Simple "forget that" (targets most recent cited memory in session)
        if self._RE_FORGET_THAT.match(clean):
            return IntentResult(intent="forget", target_query=None, confidence=0.95, raw_text=clean)

        # 3. Explicit forget
        m_forget = self._RE_FORGET.match(clean)
        if m_forget:
            target = m_forget.group(1).strip().rstrip(".?!")
            return IntentResult(intent="forget", target_query=target, confidence=0.95, raw_text=clean)

        # 4. Directive
        m_dir = self._RE_DIRECTIVE.match(clean)
        if m_dir:
            body = clean
            lowered = clean.lower()
            d_type = "constraint" if "never" in lowered else ("preference" if "prefer" in lowered else "rule")
            if d_type == "rule":
                d_type = "constraint" if "always" in lowered else "preference"
            return IntentResult(
                intent="directive",
                new_value=body,
                directive_type=d_type,
                priority=7 if "never" in lowered or "always" in lowered else 5,
                confidence=0.90,
                raw_text=clean,
            )

        # 5. Correction
        m_corr = self._RE_CORRECT_PREFIX.match(clean)
        if m_corr:
            new_val = m_corr.group(1).strip().rstrip(".?!")
            # If phrasing is "not X but Y"
            m_not_but = re.match(r"^not\s+(.+?)\s+but\s+(.+)$", clean, re.IGNORECASE)
            target = m_not_but.group(1).strip() if m_not_but else None
            actual_new = m_not_but.group(2).strip() if m_not_but else new_val
            return IntentResult(
                intent="correct",
                target_query=target,
                new_value=actual_new,
                confidence=0.90,
                raw_text=clean,
            )

        return None

    async def classify(self, text: str) -> IntentResult:
        """Classifies intent using rules first, falling back to local LLM for nuanced phrasing."""
        rule_res = self.classify_rules(text)
        if rule_res:
            return rule_res

        # LLM Classifier Fallback
        return await self._classify_llm(text)

    async def _classify_llm(self, text: str) -> IntentResult:
        """Calls local Ollama with JSON schema for nuanced conversational intent detection."""
        system_prompt = (
            "You are an intent classification classifier for the Lumen Memory Engine.\n"
            "Analyze the user's conversational turn and classify into one of:\n"
            "- 'forget': user wants to delete, forget, or erase a memory.\n"
            "- 'undo': user wants to restore/undo the last deletion.\n"
            "- 'correct': user is correcting a previously stated fact (e.g. 'Actually I moved to Bengaluru', 'Nahi, X hai').\n"
            "- 'directive': user is setting a persistent guideline, constraint, or preference (e.g. 'Always answer in bullet points').\n"
            "- 'none': normal question or statement.\n\n"
            "Return JSON matching this schema:\n"
            "{\n"
            "  \"intent\": \"forget\" | \"undo\" | \"correct\" | \"directive\" | \"none\",\n"
            "  \"target_query\": \"topic or text of fact to forget or correct (null if none)\",\n"
            "  \"new_value\": \"new corrected value or directive body (null if none)\",\n"
            "  \"directive_type\": \"preference\" | \"constraint\" | \"goal\" | \"style\" | \"context\" | null,\n"
            "  \"confidence\": 0.0 to 1.0\n"
            "}"
        )

        ollama_url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/generate"
        payload = {
            "model": settings.OLLAMA_MODEL,
            "system": system_prompt,
            "prompt": f"User Turn: {text}",
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.0, "num_predict": 128},
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(ollama_url, json=payload)
                if res.status_code == 200:
                    data = json.loads(res.json().get("response", "{}"))
                    intent = data.get("intent", "none")
                    if intent in ("forget", "undo", "correct", "directive", "none"):
                        return IntentResult(
                            intent=intent,
                            target_query=data.get("target_query"),
                            new_value=data.get("new_value"),
                            directive_type=data.get("directive_type"),
                            confidence=float(data.get("confidence", 0.8)),
                            raw_text=text,
                        )
        except Exception as e:
            logger.warning(f"Ollama intent classification fallback failed: {e}")

        return IntentResult(intent="none", confidence=1.0, raw_text=text)


class TargetResolver:
    """Resolves target memory candidates using recent session memories and 5-channel recall."""

    def __init__(self, pool: asyncpg.Pool, recall_adapter: Optional[PostgresRecallAdapter] = None):
        self.pool = pool
        self.recall_adapter = recall_adapter or PostgresRecallAdapter(pool)

    async def resolve(
        self,
        target_query: Optional[str],
        recent_memory_ids: Optional[List[uuid.UUID]] = None,
        context_slug: str = "default",
    ) -> dict[str, Any]:
        """
        Resolves the exact memory to update/retract:
        1. Checks recent session memories first.
        2. Falls back to hybrid recall with ambiguity checking.
        """
        async with self.pool.acquire() as conn:
            # 1. If target_query is None or "that", use most recent cited memory in session
            if not target_query and recent_memory_ids:
                row = await conn.fetchrow(
                    """
                    SELECT id, lineage_id, statement, version, recorded_at
                    FROM memories
                    WHERE id = $1 AND status = 'active'
                    """,
                    recent_memory_ids[0],
                )
                if row:
                    return {
                        "resolved": True,
                        "memory_id": row["id"],
                        "lineage_id": row["lineage_id"],
                        "statement": row["statement"],
                        "version": row["version"],
                        "recorded_at": row["recorded_at"],
                    }

            # 2. Check if recent_memory_ids has an exact match
            if recent_memory_ids and target_query:
                q_lower = target_query.lower()
                rows = await conn.fetch(
                    """
                    SELECT id, lineage_id, statement, version, recorded_at
                    FROM memories
                    WHERE id = ANY($1) AND status = 'active'
                    """,
                    recent_memory_ids,
                )
                for r in rows:
                    if q_lower in r["statement"].lower():
                        return {
                            "resolved": True,
                            "memory_id": r["id"],
                            "lineage_id": r["lineage_id"],
                            "statement": r["statement"],
                            "version": r["version"],
                            "recorded_at": r["recorded_at"],
                        }

            # 3. Hybrid Recall search
            if not target_query:
                return {"resolved": False, "reason": "no_target_specified"}

            recalled = await self.recall_adapter.recall(
                query=target_query,
                context_slug=context_slug,
                limit=3,
                threshold=0.30,
            )

            if not recalled:
                return {"resolved": False, "reason": "not_found", "query": target_query}

            # Check for ambiguity between top 2 candidates
            if len(recalled) >= 2:
                top_score = recalled[0].get("score", 0.0)
                second_score = recalled[1].get("score", 0.0)
                # If top two candidates are closely matched within 0.04
                if abs(top_score - second_score) < 0.04:
                    return {
                        "resolved": False,
                        "reason": "ambiguous",
                        "candidates": [
                            {
                                "lineage_id": str(r["lineage_id"]),
                                "statement": r["statement"],
                                "score": r.get("score"),
                            }
                            for r in recalled[:2]
                        ],
                    }

            # Single clear candidate
            top = recalled[0]
            return {
                "resolved": True,
                "memory_id": top["id"],
                "lineage_id": top["lineage_id"],
                "statement": top["statement"],
                "version": top.get("version", 1),
                "recorded_at": top.get("recorded_at"),
            }


class IntentExecutor:
    """Executes the resolved conversational intent and formats human-friendly confirmation replies."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self.classifier = IntentClassifier()
        self.target_resolver = TargetResolver(pool)
        self.lineage_svc = LineageService(pool)
        self.directives_svc = DirectivesService(pool)

    async def process_turn(
        self,
        user_text: str,
        recent_memory_ids: Optional[List[uuid.UUID]] = None,
        context_slug: str = "default",
        actor: str = "user",
    ) -> dict[str, Any]:
        """End-to-end processing of a user turn for memory commands."""
        intent_res = await self.classifier.classify(user_text)

        if intent_res.intent == "none":
            return {"handled": False, "intent": "none"}

        # ── 1. Undo Intent ──
        if intent_res.intent == "undo":
            # Find most recently retracted memory in this context
            async with self.pool.acquire() as conn:
                ctx_row = await conn.fetchrow("SELECT id FROM contexts WHERE slug = $1", context_slug)
                ctx_id = ctx_row["id"] if ctx_row else None
                last_retracted = await conn.fetchrow(
                    """
                    SELECT lineage_id, statement FROM memories
                    WHERE status = 'retracted' AND context_id = $1
                    ORDER BY superseded_at DESC NULLS LAST, recorded_at DESC
                    LIMIT 1
                    """,
                    ctx_id,
                )
            if not last_retracted:
                return {
                    "handled": True,
                    "intent": "undo",
                    "status": "error",
                    "reply": "No recently retracted memories found to restore.",
                }

            lid = last_retracted["lineage_id"]
            undo_res = await self.lineage_svc.undo_retract(lid, reason="conversational undo", actor=actor)
            reply = f"Restored memory: '{undo_res['statement']}' (v{undo_res['version']})."
            return {
                "handled": True,
                "intent": "undo",
                "status": "success",
                "result": undo_res,
                "reply": reply,
            }

        # ── 2. Directive Intent ──
        if intent_res.intent == "directive":
            body = intent_res.new_value or user_text
            title = body[:48] + ("..." if len(body) > 48 else "")
            d_type = intent_res.directive_type or "preference"
            priority = intent_res.priority or 5

            async with self.pool.acquire() as conn:
                ctx_row = await conn.fetchrow("SELECT id FROM contexts WHERE slug = $1", context_slug)
                ctx_id = ctx_row["id"] if ctx_row else None

            try:
                d_res = await self.directives_svc.create_directive(
                    title=title,
                    body=body,
                    directive_type=d_type,
                    priority=priority,
                    context_id=ctx_id,
                    actor=actor,
                )
                reply = f"Recorded directive: **'{title}'** (type: {d_type}, priority: {priority})."
                return {
                    "handled": True,
                    "intent": "directive",
                    "status": "success",
                    "result": d_res,
                    "reply": reply,
                }
            except Exception as e:
                return {
                    "handled": True,
                    "intent": "directive",
                    "status": "error",
                    "reply": f"Could not record directive: {e}",
                }

        # ── 3. Forget or Correct: Resolve Target Memory ──
        target_info = await self.target_resolver.resolve(
            target_query=intent_res.target_query,
            recent_memory_ids=recent_memory_ids,
            context_slug=context_slug,
        )

        if not target_info.get("resolved"):
            reason = target_info.get("reason")
            if reason == "ambiguous":
                cands = target_info.get("candidates", [])
                lines = [f"{idx+1}. \"{c['statement']}\"" for idx, c in enumerate(cands)]
                cand_str = "\n".join(lines)
                reply = (
                    f"I found multiple memories matching '{intent_res.target_query}':\n"
                    f"{cand_str}\n"
                    "Which one would you like me to update?"
                )
                return {
                    "handled": True,
                    "intent": intent_res.intent,
                    "status": "clarification_needed",
                    "candidates": cands,
                    "reply": reply,
                }
            else:
                reply = f"I couldn't find an active memory matching '{intent_res.target_query or 'that'}'. No changes were made."
                return {
                    "handled": True,
                    "intent": intent_res.intent,
                    "status": "not_found",
                    "reply": reply,
                }

        lineage_id = target_info["lineage_id"]
        old_stmt = target_info["statement"]

        # ── 4. Execute Forget ──
        if intent_res.intent == "forget":
            retract_res = await self.lineage_svc.retract(
                lineage_id=lineage_id,
                reason=f"conversational forget: '{user_text}'",
                actor=actor,
            )
            rec_date = target_info.get("recorded_at")
            date_str = f" (recorded {rec_date.strftime('%d %b %Y')})" if rec_date else ""
            reply = f"Forgotten: '{old_stmt}'{date_str}. (Say 'undo' if you change your mind)."
            return {
                "handled": True,
                "intent": "forget",
                "status": "success",
                "result": retract_res,
                "reply": reply,
            }

        # ── 5. Execute Correction ──
        if intent_res.intent == "correct":
            new_val = intent_res.new_value
            if not new_val:
                return {
                    "handled": True,
                    "intent": "correct",
                    "status": "error",
                    "reply": "What should the updated value be?",
                }

            corr_res = await self.lineage_svc.correct(
                lineage_id=lineage_id,
                new_statement=new_val,
                reason=f"conversational correction: '{user_text}'",
                actor=actor,
                base_version=target_info.get("version"),
            )
            reply = (
                f"Updated: '{old_stmt}' → **'{new_val}'** (v{corr_res['new_version']}). "
                "The previous value is preserved in history."
            )
            return {
                "handled": True,
                "intent": "correct",
                "status": "success",
                "result": corr_res,
                "reply": reply,
            }

        return {"handled": False, "intent": "none"}
