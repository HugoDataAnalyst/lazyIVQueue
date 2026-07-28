"""Encounter ID normalization - handles signed/unsigned 64-bit int representation mismatches."""
from typing import Any, Optional

from LazyIVQueue.utils.logger import logger

_UINT64_MASK = (1 << 64) - 1
_MAX_EXACT_FLOAT_INT = 1 << 53  # float64 represents every integer up to here exactly


def normalize_encounter_id(eid: Any) -> Optional[str]:
    """
    Normalize an encounter_id to a canonical string.

    The same real-world encounter_id can arrive as a large positive number in
    one webhook delivery and as a negative signed int64 in another (Go/JSON
    int64 overflow), which would otherwise compare unequal for the same
    encounter. Returns None for missing/zero/empty values.
    """
    if eid is None:
        return None
    if isinstance(eid, float):
        # Below 2^53, float64 represents every integer exactly - safe to
        # convert. Beyond it (where real 64-bit encounter_ids live), whatever
        # produced this float has already lost precision before we saw it;
        # converting anyway risks silently matching the WRONG encounter,
        # since many distinct real ids can round to the same float at that
        # magnitude - worse than just falling back to proximity matching.
        if eid.is_integer() and abs(eid) <= _MAX_EXACT_FLOAT_INT:
            eid = int(eid)
        else:
            logger.warning(
                f"encounter_id arrived as an imprecise float ({eid!r}) - "
                f"treating as missing rather than risk matching the wrong encounter"
            )
            return None
    try:
        val = int(eid) & _UINT64_MASK
        return str(val) if val != 0 else None
    except (ValueError, TypeError):
        s = str(eid).strip().lower()
        return s if s and s != "0" else None
