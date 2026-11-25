"""
Present-tense fact for a transition sentence.

Recall asks "where do I live?" The saved sentence is often the change
itself ("I moved from Mumbai to Bengaluru"). The cross-encoder scores
that narrative as irrelevant even when the embedding agrees. The value
below is copied out of the sentence; the citation quote stays the
user's words.
"""
from __future__ import annotations
import re

_PLACE = r"([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,3})"

_MOVED = re.compile(
    rf"\b(?:moved|relocated)\s+from\s+.+?\s+to\s+{_PLACE}",
)

_SHELL = re.compile(
    r"\b(?:changed|switched)\s+(?:my\s+)?(?:default\s+|login\s+)?shell\s+to\s+([A-Za-z][\w.-]*)",
    re.IGNORECASE,
)

_BOUGHT = re.compile(
    r"\bbought\s+(a|an|the)?\s*(.+?)\s+to\s+replace\b",
    re.IGNORECASE,
)

_SWITCHED = re.compile(
    r"\bswitched(?:\s+entirely)?\s+to\s+([A-Za-z][\w.-]*)",
    re.IGNORECASE,
)

_AS_MY = re.compile(r"\bas my ([^.]+)", re.IGNORECASE)
_FOR = re.compile(r"\bfor ([^.]+)", re.IGNORECASE)


def _in_text(value: str, text: str) -> bool:
    return bool(value) and value.lower() in text.lower()


def current_statement(text: str, predecessor: str | None = None) -> str | None:
    """
    The fact that is true after `text`, or None when the sentence should
    be stored as written. The new value is always a substring of `text`.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    pred = predecessor or ""

    moved = _MOVED.search(raw)
    if moved:
        place = moved.group(1).strip()
        if _in_text(place, raw):
            return f"I live in {place}."

    shell = _SHELL.search(raw)
    if shell:
        name = shell.group(1)
        if _in_text(name, raw):
            return f"My login shell is {name}."

    bought = _BOUGHT.search(raw)
    slot = _AS_MY.search(pred) if pred else None
    if bought and slot:
        article = (bought.group(1) or "").lower()
        item = bought.group(2).strip(" .")
        if _in_text(item, raw):
            art = f"{article} " if article else ""
            return f"I use {art}{item} as my {slot.group(1).strip()}."

    switched = _SWITCHED.search(raw)
    purpose = _FOR.search(pred) if pred else None
    if switched and purpose:
        tool = switched.group(1).strip(" .")
        use = purpose.group(1).strip()
        if _in_text(tool, raw) and tool.lower() not in use.lower():
            return f"I use {tool} for {use}."

    return None
