"""
Cross-Encoder Reranker Adapter for Lumen Memory Engine (Phase 0.6 - R1).
Supports:
1. FastEmbed ONNX Cross-Encoder (BAAI/bge-reranker-base, Xenova/ms-marco-MiniLM-L-6-v2, BAAI/bge-reranker-v2-m3)
2. Fast fallback scoring via token-overlap & RapidFuzz alignment when models are not cached or in light testing.
All models run 100% locally with permissive licenses (MIT, Apache-2.0).
"""
from __future__ import annotations
import logging
import math
from typing import Sequence, Optional
from rapidfuzz import fuzz

logger = logging.getLogger("lumen.reranker")


class CrossEncoderReranker:
    def __init__(
        self,
        model_name: str = "Xenova/ms-marco-MiniLM-L-6-v2",
        enabled: bool = True,
    ):
        self.model_name = model_name
        self.enabled = enabled
        self._model = None
        self._init_attempted = False

    def _get_model(self):
        if not self.enabled:
            return None
        if not self._init_attempted:
            self._init_attempted = True
            try:
                from fastembed.rerank.cross_encoder import TextCrossEncoder
                self._model = TextCrossEncoder(model_name=self.model_name)
                logger.info(f"Loaded CrossEncoderReranker model: {self.model_name}")
            except Exception as e:
                logger.warning(
                    f"FastEmbed cross-encoder unavailable ({e}). Using fast lexical-semantic fallback."
                )
                self._model = None
        return self._model

    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        """
        Computes relevance scores in [0.0, 1.0] for (query, document) pairs.
        Returns list of floats corresponding to input documents order.
        """
        if not documents:
            return []
        if not query.strip():
            return [0.0] * len(documents)

        model = self._get_model()
        if model is not None:
            try:
                scores = list(model.rerank(query, list(documents)))
                # Sigmoid normalize if raw logits returned
                norm_scores = []
                for s in scores:
                    if 0.0 <= s <= 1.0:
                        norm_scores.append(float(s))
                    else:
                        # Standard logistic sigmoid for logits
                        sig = 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, float(s)))))
                        norm_scores.append(round(sig, 4))
                return norm_scores
            except Exception as e:
                logger.debug(f"Cross-encoder inference error ({e}), falling back to lexical-semantic scoring.")

        # Fallback: High-precision fuzzy token & partial ratio matching
        fallback_scores = []
        q_lower = query.lower()
        q_tokens = set(q_lower.split())
        for doc in documents:
            d_lower = doc.lower()
            d_tokens = set(d_lower.split())
            
            # Token overlap Jaccard
            inter = len(q_tokens & d_tokens)
            union = len(q_tokens | d_tokens)
            jaccard = inter / union if union > 0 else 0.0

            # Fuzzy token set ratio
            set_ratio = fuzz.token_set_ratio(q_lower, d_lower) / 100.0
            part_ratio = fuzz.partial_ratio(q_lower, d_lower) / 100.0

            # Weighted combination
            score = 0.4 * set_ratio + 0.3 * part_ratio + 0.3 * jaccard
            fallback_scores.append(round(score, 4))

        return fallback_scores
