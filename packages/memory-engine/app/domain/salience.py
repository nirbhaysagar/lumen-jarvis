"""
Salience calculation ported from memory-engine.
Computes memory importance by fusing LLM importance with epistemic type weights.
"""
from __future__ import annotations

TYPE_WEIGHTS: dict[str, float] = {
    "decision": 0.95,
    "constraint": 0.90,
    "goal": 0.85,
    "action": 0.80,
    "insight": 0.75,
    "fact": 0.70,
    "assumption": 0.65,
    "event": 0.65,
    "question": 0.60,
}


def compute_salience(
    llm_importance: float,
    memory_type: str,
    confidence: float,
) -> float:
    """
    Computes final salience / importance score:
    importance = round(0.5 * llm_importance + 0.5 * type_weight * confidence, 3)
    Clamped to range [0.1, 1.0].
    """
    type_weight = TYPE_WEIGHTS.get(memory_type.lower(), 0.70)
    conf = max(0.0, min(1.0, confidence))
    llm_imp = max(0.0, min(1.0, llm_importance))
    
    raw = 0.5 * llm_imp + 0.5 * type_weight * conf
    clamped = max(0.1, min(1.0, raw))
    return round(clamped, 3)
