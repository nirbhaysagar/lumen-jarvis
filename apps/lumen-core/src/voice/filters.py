import re
from typing import Set

WHISPER_HALLUCINATIONS: Set[str] = {
    "",
    ".",
    "..",
    "...",
    "....",
    ",",
    "!",
    "?",
    "-",
    "thank you",
    "thank you.",
    "thank you very much",
    "thank you very much.",
    "thanks for watching",
    "thanks for watching.",
    "thank you for watching",
    "thank you for watching.",
    "you",
    "rational",
    "rational.",
    "beams",
    "beams.",
    "beam",
    "subscribe",
    "subscribe.",
    "like and subscribe",
    "like and subscribe.",
    "subtitles by",
    "subtitles",
    "translated by",
    "english",
    "listening",
    "silence",
    "music",
    "applause",
    "[music]",
    "[applause]",
    "[silence]",
    "(music)",
    "(applause)",
    "shh",
    "shhh",
    "okay",
    "ok",
}


def is_whisper_hallucination(text: str) -> bool:
    """
    Detects known Whisper STT phantom hallucinations on ambient noise or silence.
    Filters out punctuation-only artifacts, credit tags, and single-word phantom echoes.
    """
    if not text:
        return True
    raw_lower = text.strip().lower()
    cleaned = re.sub(r"[^\w\s]", "", raw_lower).strip()

    # Reject if empty or only punctuation / whitespace or single char
    if not cleaned or len(cleaned) < 2:
        return True

    # Exact match against known hallucination phrases
    if raw_lower in WHISPER_HALLUCINATIONS or cleaned in WHISPER_HALLUCINATIONS:
        return True

    # Prefix match for common subtitle hallucinations
    for prefix in ("subtitles by", "translated by", "thank you for watching", "thanks for watching"):
        if cleaned.startswith(prefix):
            return True

    # Repeated "thank you" loops (e.g. "thank you thank you")
    if not cleaned.replace("thank you", "").strip():
        return True

    # Repetitive single-word loops like "you you you"
    words = cleaned.split()
    if len(words) >= 2 and all(w == words[0] for w in words):
        return True

    return False


def is_sleep_command(text: str) -> bool:
    """
    Detects natural human speech commands instructing the assistant to stop or sleep.
    Bypasses LLM generation and directly triggers dormant listening.
    """
    if not text:
        return False
    raw_lower = text.strip().lower()
    cleaned = re.sub(r"[^\w\s]", "", raw_lower).strip()
    words = cleaned.split()
    if not words:
        return False

    # 1. Reject questions (e.g. "Are you sleep?", "Did you sleep?", "Why sleep?")
    if raw_lower.endswith("?"):
        return False

    question_prefixes = (
        "are you",
        "are we",
        "is it",
        "did you",
        "do you",
        "can you",
        "could you",
        "would you",
        "will you",
        "why",
        "how",
        "what",
        "where",
        "who",
        "which",
    )
    if any(cleaned.startswith(qp) for qp in question_prefixes):
        return False

    # 2. Standalone stop/sleep commands
    stop_phrases = {
        "stop",
        "okay stop",
        "ok stop",
        "please stop",
        "stop please",
        "shut up",
        "go to sleep",
        "sleep",
        "be quiet",
        "quiet",
        "silence",
        "cancel",
        "stop listening",
        "quit",
        "exit",
        "pause",
        "bye",
        "goodbye",
        "bye jarvis",
        "goodbye jarvis",
        "bye lumen",
        "goodbye lumen",
        "good night",
        "see you later",
    }
    if cleaned in stop_phrases:
        return True

    # 3. Key phrase matching
    if any(phrase in cleaned for phrase in ("go to sleep", "shut up", "stop listening", "be quiet", "good night", "see you later")):
        return True

    # 4. Short utterance (<= 4 words) containing imperative stop or sleep
    # e.g. "Okay stop. Hijabay stop.", "Hey Jarvis stop", "Lumen sleep", "Bye, Jarvis"
    imperative_triggers = ("stop", "sleep", "cancel", "quiet", "bye", "goodbye")
    if len(words) <= 4 and any(w in imperative_triggers for w in words):
        # Exclude questions starting with pronouns/question markers
        if words[0] in ("you", "are", "do", "did", "can", "why", "how", "what", "where"):
            return False
        return True

    return False
