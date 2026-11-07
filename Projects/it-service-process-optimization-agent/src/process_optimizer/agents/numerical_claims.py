"""Deterministic extraction and verification of numerical claims in recommendation prose.

Free-text numbers are checked against the numeric values of the cited Evidence Contract
findings (and a cited simulation), plus a closed set of arithmetic derivations: ratios,
percentages, percentage-point and absolute differences. Matching respects the claimed
unit dimension and the precision the claim was stated with. Model text is never rewritten.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import permutations
from typing import Any, Literal

type Dimension = Literal["time", "count", "ratio", "fraction"]
type ClaimKind = Literal["percent", "multiplier", "time", "count", "plain"]

_SECONDS_PER_UNIT = (
    (re.compile(r"(?:seconds?|secs?)\b"), 1.0),
    (re.compile(r"(?:minutes?|mins?)\b"), 60.0),
    (re.compile(r"(?:hours?|hrs?)\b"), 3600.0),
    (re.compile(r"days?\b"), 86400.0),
    (re.compile(r"weeks?\b"), 604800.0),
    (re.compile(r"months?\b"), 2629800.0),
)
_PERCENT_UNIT = re.compile(r"(?:%|percent\b|per\s+cent\b|percentage\s+points?\b|pp\b)")
_MULTIPLIER_UNIT = re.compile(
    r"(?:x\b|×|-?fold\b|times\s+(?:as|longer|shorter|more|less|higher|lower|faster|slower"
    r"|greater|larger|smaller|the)\b)"
)
_COUNT_UNIT = re.compile(
    r"(?:incidents?|events?|event[\s-]transitions?|transitions?|cases?|tickets?|visits?"
    r"|hand-?offs?|returns?|groups?)\b"
)
_MAGNITUDE = re.compile(r"^\s+(thousand|million|billion)\b")
_MAGNITUDE_FACTOR = {"thousand": 1e3, "million": 1e6, "billion": 1e9}

# Digits glued to an identifier, a decimal point or another number are not claims.
_NUMBER = re.compile(r"(?<![\w.,])\d(?:[\d,.]*\d)?")
_VALID_GROUPING = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
_EUROPEAN_GROUPING = re.compile(r"^\d{1,3}(?:\.\d{3})+$")
_MASKED_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?"),
    re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b"),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),
)
_IDENTIFIER_PREFIX = re.compile(r"[A-Za-z_]-$")
_LABEL_PREFIX = re.compile(
    r"(?:\b(?:v|version|release|phase|step|stage|tier|level|priority|option|scenario"
    r"|section|figure|table|appendix|item|iteration|round|wave|sprint|rule|no\.|number)"
    r"\s*|#\s*)$",
    re.IGNORECASE,
)
_ORDINAL_SUFFIX = re.compile(r"^(?:st|nd|rd|th)\b")
_PLANNING_SUFFIX = re.compile(r"^\s*(?:later|earlier|apart|after|before|from\s+now)\b")
_PLANNING_PREFIX = re.compile(
    r"\b(?:within|after|every|each|per|next|for|over|during|first|last)\s+(?:the\s+)?"
    r"(?:first\s+|next\s+|last\s+)?$",
    re.IGNORECASE,
)
_HEDGE_PREFIX = re.compile(
    r"(?:\b(?:approximately|approx\.|about|around|roughly|nearly|almost|circa|ca\.|close\s+to"
    r"|some)\s*|[~≈]\s*)$",
    re.IGNORECASE,
)
_RANGE_JOIN = re.compile(r"^\s*(?:-|–|—|to)\s*(?=\d)")
_NUMBER_WORDS = (
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty"
    r"|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)"
)
_SPELLED_QUANTITY = re.compile(
    rf"\b{_NUMBER_WORDS}(?:[\s-]{_NUMBER_WORDS})?\s+(?:percent|per\s+cent"
    r"|percentage\s+points?|times\s+(?:as|longer|shorter|more|less|higher|lower|faster"
    r"|slower))\b",
    re.IGNORECASE,
)
_WORD_MULTIPLIERS = {
    "twice": 2.0,
    "doubled": 2.0,
    "tripled": 3.0,
    "quadrupled": 4.0,
    "halved": 0.5,
    "twofold": 2.0,
    "threefold": 3.0,
    "fourfold": 4.0,
    "fivefold": 5.0,
    "tenfold": 10.0,
}
_WORD_MULTIPLIER = re.compile(rf"\b(?:{'|'.join(_WORD_MULTIPLIERS)})\b", re.IGNORECASE)

_COHORT_PREFIX = re.compile(r"^(?:with_return_|without_return_|with_|without_)")
_COUNT_PAIRS = ({"numerator", "denominator"}, {"observation_count", "denominator"})
_SMALL_INTEGER_LIMIT = 100
_YEAR_RANGE = range(1900, 2101)
_MAX_RELATIVE_ROUNDING = 0.05
_HEDGE_RELATIVE_TOLERANCE = 0.01


@dataclass(frozen=True)
class NumericalClaim:
    """One numeric expression found in model-generated prose."""

    text: str
    value: float
    kind: ClaimKind
    precision: float
    hedged: bool
    material: bool
    ambiguous: bool = False


@dataclass(frozen=True)
class SupportedValues:
    """Evidence values and closed-form derivations a free-text claim may cite."""

    absolute: dict[Dimension, tuple[float, ...]]
    ratios: tuple[float, ...]
    percents: tuple[float, ...]

    def supports(self, claim: NumericalClaim) -> bool:
        if claim.kind == "percent":
            candidates: Iterable[float] = self.percents
        elif claim.kind == "multiplier":
            candidates = self.ratios
        elif claim.kind in ("time", "count"):
            candidates = self.absolute.get(claim.kind, ())
        else:
            candidates = (
                *(value for values in self.absolute.values() for value in values),
                *self.ratios,
            )
        return any(_within_tolerance(claim, expected) for expected in candidates)


def extract_numerical_claims(text: str) -> tuple[NumericalClaim, ...]:
    """Find numeric expressions; dates, identifiers, versions and ordinals are skipped."""
    masked = text
    for pattern in _MASKED_PATTERNS:
        masked = pattern.sub(lambda match: " " * len(match.group()), masked)
    claims = [_ambiguous(match.group()) for match in _SPELLED_QUANTITY.finditer(masked)]
    for match in _WORD_MULTIPLIER.finditer(masked):
        hedged = bool(_HEDGE_PREFIX.search(masked[: match.start()]))
        value = _WORD_MULTIPLIERS[match.group().lower()]
        claims.append(NumericalClaim(match.group(), value, "multiplier", 0.0, hedged, True))
    for match in _NUMBER.finditer(masked):
        claim = _classify(masked, match)
        if claim is not None:
            claims.append(claim)
    return tuple(claims)


def build_supported_values(
    findings: Iterable[dict[str, Any]],
    simulations: Iterable[dict[str, Any]] = (),
) -> SupportedValues:
    """Collect cited evidence values and their admissible arithmetic derivations.

    Ratios, percentages and differences are derived only for declared comparison pairs
    inside one finding: cohort counterparts (``with_return_x`` / ``without_return_x``), a
    statistic and its baseline counterpart, and count/denominator. Pairing arbitrary
    values would make almost any percentage reachable. Simulation values are accepted as
    reported, because the engine already states its own derived shares.
    """
    derived: list[SupportedValues] = []
    for finding in findings:
        entries: list[tuple[str, Dimension, float]] = [
            (key, "count", value) for key, value in _numeric_leaves(finding.get("counts", {}), "")
        ]
        entries.extend(
            (key, _dimension(key, value), value)
            for key, value in _numeric_leaves(finding.get("measured_statistics", {}), "")
        )
        comparison = finding.get("baseline_or_comparison") or {}
        entries.extend(
            (key, _dimension(key, value), value)
            for key, value in _numeric_leaves(comparison.get("statistics", {}), "")
        )
        base = _empty_base()
        for _key, dimension, value in entries:
            base[dimension].add(value)
        pairs = [
            (first_dimension, first, second)
            for (first_key, first_dimension, first), (
                second_key,
                second_dimension,
                second,
            ) in permutations(entries, 2)
            if first_dimension == second_dimension and _comparison_pair(first_key, second_key)
        ]
        derived.append(_derive(base, pairs))
    for simulation in simulations:
        base = _empty_base()
        for key in ("baseline", "scenarios"):
            for path, value in _numeric_leaves(simulation.get(key, {}), key):
                base[_dimension(path, value)].add(value)
        derived.append(_derive(base, ()))
    return _merge(derived)


def supported_values_for_fact(field_path: str, value: float) -> SupportedValues:
    """Admissible renderings of one structured fact, used to check its own statement."""
    base = _empty_base()
    base[_dimension(field_path, value)].add(float(value))
    return _derive(base, ())


def _comparison_pair(first_key: str, second_key: str) -> bool:
    if first_key == second_key:
        return False
    if {first_key, second_key} in _COUNT_PAIRS:
        return True
    return _comparison_stem(first_key) == _comparison_stem(second_key)


def _comparison_stem(key: str) -> str:
    return _COHORT_PREFIX.sub("", key).removesuffix("_median")


def _derive(
    base: dict[Dimension, set[float]],
    pairs: Iterable[tuple[Dimension, float, float]],
) -> SupportedValues:
    absolute = {dimension: set(values) for dimension, values in base.items()}
    ratios = set(base["ratio"])
    percents = {value * 100 for value in base["fraction"]}
    percents.update(value * 100 for value in base["ratio"])
    percents.update(abs(value - 1) * 100 for value in base["ratio"])
    for dimension, first, second in pairs:
        if dimension != "fraction":
            absolute[dimension].add(abs(first - second))
        if second == 0:
            continue
        ratios.add(first / second)
        percents.add(first / second * 100)
        percents.add(abs(first - second) / second * 100)
        if dimension == "fraction":
            percents.add(abs(first - second) * 100)
    return SupportedValues(
        absolute={dimension: tuple(sorted(values)) for dimension, values in absolute.items()},
        ratios=tuple(sorted(ratios)),
        percents=tuple(sorted(percents)),
    )


def _merge(parts: Iterable[SupportedValues]) -> SupportedValues:
    absolute: dict[Dimension, set[float]] = _empty_base()
    ratios: set[float] = set()
    percents: set[float] = set()
    for part in parts:
        for dimension, values in part.absolute.items():
            absolute[dimension].update(values)
        ratios.update(part.ratios)
        percents.update(part.percents)
    return SupportedValues(
        absolute={dimension: tuple(sorted(values)) for dimension, values in absolute.items()},
        ratios=tuple(sorted(ratios)),
        percents=tuple(sorted(percents)),
    )


def _classify(text: str, match: re.Match[str]) -> NumericalClaim | None:
    literal = match.group()
    before = text[max(0, match.start() - 40) : match.start()]
    after = text[match.end() :]
    if _IDENTIFIER_PREFIX.search(before) or _LABEL_PREFIX.search(before):
        return None
    if after[:1].isalpha() or after[:1] == "_":
        if _ORDINAL_SUFFIX.match(after):
            return None
        if not re.match(r"x\b", after):
            return None
    if "," in literal and not _VALID_GROUPING.match(literal):
        return _ambiguous(_claim_text(literal, after))
    if literal.count(".") > 1:
        if _EUROPEAN_GROUPING.match(literal):
            return _ambiguous(_claim_text(literal, after))
        return None

    digits = literal.replace(",", "")
    value = float(digits)
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    precision = 0.5 * 10**-decimals
    is_small_integer = decimals == 0 and "," not in literal and value < _SMALL_INTEGER_LIMIT

    unit_context = after
    range_join = _RANGE_JOIN.match(after)
    if range_join is not None:
        range_end = _NUMBER.match(after, range_join.end())
        if range_end is not None:
            unit_context = after[range_end.end() :]
    if re.match(r"-[A-Za-z]", unit_context) and not unit_context.startswith("-fold"):
        if is_small_integer:
            return None
        unit_context = unit_context[1:]
    magnitude = _MAGNITUDE.match(unit_context)
    if magnitude is not None:
        factor = _MAGNITUDE_FACTOR[magnitude.group(1).lower()]
        value *= factor
        precision *= factor
        unit_context = unit_context[magnitude.end() :]

    stripped = unit_context.lstrip()
    kind: ClaimKind = "plain"
    planned_after = None
    if _PERCENT_UNIT.match(stripped):
        kind = "percent"
    elif _MULTIPLIER_UNIT.match(stripped):
        kind = "multiplier"
    elif _COUNT_UNIT.match(stripped.lower()):
        kind = "count"
    else:
        for unit, seconds in _SECONDS_PER_UNIT:
            unit_match = unit.match(stripped.lower())
            if unit_match is not None:
                kind = "time"
                value *= seconds
                precision *= seconds
                planned_after = _PLANNING_SUFFIX.match(stripped[unit_match.end() :])
                break

    hedged = bool(_HEDGE_PREFIX.search(before))
    claim_text = _claim_text(literal, after)
    if kind == "plain" and decimals == 0 and "," not in literal and int(value) in _YEAR_RANGE:
        return NumericalClaim(claim_text, value, kind, precision, hedged, material=False)
    if kind == "plain" and is_small_integer:
        return NumericalClaim(claim_text, value, kind, precision, hedged, material=False)
    planned = planned_after is not None or _PLANNING_PREFIX.search(before) is not None
    if kind == "time" and is_small_integer and planned:
        return NumericalClaim(claim_text, value, kind, precision, hedged, material=False)
    return NumericalClaim(claim_text, value, kind, precision, hedged, material=True)


def _within_tolerance(claim: NumericalClaim, expected: float) -> bool:
    observed = abs(claim.value)
    target = abs(expected)
    tolerance = min(claim.precision, _MAX_RELATIVE_ROUNDING * target)
    if claim.hedged:
        # A hedge widens rounding to one unit of the last stated digit or 1 % relative.
        tolerance = max(
            min(2 * claim.precision, _MAX_RELATIVE_ROUNDING * target),
            _HEDGE_RELATIVE_TOLERANCE * target,
        )
    return math.isclose(observed, target, rel_tol=1e-9, abs_tol=1e-9) or (
        abs(observed - target) <= tolerance + 1e-9 * max(1.0, target)
    )


def _empty_base() -> dict[Dimension, set[float]]:
    return {"time": set(), "count": set(), "ratio": set(), "fraction": set()}


def _numeric_leaves(payload: Any, path: str) -> Iterator[tuple[str, float]]:
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield from _numeric_leaves(value, f"{path}.{key}" if path else str(key))
    elif isinstance(payload, list | tuple):
        for item in payload:
            yield from _numeric_leaves(item, path)
    elif (
        isinstance(payload, int | float)
        and not isinstance(payload, bool)
        and math.isfinite(payload)
    ):
        yield path, float(payload)


def _dimension(path: str, value: float) -> Dimension:
    name = path.lower()
    if "seconds" in name:
        return "time"
    if "ratio" in name:
        return "ratio"
    if ("share" in name or "rate" in name) and 0.0 <= value <= 1.0:
        return "fraction"
    return "count"


def _claim_text(literal: str, after: str) -> str:
    unit = re.match(
        r"(?:\s*(?:-|–|to)\s*\d[\d,.]*)?(?:\s+(?:thousand|million|billion))?"
        r"(?:%|x\b|×|-?fold\b|\s*(?:percentage\s+points?|percent|per\s+cent|pp)\b"
        r"|\s+[A-Za-z][A-Za-z-]*)?",
        after,
    )
    return (literal + (unit.group() if unit else "")).strip()[:60]


def _ambiguous(text: str) -> NumericalClaim:
    return NumericalClaim(text.strip()[:60], math.nan, "plain", 0.0, False, True, True)
