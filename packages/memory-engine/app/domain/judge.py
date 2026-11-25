"""
Local LLM Judge for Nightly Consolidation (Milestone C1).
Provides JSON-structured judgments for memory deduplication, contradiction detection,
relation typing, and cluster synthesis via Ollama (qwen2.5:7b-instruct).
"""
from __future__ import annotations
import json
import logging
from typing import Any, Protocol
from app.core.config import settings
from app.adapters.llm.ollama_client import OllamaClient

logger = logging.getLogger("lumen.domain.judge")


class MemoryJudgePort(Protocol):
    async def judge_pair(self, stmt_a: str, stmt_b: str, quote_a: str = "", quote_b: str = "") -> dict[str, Any]:
        """Returns {'verdict': 'same'|'different'|'contradicts', 'confidence': float, 'explanation': str}."""
        ...

    async def synthesize_cluster(self, statements: list[str], entity_names: list[str]) -> dict[str, Any]:
        """Returns {'title': str, 'summary': str, 'key_points': list[str], 'contradictions': list[str]}."""
        ...


class LocalMemoryJudge:
    """Ollama-backed local judge using qwen2.5:7b-instruct."""

    def __init__(self, ollama_client: OllamaClient | None = None, model: str | None = None):
        self.client = ollama_client or OllamaClient(base_url=settings.OLLAMA_BASE_URL)
        self.model = model or settings.LUMEN_LLM_MODEL

    async def judge_pair(
        self,
        stmt_a: str,
        stmt_b: str,
        quote_a: str = "",
        quote_b: str = "",
    ) -> dict[str, Any]:
        prompt = (
            "Compare these two memory assertions about the same subject.\n"
            f"Memory A: {stmt_a}\n"
            f"Quote A: {quote_a}\n"
            f"Memory B: {stmt_b}\n"
            f"Quote B: {quote_b}\n\n"
            'Respond ONLY with JSON: {"verdict": "same" | "different" | "contradicts", "confidence": 0.0..1.0, "explanation": "..."}'
        )
        try:
            resp = await self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a strict semantic memory judge. Output JSON only."},
                    {"role": "user", "content": prompt},
                ],
                format="json",
                options={"temperature": 0.0, "num_predict": 160},
            )
            data = json.loads(resp.get("content", "{}"))
            verdict = data.get("verdict", "different")
            if verdict not in ("same", "different", "contradicts"):
                verdict = "different"
            return {
                "verdict": verdict,
                "confidence": float(data.get("confidence", 0.5)),
                "explanation": str(data.get("explanation", "")),
            }
        except Exception as e:
            logger.warning(f"LocalMemoryJudge.judge_pair fallback: {e}")
            return {"verdict": "different", "confidence": 0.0, "explanation": f"error: {e}"}

    async def synthesize_cluster(self, statements: list[str], entity_names: list[str]) -> dict[str, Any]:
        bullets = "\n".join(f"- {s}" for s in statements[:15])
        prompt = (
            f"Entities: {', '.join(entity_names)}\n"
            f"Memories:\n{bullets}\n\n"
            'Synthesize into JSON: {"title": "...", "summary": "...", "key_points": ["..."], "contradictions": []}'
        )
        try:
            resp = await self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a concise memory synthesizer. Output JSON only."},
                    {"role": "user", "content": prompt},
                ],
                format="json",
                options={"temperature": 0.2, "num_predict": 350},
            )
            data = json.loads(resp.get("content", "{}"))
            return {
                "title": str(data.get("title") or f"Summary: {', '.join(entity_names[:2])}"),
                "summary": str(data.get("summary") or " ".join(statements[:3])),
                "key_points": list(data.get("key_points") or []),
                "contradictions": list(data.get("contradictions") or []),
            }
        except Exception as e:
            logger.warning(f"LocalMemoryJudge.synthesize_cluster fallback: {e}")
            return {
                "title": f"Cluster Summary: {', '.join(entity_names[:2]) or 'General'}",
                "summary": " ".join(statements[:3]),
                "key_points": statements[:3],
                "contradictions": [],
            }
