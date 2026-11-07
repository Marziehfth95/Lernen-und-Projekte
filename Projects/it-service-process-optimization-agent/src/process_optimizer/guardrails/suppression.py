"""Small-group suppression for aggregate results (k-threshold, item-level, complementary).

A count from 1 to k-1 anywhere in an item suppresses the whole item, so a cohort
comparison or numerator/denominator pair cannot disclose a small complement. Filters
only select precomputed rows and suppression runs before any result limit, so the value
of a visible row never depends on the query and overlapping or complementary queries
can only ever combine counts of at least k.
"""

from __future__ import annotations

import math
from typing import Any

_COUNT_KEYS = frozenset({"numerator", "denominator", "count"})


def is_count_key(key: str) -> bool:
    return key in _COUNT_KEYS or key.endswith("_count")


def _small(value: Any, threshold: int) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return True
    return 0 < value < threshold


def contains_small_group(value: Any, threshold: int) -> bool:
    """Return True if any count (or numerator/denominator complement) is in 1..k-1."""
    if isinstance(value, dict):
        numerator = value.get("numerator")
        denominator = value.get("denominator")
        if (
            isinstance(numerator, int | float)
            and isinstance(denominator, int | float)
            and not isinstance(numerator, bool)
            and not isinstance(denominator, bool)
            and _small(denominator - numerator, threshold)
        ):
            return True
        for key, nested in value.items():
            if is_count_key(str(key)) and _small(nested, threshold):
                return True
            if isinstance(nested, dict | list | tuple) and contains_small_group(
                nested, threshold
            ):
                return True
        return False
    if isinstance(value, list | tuple):
        return any(contains_small_group(item, threshold) for item in value)
    return False


def suppress_small_groups(
    items: list[dict[str, Any]], threshold: int
) -> tuple[list[dict[str, Any]], int]:
    """Drop every item that discloses a small group; return kept items and dropped count."""
    kept = [item for item in items if not contains_small_group(item, threshold)]
    return kept, len(items) - len(kept)
