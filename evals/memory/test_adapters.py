"""
Unit tests for benchmark adapters (LongMemEval and LoCoMo).
"""
import pytest
from evals.memory.bench.longmemeval_adapter import adapt_longmemeval_instance
from evals.memory.bench.locomo_adapter import adapt_locomo_instance


def test_longmemeval_adapter():
    sample = {
        "question_id": "q101",
        "category": "fact_recall",
        "history": [
            {"role": "user", "content": "I like dark chocolate.", "timestamp": "-5d"},
            {"role": "assistant", "content": "Noted! Dark chocolate is great.", "timestamp": "-5d"},
        ],
        "question": "What kind of chocolate do I like?",
        "golden_answer": ["dark chocolate"],
    }
    adapted = adapt_longmemeval_instance(sample)
    assert adapted["id"] == "longmemeval-q101"
    assert adapted["category"] == "bench_longmemeval_fact_recall"
    assert len(adapted["setup"]) == 2
    assert adapted["setup"][0]["speaker"] == "user"
    assert adapted["setup"][1]["speaker"] == "assistant"
    assert adapted["query"]["text"] == "What kind of chocolate do I like?"
    assert "dark chocolate" in adapted["expect"]["answer_contains_any"]


def test_locomo_adapter():
    sample = {
        "id": "loco_55",
        "test_type": "update",
        "context_dialogs": [
            {"speaker": "user", "utterance": "I used to code in Ruby.", "time": "-30d"},
            {"speaker": "user", "utterance": "Now I exclusively use Rust.", "time": "-2d"},
        ],
        "query": "What language do I code in now?",
        "target": "Rust",
    }
    adapted = adapt_locomo_instance(sample)
    assert adapted["id"] == "locomo-loco_55"
    assert adapted["category"] == "bench_locomo_update"
    assert len(adapted["setup"]) == 2
    assert adapted["expect"]["answer_contains_any"] == ["Rust"]


def test_locomo_adapter_abstain():
    sample = {
        "id": "loco_99",
        "test_type": "abstain",
        "context_dialogs": [
            {"speaker": "user", "utterance": "I have two dogs.", "time": "-10d"},
        ],
        "query": "What is my mother's maiden name?",
        "target": "",
    }
    adapted = adapt_locomo_instance(sample)
    assert adapted["expect"].get("abstain") is True
