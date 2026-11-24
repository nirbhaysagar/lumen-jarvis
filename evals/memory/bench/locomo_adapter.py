"""
LoCoMo Benchmark Adapter (Phase 0.5 - E4).
Converts LoCoMo (Long-Context Memory Benchmark) evaluation instances into
Lumen declarative YAML eval cases.

Reference: LoCoMo (Evaluating Long-Context LLMs with Multi-Session Conversational Memory).
Handles: multi-turn persona verification, temporal updates, and claim conflicts.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
import yaml


def adapt_locomo_instance(raw_instance: dict[str, Any]) -> dict[str, Any]:
    """
    Transforms a single LoCoMo JSON instance into a Lumen eval case dictionary.

    Expected raw_instance fields:
    - id: str
    - context_dialogs: list of [{"speaker": str, "utterance": str, "time": str}]
    - query: str
    - target: str or list[str]
    - test_type: str (e.g. "update", "conflict", "retrieval", "abstain")
    """
    case_id = str(raw_instance.get("id", "locomo_001"))
    test_type = raw_instance.get("test_type", "retrieval")

    dialogs = raw_instance.get("context_dialogs", [])
    setup_items = []
    for d in dialogs:
        speaker = d.get("speaker", "user")
        utterance = d.get("utterance", "")
        occurred_at = d.get("time", "-1d")
        setup_items.append({
            "content": utterance,
            "speaker": speaker,
            "source": {"kind": "chat", "title": "locomo turn", "author": speaker},
            "occurred_at": occurred_at,
        })

    target = raw_instance.get("target", [])
    if isinstance(target, str):
        target_list = [target]
    elif isinstance(target, list):
        target_list = [str(t) for t in target]
    else:
        target_list = []

    expect: dict[str, Any] = {}
    if test_type == "abstain":
        expect["abstain"] = True
    else:
        expect["answer_contains_any"] = target_list

    return {
        "id": f"locomo-{case_id}",
        "category": f"bench_locomo_{test_type}",
        "setup": setup_items,
        "query": {
            "text": raw_instance.get("query", ""),
        },
        "expect": expect,
    }


def convert_locomo_file(input_json: Path, output_dir: Path) -> int:
    """Reads a JSON / JSONL LoCoMo benchmark file and outputs YAML test cases."""
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(input_json, "r", encoding="utf-8") as f:
        data = json.load(f) if input_json.suffix == ".json" else [json.loads(line) for line in f if line.strip()]

    for item in data:
        adapted = adapt_locomo_instance(item)
        out_file = output_dir / f"{adapted['id']}.yaml"
        with open(out_file, "w", encoding="utf-8") as out:
            yaml.dump(adapted, out, sort_keys=False, allow_unicode=True)
        count += 1

    return count
