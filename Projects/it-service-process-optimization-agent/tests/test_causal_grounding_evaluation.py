"""Frozen causal-grounding evaluation sets: evidence of residual gaps, not tuning targets.

``heldout_v1`` was written before CA-04..06 and evaluated once against policy a0b4adeb…;
its four misses drove CA-07, so it is no longer independent. ``independent_v2`` was
written and hashed (2026-11-01 09:17:58Z) before CA-07 was implemented and evaluated
once afterwards; the same author wrote both the rules and the set. The snapshots record
today's outcome per case, including false negatives and false positives. Changing a rule
that flips a case must update the snapshot deliberately; the sets themselves never change.

One documented exception (2026-11-06, public release): in ``independent_v2`` the numbers in
cases I-U06, I-S01 and I-S02 were replaced with the synthetic values used by the other unit-test
fixtures, so that no statistic of the original dataset is published, and the opening verb of
I-S06 was changed ("Interview" → "Ask the", keeping the "group leads to" phrase that makes it a
false positive). Fields, labels and every snapshot outcome are unchanged. The original SHA-256
was 1922fdb1…bf9b3.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from tests import test_causal_grounding_matrix as matrix

DATA = Path(__file__).parent / "data"
SETS = {
    "causal_grounding_heldout_v1.json":
        "a0d7dc4338a1266e67fb53c5027906a9b6a3796135520a0396f80713cb33f5df",
    "causal_grounding_independent_v2.json":
        "f2f4c29a8d7326015707fd2fca39e08865aaaaa058c1074709a8f380fd28e085",
}
FIELDS = {"PS": matrix.PS, "SI": matrix.SI, "EO": matrix.EO, "KPI": matrix.KPI,
          "HY": matrix.HY}
BLOCK, APPROVAL = "BLOCK", "REQUIRE_APPROVAL"
# Unsafe cases that still reach REQUIRE_APPROVAL and safe cases that BLOCK.
FALSE_NEGATIVES = {"I-U01", "I-U02", "I-U07", "I-U09", "I-U10", "I-U15"}
# I-S06: the pre-existing CA-01 pattern "leads to" matches the noun "group leads".
FALSE_POSITIVES = {"I-S06"}


def _cases() -> list[tuple[str, str, str, dict[str, object]]]:
    cases = []
    for name in SETS:
        data = json.loads((DATA / name).read_text(encoding="utf-8"))
        cases.extend((name, group, case["id"], case) for group in ("unsafe", "safe")
                     for case in data[group])
    return cases


@pytest.mark.parametrize("name", list(SETS))
def test_evaluation_sets_are_unchanged(name: str) -> None:
    assert hashlib.sha256((DATA / name).read_bytes()).hexdigest() == SETS[name]


@pytest.mark.parametrize(("name", "group", "case_id", "case"), _cases(),
                         ids=[case[2] for case in _cases()])
def test_evaluation_snapshot(name: str, group: str, case_id: str, case: dict) -> None:
    field = FIELDS[str(case["field"])]
    text = (case["text"],) if field == matrix.HY else case["text"]
    decision = matrix._decide(field, text, simulation=bool(case.get("sim"))).decision.value

    intended = BLOCK if group == "unsafe" else APPROVAL
    flipped = case_id in FALSE_NEGATIVES | FALSE_POSITIVES
    assert decision == ((APPROVAL if group == "unsafe" else BLOCK) if flipped else intended)
