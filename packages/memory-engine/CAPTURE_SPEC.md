# Capture Spec — how context gets INTO Lumen (Front Side)

Order matters. Build cheap + robust first, brittle automation last.

## Phase 1 (now): manual + export — zero maintenance
1. **Paste box** (`api.ingest(text, chain_id)`) — already works. Default chain `default`.
2. **ChatGPT / Claude export import** — user uploads `conversations.json` → split per conversation → `ingest(each, origin="chatgpt-export", chain_id=<topic>)`.
3. **Copy-button snippet** — Sheldon "Copy context" puts `RELEVANT CONTEXT` on clipboard for pasting into any model.

Kill criteria before Phase 2: save-rate >30%, 7-day return >25% on 100 dumps.

## Phase 2: browser capture (Context Graph Platform Front Side)
- Chrome MV3 side panel (380px), `chrome.storage.local` queue, background worker → `POST /memories`.
- Content scripts per tool with **multiple fallback selectors** (never one brittle selector):
  - ChatGPT: `data-message-author-role`, fallback `article`, fallback accessibility tree
  - Claude: `data-testid`, fallback `[data-is-streaming]`, fallback a11y tree
- **Capture-status indicator** (green/amber/red) so user knows when DOM broke.
- Health check: nightly job compares captured chars vs expected pattern; alert on drop >20%.
- Budget: ~20% eng time permanently. Playbook: detect → fallback to a11y parse → ship selector fix <48h.

## Privacy (trust product — one breach ends it)
- Capture is opt-in per tool + per chain. Research chain never auto-shares to coding chain.
- Raw preserved but exportable + deletable (`delete_memory` cascades). No training on user data.
- Phase 3: E2E encryption, SOC2/GDPR path, BYOK for enterprise.

## Mapping to code
- `chain_id` on ingest = Chain Layer isolation (no cross-contamination).
- `template` on extract = structured nodes (research/decision/planning/client).
- `enhancement_level` + `enhanced_content` = Back-Side curation; boosts `effective_confidence` and search rank.
- `assemble_context(..., active_chain=)` = in-chain authoritative + max 2 cross-chain related.

