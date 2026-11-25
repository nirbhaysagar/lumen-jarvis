"""Eval checks that should not punish a correct update for naming what it replaced."""
from __future__ import annotations
import re


def obsolete_still_asserted(answer: str, excluded: str) -> bool:
    """
    True when `excluded` is still stated as current.
    A mention inside "switched from X to Y" or "cancelled X" is history, not the answer.
    """
    if not answer or not excluded:
        return False
    needle = excluded.lower()
    if needle not in answer.lower():
        return False
    historical = re.compile(
        rf"(?:switched|moved|upgraded|renamed|changed|went|migrated)\s+from\s+.{{0,80}}?{re.escape(needle)}"
        rf"|\bfrom\s+{re.escape(needle)}\s+to\b"
        rf"|\bno longer(?:\s+\w+){{0,4}}\s+{re.escape(needle)}"
        rf"|\b(?:cancelled|canceled|sold|replaced)\s+.{{0,40}}?{re.escape(needle)}"
        rf"|\breplace(?:d)?\s+(?:my\s+)?old\s+{re.escape(needle)}"
        rf"|\bold\s+{re.escape(needle)}\b",
        re.IGNORECASE,
    )
    stripped = historical.sub(" ", answer)
    return needle in stripped.lower()
