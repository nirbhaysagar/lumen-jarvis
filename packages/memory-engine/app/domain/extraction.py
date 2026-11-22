"""
Structured Memory Extraction Engine.
Turns raw captured text into typed, verifiable candidate memories
using cloud API or local LLM, validated strictly against JSON Schema (Draft 2020-12).
"""
from __future__ import annotations
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
import jsonschema
from litellm import acompletion

from app.core.config import settings

logger = logging.getLogger("lumen.memory.extraction")

# Load extraction prompt and JSON schema
PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "extract.md"
SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "schemas" / "extract.json"

_SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8") if PROMPT_PATH.exists() else ""
_SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8")) if SCHEMA_PATH.exists() else {}


_ALLOWED_KINDS = {"person", "org", "project", "tool", "place", "concept", "document", "other"}
_KIND_MAP = {
    "software": "tool",
    "app": "tool",
    "library": "tool",
    "framework": "tool",
    "service": "tool",
    "company": "org",
    "team": "org",
    "group": "org",
}
_ALLOWED_ENTITY_KEYS = {"ref", "name", "kind", "aliases"}
_ALLOWED_MEMORY_KEYS = {
    "ref", "type", "epistemic", "asserted_by", "statement", "subject",
    "predicate", "object", "object_value", "cardinality", "polarity",
    "valid_from", "valid_to", "evidence", "confidence", "importance",
    "change_cue", "relations",
}
_ALLOWED_RELATION_KEYS = {"type", "to"}


