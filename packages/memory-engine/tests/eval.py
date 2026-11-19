"""MVP acceptance eval. Run: python3 tests/eval.py
WHAT: 4 checks from MVP.md that must pass before we call it shippable.
WHY a separate eval (not just unit tests): unit tests check pieces;
  eval checks the USER promise end-to-end on a fresh brain.
"""
import sys
sys.path.insert(0, ".")
from lumen.store import Store
from lumen.models import RawRecord, Chain
from lumen.extract import extract
from lumen.canonicalize import canonicalize
from lumen.link import link
from lumen.recall import assemble_context


def ingest(s: Store, text: str, chain: str):
    out = []
    raw = s.save_raw(RawRecord(content=text, origin="eval"))
    for m in extract(text, source=raw.id, chain_id=chain):
        mem, verdict = canonicalize(m, s)
        if verdict in ("new",) or verdict.startswith("supersedes"):
            link(mem, s)
        out.append((mem, verdict))
    return out


results = []
def check(name, cond, detail=""):
    results.append((name, cond))
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))

# 1. Temporal: Sept→Oct→Nov, only Nov active
s = Store()
for t in ["Launch Orbit in September.", "Launch Orbit in October.", "Launch Orbit in November."]:
    for m in extract(t, source="x"):
        m.entities, m.type = ["Orbit"], "decision"
        canonicalize(m, s)
active = [m.content for m in s.get_active("Orbit")]
check("temporal-only-latest-active", any("November" in c for c in active) and not any("September" in c for c in active), str(active))

# 2. Chain isolation: same words in other chain must NOT auto-supersede
s2 = Store()
s2.save_chain(Chain(id="a")); s2.save_chain(Chain(id="b"))
ingest(s2, "Launch Orbit in September.", "a")
r = ingest(s2, "Launch Orbit in October.", "b")
check("chain-isolation", all(not v.startswith("supersedes") for _, v in r), str([v for _, v in r]))
check("chain-a-keeps-september", any("September" in m.content for m in s2.get_active("Orbit", chain_id="a")))

# 3. Dedupe
s3 = Store()
ingest(s3, "Orbit costs $29.", "default")
r2 = ingest(s3, "Orbit costs $29.", "default")
check("dedupe", all(v == "duplicate" for _, v in r2))

# 4. Pricing relevance: Ask must contain price + goal + feedback-ish
s4 = Store()
for t in ["Orbit pricing is $29 per month.", "Target is 500 paid users.", "3 users said $29 is acceptable."]:
    ingest(s4, t, "orbit")
ctx = assemble_context("Should we change Orbit pricing?", s4, active_chain="orbit")["context"]
check("pricing-relevance", all(k in ctx for k in ["$29", "500", "acceptable"]), ctx[:200].replace("\n", " "))

print(f"\n{sum(1 for _, c in results if c)}/{len(results)} passed")
sys.exit(0 if all(c for _, c in results) else 1)

