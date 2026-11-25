"""Current-fact sentences copied out of a transition."""
from app.domain.current_fact import current_statement


def test_move_becomes_where_i_live():
    text = "I moved from Mumbai to Bengaluru last month."
    assert current_statement(text, "I live in Mumbai near Bandra.") == "I live in Bengaluru."
    assert "Mumbai" not in current_statement(text)


def test_phone_keeps_the_role_from_the_previous_fact():
    text = "I bought a Google Pixel 9 Pro to replace my old iPhone."
    old = "I use an iPhone 13 Mini as my personal phone."
    assert current_statement(text, old) == "I use a Google Pixel 9 Pro as my personal phone."
    assert current_statement(text) is None


def test_switch_keeps_the_purpose():
    text = "I no longer use Docker Desktop, I switched entirely to OrbStack."
    old = "I use Docker Desktop for local container management."
    assert current_statement(text, old) == "I use OrbStack for local container management."


def test_shell_change_names_the_current_shell():
    text = "I changed my default shell to zsh with starship prompt."
    assert current_statement(text, "I use bash as my login shell.") == "My login shell is zsh."
    assert "bash" not in current_statement(text)


def test_sentences_that_already_answer_are_left_alone():
    left_alone = [
        "I switched from VS Code to Neovim as my main editor.",
        "We upgraded Lumen primary database to PostgreSQL 17.",
        "I cancelled Cult.fit and joined Gold's Gym.",
        "I decided to become strictly vegetarian from this month.",
        "I sold my Civic and bought a Hyundai Ioniq 5 electric car.",
    ]
    for text in left_alone:
        assert current_statement(text, "I use something for work.") is None, text