def _normalize_parsed(data: Any) -> dict[str, Any]:
    """Coerces LLM quirks (e.g. entity.type -> entity.kind, raw name for subject/object, extra metadata) to conform strictly to schema."""
    if not isinstance(data, dict):
        return {"entities": [], "memories": []}

    # Handle singular aliases: "memory" -> "memories", "entity" -> "entities"
    if ("memories" not in data or not data["memories"]) and "memory" in data:
        mem_val = data.pop("memory")
        if isinstance(mem_val, list):
            data["memories"] = mem_val
        elif isinstance(mem_val, dict):
            data["memories"] = [mem_val]
    elif "memory" in data:
        data.pop("memory", None)

    if ("entities" not in data or not data["entities"]) and "entity" in data:
        ent_val = data.pop("entity")
        if isinstance(ent_val, list):
            data["entities"] = ent_val
        elif isinstance(ent_val, dict):
            data["entities"] = [ent_val]
    elif "entity" in data:
        data.pop("entity", None)

    # Hoist nested entities inside memories up to top-level entities if top-level is empty or missing
    raw_entities = data.get("entities")
    if not isinstance(raw_entities, list):
        raw_entities = []

    raw_memories = data.get("memories")
    if not isinstance(raw_memories, list):
        raw_memories = []

    for m in raw_memories:
        if isinstance(m, dict) and "entities" in m:
            nested_ents = m.pop("entities", None)
            if isinstance(nested_ents, list):
                raw_entities.extend(nested_ents)

    cleaned_entities = []
    for idx, e in enumerate(raw_entities):
        if not isinstance(e, dict):
            continue
        item = dict(e)
        if "ref" not in item or not item["ref"]:
            item["ref"] = f"e{idx+1}"
        if "kind" not in item and "type" in item:
            item["kind"] = item.pop("type")
        kind_val = str(item.get("kind", "other")).lower()
        item["kind"] = _KIND_MAP.get(kind_val, kind_val if kind_val in _ALLOWED_KINDS else "other")
        if "name" in item:
            item["name"] = str(item["name"])
        else:
            item["name"] = f"Entity_{idx+1}"
        if "aliases" in item and not isinstance(item["aliases"], list):
            item["aliases"] = [str(item["aliases"])]
        # Drop unknown keys
        item = {k: v for k, v in item.items() if k in _ALLOWED_ENTITY_KEYS}
        cleaned_entities.append(item)

    # Deduplicate entities by canonical name or ref
    seen_refs = set()
    deduped_entities = []
    for e in cleaned_entities:
        if e["ref"] not in seen_refs:
            seen_refs.add(e["ref"])
            deduped_entities.append(e)
    data["entities"] = deduped_entities

    ent_by_name = {e["name"].lower(): e["ref"] for e in deduped_entities if "name" in e and "ref" in e}
    valid_refs = {e["ref"] for e in deduped_entities if "ref" in e}

    cleaned_memories = []
    for idx, m in enumerate(raw_memories):
        if not isinstance(m, dict):
            continue
        item = dict(m)
        if "ref" not in item or not item["ref"]:
            item["ref"] = f"m{idx+1}"
        if "asserted_by" not in item:
            item["asserted_by"] = "user"
        if "confidence" not in item or not isinstance(item["confidence"], (int, float)):
            item["confidence"] = 0.9
        else:
            item["confidence"] = max(0.0, min(1.0, float(item["confidence"])))
        if "importance" not in item or not isinstance(item["importance"], (int, float)):
            item["importance"] = 0.5
        else:
            item["importance"] = max(0.0, min(1.0, float(item["importance"])))
        if "type" not in item or item["type"] not in ("fact", "decision", "goal", "assumption", "insight", "question", "action", "constraint", "event"):
            item["type"] = "fact"
        if "epistemic" not in item or item["epistemic"] not in ("fact", "belief", "inference", "decision"):
            item["epistemic"] = "belief"
        if "statement" not in item or not str(item["statement"]).strip():
            item["statement"] = f"Fact asserted by {item.get('asserted_by', 'user')}"
        else:
            stmt = str(item["statement"]).strip()
            if len(stmt) < 5:
                stmt = f"{stmt} asserted"
            item["statement"] = stmt[:300]

        # Subject resolution: must match ^e[0-9]+$
        if "subject" in item and item["subject"]:
            sub_val = str(item["subject"]).strip()
            if sub_val in valid_refs:
                item["subject"] = sub_val
            elif sub_val.lower() in ent_by_name:
                item["subject"] = ent_by_name[sub_val.lower()]
            else:
                new_ref = f"e{len(data['entities']) + 1}"
                new_ent = {
                    "ref": new_ref,
                    "name": sub_val.title() if sub_val.lower() != "user" else "User",
                    "kind": "person" if sub_val.lower() == "user" else "concept",
                }
                data["entities"].append(new_ent)
                ent_by_name[sub_val.lower()] = new_ref
                valid_refs.add(new_ref)
                item["subject"] = new_ref
        else:
            item["subject"] = None

        # Object resolution: must match ^e[0-9]+$ or be null
        if "object" in item and item["object"]:
            obj_val = str(item["object"]).strip()
            if obj_val in valid_refs:
                item["object"] = obj_val
            elif obj_val.lower() in ent_by_name:
                item["object"] = ent_by_name[obj_val.lower()]
            else:
                item["object_value"] = obj_val
                item["object"] = None
        else:
            item["object"] = None

        # Predicate normalization: ^[a-z][a-z0-9_]{1,40}$
        if "predicate" in item and item["predicate"]:
            pred_raw = str(item["predicate"]).lower().strip()
            pred_clean = re.sub(r"[^a-z0-9_]+", "_", pred_raw).strip("_")
            if not re.match(r"^[a-z]", pred_clean):
                pred_clean = f"p_{pred_clean}"
            item["predicate"] = pred_clean[:40]

        # Normalize evidence: 1 to 3 items, 3 to 400 chars each
        ev = item.get("evidence", [])
        clean_ev = []
        if isinstance(ev, str) and len(ev.strip()) >= 3:
            clean_ev.append(ev.strip()[:400])
        elif isinstance(ev, list):
            for x in ev:
                xs = str(x).strip()
                if len(xs) >= 3:
                    clean_ev.append(xs[:400])
        if not clean_ev:
            fallback_ev = item.get("statement", "User asserted statement")
            if len(fallback_ev) < 3:
                fallback_ev = "User asserted statement"
            clean_ev.append(fallback_ev[:400])
        item["evidence"] = clean_ev[:3]

        # Ensure cardinality
        if "cardinality" not in item:
            item["cardinality"] = "many"
        elif item["cardinality"] not in ("one", "many"):
            item["cardinality"] = "many"

        # Filter relations
        if "relations" in item and isinstance(item["relations"], list):
            clean_rels = []
            for r in item["relations"]:
                if isinstance(r, dict) and "type" in r and "to" in r:
                    clean_rels.append({k: r[k] for k in _ALLOWED_RELATION_KEYS if k in r})
            item["relations"] = clean_rels

        # Drop unknown keys
        item = {k: v for k, v in item.items() if k in _ALLOWED_MEMORY_KEYS}
        cleaned_memories.append(item)
    data["memories"] = cleaned_memories

    # Clean top-level keys to strictly adhere to additionalProperties: false
    final_dict = {
        "entities": data["entities"],
        "memories": data["memories"],
    }
    return final_dict


