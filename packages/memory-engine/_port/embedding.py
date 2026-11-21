from typing import Sequence

from app.config import get_settings

settings = get_settings()

_embedding_model = None


def _get_local_model():
    """Lazy-load the sentence-transformers model (loaded once, cached)."""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer

        _embedding_model = SentenceTransformer(settings.embedding_model)
    return _embedding_model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Generate embeddings for a list of texts.

    Supports:
      - sentence-transformers (local, free) — default
      - ollama (local, free) — set EMBEDDING_PROVIDER=ollama
      - openai (paid) — set EMBEDDING_PROVIDER=openai
    """
    match settings.embedding_provider:
        case "sentence-transformers":
            model = _get_local_model()
            embeddings = model.encode(texts, normalize_embeddings=True)
            return [emb.tolist() for emb in embeddings]

        case "ollama":
            return _embed_via_ollama(texts)

        case "openai":
            return _embed_via_openai(texts)

        case _:
            return _embed_via_ollama(texts)


def embed_text(text: str) -> list[float]:
    """Generate an embedding for a single text."""
    return embed_texts([text])[0]


def _embed_via_ollama(texts: list[str]) -> list[list[float]]:
    """Use Ollama's embedding API (batched /api/embed with a fallback)."""
    import requests

    if not texts:
        return []

    resp = requests.post(
        f"{settings.ollama_base_url}/api/embed",
        json={"model": settings.embedding_model, "input": texts},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    embeddings = data.get("embeddings")
    if embeddings:
        return [list(e) for e in embeddings]

    # Fallback for older Ollama versions without /api/embed.
    results = []
    for text in texts:
        r = requests.post(
            f"{settings.ollama_base_url}/api/embeddings",
            json={"model": settings.embedding_model, "prompt": text},
            timeout=60,
        )
        r.raise_for_status()
        results.append(r.json()["embedding"])
    return results


def _embed_via_openai(texts: list[str]) -> list[list[float]]:
    """Use OpenAI's embedding API."""
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    resp = client.embeddings.create(model=settings.embedding_model, input=texts)
    return [d.embedding for d in resp.data]


async def aembed_texts(texts: list[str]) -> list[list[float]]:
    """Async wrapper — runs sync embedding in a thread (sentence-transformers is sync)."""
    import asyncio

    return await asyncio.to_thread(embed_texts, texts)


async def aembed_text(text: str) -> list[float]:
    import asyncio

    return await asyncio.to_thread(embed_text, text)
