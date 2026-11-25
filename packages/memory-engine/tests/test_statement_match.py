"""Statement matching for same-speaker updates and cross-speaker conflicts."""
from app.domain.match import has_change_cue, pick_predecessor, statements_conflict


def test_change_cues_cover_eval_updates():
    updates = [
        "I switched from VS Code to Neovim as my main editor.",
        "We upgraded Lumen primary database to PostgreSQL 17.",
        "I moved from Mumbai to Bengaluru last month.",
        "I sold my Civic and bought a Hyundai Ioniq 5 electric car.",
        "I got promoted to Staff Engineer at Stripe.",
        "I decided to become strictly vegetarian from this month.",
        "We upgraded all microservices to Python 3.12.",
        "I bought a Google Pixel 9 Pro to replace my old iPhone.",
        "I no longer use Docker Desktop, I switched entirely to OrbStack.",
        "We renamed our repository default branch to main.",
        "I cancelled Cult.fit and joined Gold's Gym.",
        "Project Apollo launched to general availability today.",
        "We migrated our database backups to Cloudflare R2 for zero egress fees.",
        "I changed my default shell to zsh with starship prompt.",
        "I fixed my circadian rhythm and now sleep by 10:30 PM.",
    ]
    for text in updates:
        assert has_change_cue(text), text


def test_predecessor_picks_the_fact_the_change_names():
    pairs = [
        ("I use VS Code for coding.", "I switched from VS Code to Neovim as my main editor."),
        ("Lumen runs on PostgreSQL 16.", "We upgraded Lumen primary database to PostgreSQL 17."),
        ("I live in Mumbai near Bandra.", "I moved from Mumbai to Bengaluru last month."),
        ("I use an iPhone 13 Mini as my personal phone.", "I bought a Google Pixel 9 Pro to replace my old iPhone."),
        ("I use Docker Desktop for local container management.", "I no longer use Docker Desktop, I switched entirely to OrbStack."),
        ("Our default git branch is named master.", "We renamed our repository default branch to main."),
        ("I work out at Cult.fit Indiranagar.", "I cancelled Cult.fit and joined Gold's Gym."),
        ("I use bash as my login shell.", "I changed my default shell to zsh with starship prompt."),
        ("I usually sleep at 1:00 AM.", "I fixed my circadian rhythm and now sleep by 10:30 PM."),
    ]
    for old, new in pairs:
        assert pick_predecessor(new, [old, "I prefer black coffee."]) == 0, new
    # No shared words: only close the prior fact when it is the speaker's only one.
    diet = "I decided to become strictly vegetarian from this month."
    assert pick_predecessor(diet, ["I eat chicken and eggs regularly."]) == 0
    assert pick_predecessor(diet, ["I eat chicken and eggs regularly.", "I prefer black coffee."]) is None
    # A brand-new fact must not close an unrelated memory when others exist.
    assert pick_predecessor("I switched to Rust.", ["I live in Mumbai.", "I prefer black coffee."]) is None


def test_cross_speaker_claims_conflict_without_the_same_predicate():
    pairs = [
        (
            "I decided Lumen must use PostgreSQL 17 for ACID transactions.",
            "Rahul says Lumen should migrate to MongoDB for document flexibility.",
        ),
        (
            "We deploy our primary cluster in AWS Mumbai ap-south-1.",
            "DevOps engineer Karthik argued we should host in Singapore ap-southeast-1.",
        ),
        (
            "I established gRPC as our inter-service communication protocol.",
            "Vikas claims we should use GraphQL instead of gRPC.",
        ),
        (
            "We chose Apache 2.0 license for our open core SDK.",
            "Legal counsel advised switching the SDK to AGPLv3.",
        ),
        (
            "I selected Python for Lumen backend services.",
            "Arjun proposed rewriting the backend in Go for concurrency.",
        ),
    ]
    for own, other in pairs:
        assert statements_conflict(own, other), other
    assert statements_conflict(
        "User uses PostgreSQL 16.",
        "User uses PostgreSQL 16.",
    ) is False
