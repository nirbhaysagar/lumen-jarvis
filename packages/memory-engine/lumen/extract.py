        if re.search(spec["hint"], t):
            return name
    return "general"


def extract_entities(text: str) -> list[str]:
    words = re.findall(r"\b[A-Z][a-zA-Z0-9]+\b", text)
    seen, out = set(), []
    for w in words:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out[:8]


def extract(text: str, source: str = "manual",
            chain_id: str = "default") -> list[Memory]:
    """Split on sentences, one memory per sentence. Deterministic, testable."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    out = []
    for s in sents:
        mtype = guess_type(s)
        out.append(Memory(type=mtype, content=s,
                          entities=extract_entities(s), source=source,
                          chain_id=chain_id,
                          template=guess_template(s, mtype)))
    return out


def extract_with_llm(text: str, llm_fn, source: str = "manual",
                     chain_id: str = "default",
