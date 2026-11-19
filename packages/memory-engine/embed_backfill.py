"""Backfill meaning-vectors for existing cards. Run: python3 embed_backfill.py
WHAT: gives every old card a vector so meaning search covers the whole past.
WHY a script, not automatic: embedding 1,000s of cards takes minutes;
  do it once on purpose, then new dumps embed as they arrive.
Needs: Ollama + nomic-embed-text running (see lumen/embed.py), else it
  reports "embedder off" and exits without touching anything.
"""
import sys
sys.path.insert(0, ".")
from lumen import api as L
from lumen import embed as E

if not E.available():
    print("Embedder off — install Ollama + `ollama pull nomic-embed-text`, then rerun.")
    sys.exit(0)

store = L.get_store("lumen.db")
have = {r[0] for r in store.db.execute("SELECT memory_id FROM embeddings")}
rows = store.db.execute("SELECT id, content FROM memories").fetchall()
todo = [(mid, c) for mid, c in rows if mid not in have and (c or "").strip()]
print(f"{len(todo)} cards to embed…")
done = 0
for mid, content in todo:
    try:
        store.save_embedding(mid, E.embed([content])[0])
        done += 1
    except Exception as e:
        print(f"stopped at {done}/{len(todo)} ({type(e).__name__}); rerun resumes.")
        break
print(f"done: {done}/{len(todo)} embedded.")

