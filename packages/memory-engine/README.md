# Lumen — Memory Engine (v0.2, 5-layer)

> Memory → Context → Intelligence. LLM generates intelligence, Lumen supplies continuity.

This folder is the **Lumen core only**. Sheldon (UI) sits on top later.

## Quickstart (no deps, stdlib only)

```bash
python3 demo.py
python3 -m unittest discover tests -v
```

## 5 layers (L0–L4)

```
L0 Raw          raw table (immutable receipts)
L1 Canonical    memories (typed, templated, deduped via chain-scoped ingest_key)
L2 Relational   chains (isolation) + edges (supersedes/contradicts/related, capped)
L3 Temporal     status active|superseded|archived + valid_from/to + history
L4 Context      assemble_context(query, active_chain) → Recent/Trusted/Related/Cross-chain/Conflicts/Review
```

Plus Back-Side curation: `enhance_memory()` → enhanced_content + level 0→1→2,
boosts `effective_confidence` and search rank. Field review when conf < 0.7 & L0.

## Structure

```
lumen/
  models.py        — Memory(+chain_id, template, enhanced_content, enhancement_level), Chain, Edge
  store.py         — SQLite: raw + chains + memories + edges; chain-scoped search w/ enhancement weighting
  extract.py       — rules + seed templates (general/research/engineering_decision/planning/client); LLM hook
  canonicalize.py  — authority policy; auto-supersede ONLY same-chain, else needs_review:cross_chain
  link.py          — thresholded, capped linking
  recall.py        — chain-isolated context assembly
  api.py           — ingest/enhance/ask_context/current_view/history (+chain_id)
demo.py            — chains + enhancement + cross-chain isolation demo
CAPTURE_SPEC.md    — Front-Side capture plan (export first, extension later)
BUILD_PLAN.md      — full plan: features, backend, why
```

