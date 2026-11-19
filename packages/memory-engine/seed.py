"""Seed demo story so first open isn't empty. Run: python3 seed.py
WHAT: 3 workspaces × real-ish memories (Orbit / Research / Personal).
WHY: empty app teaches nothing; seeded brain shows value in 10 seconds.
"""
import sys
sys.path.insert(0, ".")
from lumen import api as L

SEED = {
    "orbit-launch": [
        "We should launch Orbit in October. Target is 500 paid users.",
        "Orbit pricing is $29 per month. Developers are price sensitive.",
        "Constraint: no paid ads until product-market fit.",
        "3 users said $29 is acceptable. Two users expect a free tier.",
    ],
    "research": [
        "Research hypothesis: developers pay for context, not generation. Source: 12 user calls.",
        "Finding: context switching costs more than writing code.",
    ],
    "personal": [
        "Preference: minimal UI, no clutter. Goal: ship something small every week.",
    ],
}
for chain, texts in SEED.items():
    for t in texts:
        L.ingest(t, origin="seed", trust=0.7, chain_id=chain)
print("Seeded. Run: python3 server.py → http://localhost:8765")

