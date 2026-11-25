"""
Deterministic matching for updates and cross-speaker conflicts.

Extraction often names the same fact with a different predicate
(`uses_database` vs `migrate_to`). These helpers decide from the
words in the statements, so a changed fact can close the old one
and someone else's claim can be held for review.
"""
from __future__ import annotations
import re

_CHANGE_RE = re.compile(
    r"\b("
    r"switched|moved|upgraded|renamed|replaced|replace[sd]?|"
    r"cancell?ed|no longer|migrated|bought|sold|promoted|changed|"
    r"launched|became|become|instead|from now on|now"
    r")\b",
    re.IGNORECASE,
)

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", re.IGNORECASE)

_STOP = {
    "about", "also", "and", "are", "argued", "as", "at", "be", "been", "but",
    "by", "for", "from", "had", "has", "have", "her", "his", "i", "in", "into",
    "is", "it", "its", "me", "my", "of", "on", "or", "our", "says", "should",
    "that", "the", "their", "them", "they", "this", "to", "use", "used", "uses",
    "using", "was", "we", "were", "with", "you", "your",
}

# Words that name the same kind of choice even when the sentences share nothing else.
_TOPIC_GROUPS = (
    {"mumbai", "singapore", "bengaluru", "bangalore", "delhi", "hsr", "indiranagar",
     "ap-south-1", "ap-southeast-1"},
    {"postgres", "postgresql", "mongodb", "mysql", "sqlite", "database"},
    {"grpc", "graphql"},
    {"apache", "agpl", "agplv3", "mit", "license"},
    {"supabase", "clerk"},
    {"zustand", "redux"},
    {"kubernetes", "k8s", "ecs", "fargate"},
    {"python", "golang", "go"},
    {"neovim", "vscode", "vs-code"},
    {"bash", "zsh"},
    {"s3", "r2", "cloudflare"},
)

_SHORT_KEEP = {"go"}


def has_change_cue(text: str) -> bool:
    return bool(_CHANGE_RE.search(text or ""))


def significant_tokens(text: str) -> set[str]:
    found: set[str] = set()
    for raw in _TOKEN_RE.findall(text or ""):
        token = raw.lower()
        if token in _STOP:
            continue
        if len(token) >= 4 or any(ch.isdigit() for ch in token) or token in _SHORT_KEEP:
            found.add(token)
    return found


def _same_claim(left: str, right: str) -> bool:
    # Near-copies reaffirm. They are not a conflict and not a new value.
    left_tokens = significant_tokens(left)
    right_tokens = significant_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))
    return overlap >= 0.8


def statements_conflict(existing: str, candidate: str) -> bool:
    """True when two speakers are talking about the same choice and disagree."""
    if _same_claim(existing, candidate):
        return False
    if significant_tokens(existing) & significant_tokens(candidate):
        return True
    left = significant_tokens(existing)
    right = significant_tokens(candidate)
    for group in _TOPIC_GROUPS:
        if (left & group) and (right & group) and (left & group) != (right & group):
            return True
    return False


def pick_predecessor(new_statement: str, existing_statements: list[str]) -> int | None:
    """
    Index of the active statement a change-cue should close.
    Shared words win. If this speaker has exactly one active memory, a change
    closes that one. Otherwise an unrelated fact is left alone.
    """
    if not existing_statements or not has_change_cue(new_statement):
        return None
    new_tokens = significant_tokens(new_statement)
    ranked: list[tuple[int, int]] = []
    for idx, old in enumerate(existing_statements):
        shared = len(new_tokens & significant_tokens(old))
        if shared:
            ranked.append((shared, idx))
    if ranked:
        ranked.sort(reverse=True)
        return ranked[0][1]
    if len(existing_statements) == 1:
        return 0
    return None
