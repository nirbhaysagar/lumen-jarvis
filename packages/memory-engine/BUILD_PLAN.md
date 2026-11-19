
### F12. Context API (platform)
- Dev: `POST /memories` (ingest), `GET /context?query=&chain=` (ask), `GET /view?entity=&chain=`, `GET /history`, `POST /enhance`, `POST /chains`. Keys + tenants in Phase 2.
- Why: turns Lumen from app → layer (`App→Agent→Context→Memory→LLM`). B2B revenue lives here.

---

## 3. How we build — phases with exit criteria

### Phase 0 — Core loop ✅ DONE
Files: `lumen/{models,store,extract,canonicalize,link,recall,api}.py`, `demo.py`, 6 tests, `CAPTURE_SPEC.md`.
Exit (met): Sept→Oct same-chain supersede; cross-chain isolation; enhancement boost; `RELEVANT CONTEXT` assembles.

### Phase 1 — Usable brain (next, 2–3 weeks)
1. LLM extractor: wire `extract_with_llm` via LiteLLM router (model-agnostic config), keep rules as fallback. Few-shot per template + Pydantic-style validation + quality score.
2. Embeddings: sqlite-vec hybrid (token-OR + vector), keep interface `search()` stable. Migrate path to pgvector documented.
3. FastAPI wrapper over `api.py` (`/memories /context /view /history /enhance /chains`), file-backed `lumen.db`, request logging.
4. Sheldon read-only v1: static web — chain switcher, search, collections list, memory cards (type/template/conf/eff/★/chain/source), Ask box rendering `context`, Review Queue (confirm `needs_review` → confirm/keep-both), enhance editor.
5. Eval harness `tests/eval.py`: supersede accuracy (Sept→Oct→Nov suite), dedupe rate, chain-isolation rate (cross-chain must never auto-supersede), context relevance (pricing query contains goal+price+feedback).
Exit: 50 real dumps across 3 chains; Ask cites correctly; 5 conflicts resolved via queue; eval all-pass.

### Phase 2 — Connected (1–2 months)
1. Connectors: ChatGPT/Claude export import, Notion/Slack/GitHub pull, file (PDF/md) upload → all via `ingest(..., chain_id)`.
2. Custom GPT Action on `GET /context`; Sheldon "Copy context" button (works everywhere with zero platform risk).
3. Chrome extension ONLY after Phase-1 kill criteria (save-rate>30%, 7-day return>25%): MV3 side panel, multi-fallback selectors + a11y-tree fallback, capture-status indicator, nightly health check, <48h selector-fix playbook. Budget 20% eng.
4. Multi-user: API keys, per-user chains, audit log table.
Exit: daily driver; ChatGPT + Claude both answer from Lumen; extension (if built) capture uptime >95%.

### Phase 3 — Platform / company-brain
Team spaces (shared chains + row-level perms), source-trust policies, SSO/audit/BYOK/GDPR path, IDE + Slack bots, usage-billed API, template sharing (link-based; marketplace deferred).
Exit: external team ships on Context API. Stack = `App → Agent → Context → Memory → LLM → Tools`.

---

## 4. Rules that never break

1. Raw never edited. 2. Canonical transitions only via `canonicalize`. 3. Same-chain auto-supersede only; cross-chain = review. 4. Never silent overwrite. 5. Edges capped (0.6 / 8). 6. `active_chain` authoritative; cross-chain labeled. 7. Lumen never imports Sheldon. 8. Idempotent ingest. 9. Privacy: opt-in capture, export + cascade delete, no training on user data.

## 5. Risks

- Extension DOM brittleness → mitigated by export-first order + fallbacks + status indicator.
- Extraction quality (60% of product) → LLM + templates + field-level review + eval.
- Graph hairball → caps + enhancement weighting.
- Privacy breach → opt-in, minimal retention, encryption path in Phase 3.
- Overbuilding infra → stay SQLite/litellm/static-UI until Phase-1 exit metrics force scale.

## 6. What to run

```bash
python3 demo.py                    # chains + enhancement + isolation proof
python3 -m unittest discover tests -v   # 6 tests
```
Next file to create in Phase 1: `server.py` (FastAPI), `sheldon/index.html`, `tests/eval.py`.

