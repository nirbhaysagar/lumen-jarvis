    if p == "anthropic":
        yield from _anthropic_stream(messages, model)
    elif p == "gemini":
        yield from _gemini_stream(messages, model)
    elif p == "openai":
        yield from _openai_stream(messages, model)
    else:
        raise RuntimeError("no LLM key (set GEMINI_API_KEY, OPENAI_API_KEY or ANTHROPIC_API_KEY)")


def stream_answer_question(query: str, context: str):
    """Stream prose answer grounded ONLY in the given context."""
    return stream_chat(
        [{"role": "system", "content": (
            "Answer using ONLY the context below. Cite which memory each claim "
            "comes from. If the context lacks the answer, say so plainly. "
            "Reply as short plain paragraphs with a few • bullets. No jargon.")},
         {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"}])


def answer_question(query: str, context: str) -> str:
    """Prose answer grounded ONLY in the given context (no hallucinating)."""
    return chat(
        [{"role": "system", "content": (
            "Answer using ONLY the context below. Cite which memory each claim "
            "comes from. If the context lacks the answer, say so plainly. "
            "Reply as short plain paragraphs with a few • bullets. No jargon.")},
         {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"}],
        json_mode=False)

