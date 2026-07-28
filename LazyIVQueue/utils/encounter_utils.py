"""Encounter ID normalization - handles signed/unsigned 64-bit int representation mismatches."""
from typing import Any, Optional


def normalize_encounter_id(eid: Any) -> Optional[str]:
    """
    Normalize an encounter_id to a canonical string.

    The same real-world encounter_id can arrive as a large positive number in
    one webhook delivery and as a negative signed int64 in another (Go/JSON
    int64 overflow), which would otherwise compare unequal for the same
    encounter. Returns None for missing/zero/empty values.
    """
    if eid is None or eid == "" or eid == 0 or eid == "0":
        return None
    try:
        val = int(eid)
        if val == 0:
            return None
        if val < 0:
            val += (1 << 64)
        return str(val)
    except (ValueError, TypeError):
        s = str(eid).strip()
        return s if s and s != "0" else None
