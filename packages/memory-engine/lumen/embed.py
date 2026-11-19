        for w in words:
            h_val = int(hashlib.md5(w.encode("utf-8")).hexdigest()[:8], 16)
            slot = 64 + (h_val % 64)
            vec[slot] += 0.8

            if len(w) >= 3:
                for i in range(len(w) - 2):
                    ng = w[i:i + 3]
                    h_ng = int(hashlib.md5(ng.encode("utf-8")).hexdigest()[:8], 16)
                    slot_ng = 64 + (h_ng % 64)
                    vec[slot_ng] += 0.4

        # 3. L2 Normalization
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [round(x / norm, 5) for x in vec]
        else:
            val = round(1.0 / math.sqrt(128), 5)
            vec = [val] * 128

        out.append(vec)
    return out


def embed(texts: list[str]) -> list[list[float]]:
    """Generates embeddings using the best available backend."""
    if not texts:
        return []

    # 1. Ollama (if reachable)
    try:
        base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        model = os.environ.get("EMBED_MODEL", "nomic-embed-text")
        out = []
        for t in texts:
            data = _post(base + "/api/embeddings", {"model": model, "prompt": t}, timeout=10)
            out.append([float(x) for x in data["embedding"]])
        return out
    except Exception:
        pass

    # 2. OpenAI-compatible /embeddings
    if os.environ.get("EMBED_BASE_URL"):
        try:
            base = os.environ["EMBED_BASE_URL"].rstrip("/")
            headers = {"Content-Type": "application/json"}
            if os.environ.get("EMBED_KEY"):
                headers["Authorization"] = f"Bearer {os.environ['EMBED_KEY']}"
            data = _post(base + "/embeddings", payload={
                "model": os.environ.get("EMBED_MODEL", "text-embedding-3-small"),
                "input": texts
            }, headers=headers, timeout=20)
            return [[float(x) for x in row["embedding"]] for row in data["data"]]
        except Exception:
            pass

    # 3. Built-in Semantic Vector Engine
    return builtin_embed(texts)


def cosine(a: list[float], b: list[float]) -> float:
    """Computes cosine similarity (-1..1), gracefully guarding dimension mismatches."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)

