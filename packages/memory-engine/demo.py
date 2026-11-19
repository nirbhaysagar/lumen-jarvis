"""Demo: chains + enhancement + templates. Run: python3 demo.py"""
from lumen.store import Store
from lumen.models import RawRecord, Chain
from lumen.extract import extract
from lumen.canonicalize import canonicalize
from lumen.link import link
from lumen.recall import assemble_context

store = Store()
store.save_chain(Chain(id="orbit-launch", name="Orbit Launch"))
store.save_chain(Chain(id="research", name="Research"))

# orbit chain: Sept → Oct supersede (same chain → auto)
for text in [
    "We should launch Orbit in September. Target is 500 paid users.",
    "Orbit pricing is $29 per month.",
    "We are actually launching Orbit in October, not September.",
]:
    raw = store.save_raw(RawRecord(content=text, origin="demo"))
    for m in extract(text, source=raw.id, chain_id="orbit-launch"):
        mem, verdict = canonicalize(m, store)
        print(f"[{verdict:>28}] @{mem.chain_id} ({mem.type}/{mem.template}) {mem.content}")
        if not verdict.startswith("duplicate"):
            link(mem, store)

# research chain: same words, different chain → needs_review:cross_chain, no pollution
raw = store.save_raw(RawRecord(content="Launch photonics in September.", origin="demo"))
for m in extract("Launch photonics in September.", source=raw.id, chain_id="research"):
    m.entities = ["Orbit"]  # force entity overlap to prove isolation
    mem, verdict = canonicalize(m, store)
    print(f"[{verdict:>28}] @{mem.chain_id} ({mem.type}/{mem.template}) {mem.content}")

# curate: enhance pricing memory → level 1, boosts Trusted ranking
for m in store.get_active("Orbit", chain_id="orbit-launch"):
    if "pricing" in m.content.lower():
        store.enhance_memory(m.id, "Orbit pricing is $29/mo, validated with 3 users.")
        print(f"\n[enhanced L1] {m.id}: {m.content}")

print("\n--- current_view(Orbit) @orbit-launch ---")
for m in store.get_active("Orbit", chain_id="orbit-launch"):
    print(f"- [{m.type}/{m.template}/L{m.enhancement_level}] {m.display_content} ({m.status})")

print("\n--- ask 'Should we change Orbit pricing?' @orbit-launch ---")
print(assemble_context("Should we change Orbit pricing?", store,
                       active_chain="orbit-launch")["context"])

