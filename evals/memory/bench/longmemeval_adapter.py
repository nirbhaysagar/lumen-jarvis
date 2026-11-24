"""
LongMemEval Benchmark Adapter (Phase 0.5 - E4).
Converts LongMemEval long-context conversational test instances into
Lumen declarative YAML eval cases.

Reference: LongMemEval (Long-Term Memory Evaluation for Conversational Agents).
Handles: multi-session dialog history, temporal question answering, and coreference.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
import yaml


def adapt_longmemeval_instance(raw_instance: dict[str, Any]) -> dict[str, Any]:
    """
    Transforms a single LongMemEval JSON instance into a Lumen eval case dictionary.
    
    Expected raw_instance fields:
    - id / question_id: str
    - sessions / history: list of messages [{"role": "user"|"assistant", "content": str, "timestamp": str}]
    - question: str
    - golden_answer: str or list of acceptable strings
    - category: str (e.g., "temporal_reasoning", "fact_recall", "preference")
    """
    case_id = str(raw_instance.get("question_id") or raw_instance.get("id", "longmemeval_001"))
    category = raw_instance.get("category", "fact_retrieval")
    
    sessions = raw_instance.get("sessions") or raw_instance.get("history", [])
    setup_items = []
    for item in sessions:
        role = item.get("role", "user")
        content = item.get("content", "")
        # Filter assistant responses or keep as third party
        speaker = "user" if role == "user" else "assistant"
        setup_items.append({
            "content": content,
            "speaker": speaker,
            "source": {"kind": "chat", "title": "longmemeval session", "author": speaker},
            "occurred_at": item.get("timestamp") or item.get("occurred_at", "-1d"),
        })

    golden = raw_instance.get("golden_answer", [])
    if isinstance(golden, str):
        answer_contains = [golden]
    elif isinstance(golden, list):
        answer_contains = [str(g) for g in golden]
    else:
        answer_contains = []

    return {
        "id": f"longmemeval-{case_id}",
        "category": f"bench_longmemeval_{category}",
        "setup": setup_items,
        "query": {
            "text": raw_instance.get("question", ""),
        },
        "expect": {
            "answer_contains_any": answer_contains,
        },
    }


def convert_longmemeval_file(input_json: Path, output_dir: Path) -> int:
    """Reads a JSON / JSONL LongMemEval benchmark file and outputs YAML test cases."""
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(input_json, "r", encoding="utf-8") as f:
        data = json.load(f) if input_json.suffix == ".json" else [json.loads(line) for line in f if line.strip()]

    for item in data:
        adapted = adapt_longmemeval_instance(item)
        out_file = output_dir / f"{adapted['id']}.yaml"
        with open(out_file, "w", encoding="utf-8") as out:
            yaml.dump(adapted, out, sort_keys=False, allow_unicode=True)
        count += 1

    return count
