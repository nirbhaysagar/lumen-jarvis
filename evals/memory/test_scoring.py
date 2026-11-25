from evals.memory.scoring import obsolete_still_asserted


def test_transition_sentence_is_not_a_stale_answer():
    assert obsolete_still_asserted(
        "I switched from VS Code to Neovim as my main editor.",
        "VS Code",
    ) is False
    assert obsolete_still_asserted(
        "I moved from Mumbai to Bengaluru last month.",
        "Mumbai",
    ) is False
    assert obsolete_still_asserted(
        "I cancelled Cult.fit and joined Gold's Gym.",
        "Cult.fit",
    ) is False
    assert obsolete_still_asserted(
        "I no longer use Docker Desktop, I switched entirely to OrbStack.",
        "Docker Desktop",
    ) is False


def test_retired_fact_still_asserted_on_its_own_fails():
    assert obsolete_still_asserted(
        "Lumen uses PostgreSQL 16. We upgraded Lumen primary database to PostgreSQL 17.",
        "PostgreSQL 16",
    ) is True
    assert obsolete_still_asserted("I live in Mumbai near Bandra.", "Mumbai") is True
    assert obsolete_still_asserted("Our default git branch is named master.", "master") is True