class MemoryExtractor:
    def __init__(
        self,
        model: Optional[str] = None,
        api_base: Optional[str] = None,
    ):
        # Model selection: defaults to local Ollama when PREFER_LOCAL_MODELS is true
        if model:
            self.model = model
        elif os.getenv("LUMEN_EXTRACTION_MODEL"):
            self.model = os.getenv("LUMEN_EXTRACTION_MODEL")
        elif settings.PREFER_LOCAL_MODELS:
            self.model = f"ollama/{settings.LUMEN_LLM_MODEL}"
        elif os.getenv("OPENAI_API_KEY"):
            self.model = "gpt-4o-mini"
        elif os.getenv("GROQ_API_KEY"):
            self.model = "groq/qwen/qwen3.8-27b"
        else:
            self.model = f"ollama/{settings.LUMEN_LLM_MODEL}"

        self.api_base = api_base or (settings.OLLAMA_BASE_URL if self.model.startswith("ollama/") else None)

    async def extract(
        self,
        text: str,
        speaker: str = "user",
        source_kind: str = "chat",
        source_title: str = "chat utterance",
        source_author: str = "user",
        captured_at: str = "2026-11-21T10:00:00+05:30",
        tz: str = "+05:30",
        context_memories: str = "none",
    ) -> dict[str, Any]:
        """
        Executes structured extraction. Validates with jsonschema.
        Retries once with validator error if schema is violated.
        """
        system_content = _SYSTEM_PROMPT.format(
            speaker=speaker,
            captured_at=captured_at,
            tz=tz,
        )
        user_content = (
            f"CAPTURED_AT: {captured_at}\n"
            f"SPEAKER: {speaker}\n"
            f"SOURCE: {source_kind} · {source_title} · author={source_author}\n"
            f"CONTEXT (existing memories about the same entities, for name resolution only; do NOT re-extract):\n"
            f"{context_memories}\n"
            f"TEXT:\n"
            f"<<<\n"
            f"{text}\n"
            f">>>\n"
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

        # Call LLM with JSON format
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 450,
            "response_format": {"type": "json_object"},
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base

        raw_resp = None
        for attempt in range(5):
            try:
                raw_resp = await acompletion(**kwargs)
                break
            except Exception as e:
                err_msg = str(e).lower()
                if "rate limit" in err_msg or "429" in err_msg or "tokens per minute" in err_msg:
                    import re, asyncio
                    m_sec = re.search(r"try again in ([\d\.]+)\s*(m?s)", str(e))
                    if m_sec:
                        val = float(m_sec.group(1))
                        wait_sec = (val / 1000.0 if m_sec.group(2) == "ms" else val) + 1.5
                    else:
                        wait_sec = 12.0
                    logger.warning(f"Rate limit encountered on {self.model}. Waiting {wait_sec:.1f}s before retry {attempt+1}/5...")
                    await asyncio.sleep(wait_sec)
                else:
                    logger.error(f"LLM extraction error: {e}")
                    raise

        if not raw_resp:
            return {"entities": [], "memories": []}

        raw_text = raw_resp.choices[0].message.content or "{}"

        # Clean potential markdown fences
        clean_json = raw_text.strip()
        if clean_json.startswith("```"):
            clean_json = clean_json.split("\n", 1)[1]
            if clean_json.endswith("```"):
                clean_json = clean_json.rsplit("```", 1)[0]
            clean_json = clean_json.strip()

        try:
            parsed = json.loads(clean_json)
            parsed = _normalize_parsed(parsed)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse extraction JSON: {e}. Raw: {clean_json[:200]}")
            return {"entities": [], "memories": []}

        # Validate against schema
        try:
            jsonschema.validate(instance=parsed, schema=_SCHEMA)
            return parsed
        except jsonschema.ValidationError as val_err:
            logger.info(f"Schema validation error on first attempt: {val_err.message}. Retrying once...")
            # Retry once with error feedback
            messages.append({"role": "assistant", "content": clean_json})
            messages.append({
                "role": "user",
                "content": f"Your output violated the JSON schema at {list(val_err.path)}: {val_err.message}. Fix and return ONLY valid JSON."
            })
            retry_resp = None
            for r_attempt in range(5):
                try:
                    retry_resp = await acompletion(**kwargs)
                    break
                except Exception as e:
                    err_msg = str(e).lower()
                    if "rate limit" in err_msg or "429" in err_msg or "tokens per minute" in err_msg:
                        import re, asyncio
                        m_sec = re.search(r"try again in ([\d\.]+)\s*(m?s)", str(e))
                        wait_sec = (float(m_sec.group(1)) / 1000.0 if m_sec and m_sec.group(2) == "ms" else (float(m_sec.group(1)) if m_sec else 12.0)) + 1.5
                        await asyncio.sleep(wait_sec)
                    else:
                        break

            if not retry_resp:
                return {"entities": [], "memories": []}

            retry_text = retry_resp.choices[0].message.content or "{}"
            if retry_text.startswith("```"):
                retry_text = retry_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            try:
                re_parsed = json.loads(retry_text)
                re_parsed = _normalize_parsed(re_parsed)
                jsonschema.validate(instance=re_parsed, schema=_SCHEMA)
                return re_parsed
            except Exception as e:
                logger.error(f"Second extraction attempt also failed schema: {e}")
                return {"entities": [], "memories": []}
