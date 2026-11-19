        return []

    # Target high-importance memory types: decisions, constraints, goals
    relevant_types = {"decision", "constraint", "goal", "preference"}
    active_mems = store.get_active(chain_id=chain_id)
    candidate_mems = [m for m in active_mems if m.type in relevant_types or (m.level or 0) >= 1]

    keywords = set(git_info.get("keywords", []))
    files = git_info.get("files", [])
    scored: list[tuple[float, Memory]] = []

    for m in candidate_mems:
        score = 0.0
        content_lower = m.content.lower()
        entities_lower = [e.lower() for e in m.entities]

        # 1. Entity matches
        for ent in entities_lower:
            if ent in keywords or any(ent in f.lower() for f in files):
                score += 3.0

        # 2. Keyword matches in card content
        for kw in keywords:
            if kw in content_lower:
                score += 1.0

        # Boost constraints and pinned decisions
        if m.type == "constraint":
            score *= 1.4
        if m.pinned:
            score += 2.0
        if (m.level or 0) > 0:
            score += (m.level or 0) * 0.5

        if score > 0.5:
            scored.append((score, m))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored[:limit]]


