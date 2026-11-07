"""Clause-scoped output rules CA-03 (labels), CA-04, CA-05 and CA-06.

The rules are deterministic and pattern based: they react to wording, not meaning, so a
paraphrase outside the policy vocabulary can still pass. Every exemption is scoped to a
single match. A negation or an inquiry ("does not show that", "test whether") must precede
the match in the same clause with no scope break in between, and a non-promise must cover
the match itself. A disclaimer elsewhere in the text therefore never cancels an
affirmative claim.

No exemption exists for measured intervention effects: Evidence Contract V1 findings are
``observational_non_causal`` by schema, so no cited evidence can support one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from process_optimizer.guardrails.policy import ClaimEvidencePolicyV1, OutputPolicyV1

_DIGIT = re.compile(r"\d")
Patterns = tuple[re.Pattern[str], ...]
HYPOTHESES = "hypotheses"


@dataclass(frozen=True)
class ClaimType:
    """CA-07: a compiled claim type and the evidence interpretations it requires."""

    name: str
    reason_code: str
    required: frozenset[str]
    fields: tuple[str, ...]
    labelled_hypotheses_exempt: bool
    scoped_exemptions: Patterns
    frames: Patterns


def _compile(patterns: Iterable[str]) -> Patterns:
    return tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)


@dataclass(frozen=True)
class ClaimRules:
    boundary: re.Pattern[str]
    scope_break: re.Pattern[str]
    gap: int
    intervention_fields: tuple[str, ...]
    measurement_fields: frozenset[str]
    change: Patterns
    comparative: Patterns
    predictive: Patterns
    outcome: Patterns
    measurement_claim: Patterns
    direct_negation: re.Pattern[str]
    scoped_exemptions: Patterns
    nominal_non_promise: Patterns
    postfix_non_promise: Patterns
    simulation_labels: Patterns
    inefficiency_fields: tuple[str, ...]
    inefficiency_terms: Patterns
    inefficiency_local_exemption: re.Pattern[str]
    inefficiency_scoped_exemptions: Patterns
    outcome_field: str
    outcome_qualifiers: Patterns
    outcome_simulation_labels: Patterns
    hypothesis_causal: Patterns
    hypothesis_labels: Patterns
    hypothesis_prefix: re.Pattern[str]
    claim_types: tuple[ClaimType, ...]

    @classmethod
    def from_policy(
        cls, output: OutputPolicyV1, claim_evidence: ClaimEvidencePolicyV1
    ) -> ClaimRules:
        claim = output.intervention_claim
        inefficiency = output.inefficiency_claim
        outcome = output.expected_outcome
        return cls(
            boundary=re.compile(claim.clause_boundary_pattern, re.IGNORECASE),
            scope_break=re.compile(claim.scope_break_pattern, re.IGNORECASE),
            gap=claim.max_gap_characters,
            intervention_fields=claim.fields,
            measurement_fields=frozenset(claim.measurement_fields),
            change=_compile(claim.change_patterns),
            comparative=_compile(claim.comparative_patterns),
            predictive=_compile(claim.predictive_patterns),
            outcome=_compile(claim.outcome_patterns),
            measurement_claim=_compile(claim.measurement_claim_patterns),
            direct_negation=re.compile(claim.direct_negation_pattern, re.IGNORECASE),
            scoped_exemptions=_compile(claim.scoped_exemption_patterns),
            nominal_non_promise=_compile(claim.nominal_non_promise_patterns),
            postfix_non_promise=_compile(claim.postfix_non_promise_patterns),
            simulation_labels=_compile(claim.simulation_label_patterns),
            inefficiency_fields=inefficiency.fields,
            inefficiency_terms=_compile(inefficiency.term_patterns),
            inefficiency_local_exemption=re.compile(
                inefficiency.local_exemption_pattern, re.IGNORECASE
            ),
            inefficiency_scoped_exemptions=_compile(inefficiency.scoped_exemption_patterns),
            outcome_field=outcome.field,
            outcome_qualifiers=_compile(outcome.qualifier_patterns),
            outcome_simulation_labels=_compile(outcome.simulation_label_patterns),
            hypothesis_causal=_compile(output.hypothesis_causal_patterns),
            hypothesis_labels=_compile(output.hypothesis_label_patterns),
            hypothesis_prefix=re.compile(output.hypothesis_prefix_pattern, re.IGNORECASE),
            claim_types=tuple(
                ClaimType(
                    name=item.name,
                    reason_code=item.reason_code,
                    required=frozenset(item.required_evidence_interpretations),
                    fields=item.fields,
                    labelled_hypotheses_exempt=item.labelled_hypotheses_exempt,
                    scoped_exemptions=_compile(item.scoped_exemption_patterns),
                    frames=_compile(item.frames),
                )
                for item in claim_evidence.claim_types
            ),
        )

    def clauses(self, text: str) -> list[str]:
        return [part for part in self.boundary.split(text) if part and part.strip()]

    # -- CA-04 -----------------------------------------------------------------------

    def intervention_claim(self, texts: dict[str, str], *, simulation_cited: bool) -> bool:
        """An effect of an intervention on an outcome metric, or a changed quantity."""
        for field in self.intervention_fields:
            planned_measurement = field in self.measurement_fields
            for clause in self.clauses(texts[field]):
                claim_marker = _any(self.measurement_claim, clause)
                if planned_measurement and not claim_marker:
                    continue
                # A number next to a change verb restates a quantity as an effect; only a
                # cited simulation, labelled as such in the clause, may state one.
                numeric = bool(_DIGIT.search(clause)) and not (
                    simulation_cited and _any(self.simulation_labels, clause)
                )
                for match in _matches(self.change, clause):
                    if (numeric or self._near_outcome(clause, match)) and not self._exempt(
                        clause, match
                    ):
                        return True
                if _any(self.predictive, clause):
                    for match in _matches(self.comparative, clause):
                        if self._near_outcome(clause, match) and not self._exempt(
                            clause, match
                        ):
                            return True
        return False

    def _near_outcome(self, clause: str, match: re.Match[str]) -> bool:
        low, high = match.start() - self.gap, match.end() + self.gap
        return any(
            item.end() >= low and item.start() <= high for item in _matches(self.outcome, clause)
        )

    def _exempt(self, clause: str, match: re.Match[str]) -> bool:
        return self._exempt_span(clause, match.start(), match.end())

    def _exempt_span(self, clause: str, start: int, end: int) -> bool:
        if self.direct_negation.search(clause[:start]):
            return True
        if self._preceded(self.scoped_exemptions, clause, start):
            return True
        if any(item.start() <= start < item.end() for item in _matches(
            self.nominal_non_promise, clause
        )):
            return True
        tail = clause[end:]
        return any(
            (found := pattern.search(tail)) is not None
            and not self.scope_break.search(found.group())
            for pattern in self.postfix_non_promise
        )

    def _preceded(self, patterns: Patterns, clause: str, start: int) -> bool:
        return any(
            item.end() <= start and not self.scope_break.search(clause, item.end(), start)
            for pattern in patterns
            for item in pattern.finditer(clause, 0, start)
        )

    # -- CA-05 -----------------------------------------------------------------------

    def inefficiency_claim(self, texts: dict[str, str]) -> bool:
        for field in self.inefficiency_fields:
            for clause in self.clauses(texts[field]):
                for match in _matches(self.inefficiency_terms, clause):
                    if self.inefficiency_local_exemption.search(clause[: match.start()]):
                        continue
                    if self._preceded(self.inefficiency_scoped_exemptions, clause, match.start()):
                        continue
                    return True
        return False

    # -- CA-06 -----------------------------------------------------------------------

    def unqualified_expected_outcome(
        self, texts: dict[str, str], *, simulation_cited: bool
    ) -> bool:
        text = texts[self.outcome_field]
        if _any(self.outcome_qualifiers, text):
            return False
        return not (simulation_cited and _any(self.outcome_simulation_labels, text))

    # -- CA-07 -----------------------------------------------------------------------

    def matched_claim_types(
        self, texts: dict[str, str], hypotheses: Iterable[str]
    ) -> tuple[ClaimType, ...]:
        """Claim types stated anywhere in the given fields (each exemption per match)."""
        hypotheses = tuple(hypotheses)
        return tuple(
            claim
            for claim in self.claim_types
            if any(self._states(claim, field, texts, hypotheses) for field in claim.fields)
        )

    def _states(
        self, claim: ClaimType, field: str, texts: dict[str, str], hypotheses: tuple[str, ...]
    ) -> bool:
        items = hypotheses if field == HYPOTHESES else (texts[field],)
        for item in items:
            prefixed = field == HYPOTHESES and bool(self.hypothesis_prefix.search(item))
            for clause in self.clauses(item):
                if (
                    field == HYPOTHESES
                    and claim.labelled_hypotheses_exempt
                    and (prefixed or _any(self.hypothesis_labels, clause))
                ):
                    continue
                for match in _matches(claim.frames, clause):
                    start, end = match.start("rel"), match.end("rel")
                    if not (
                        self._exempt_span(clause, start, end)
                        or self._preceded(claim.scoped_exemptions, clause, start)
                    ):
                        return True
        return False

    # -- CA-03 -----------------------------------------------------------------------

    def unlabelled_causal_hypothesis(self, hypotheses: Iterable[str]) -> bool:
        """Causal wording needs an uncertainty label in the same clause (or a prefix)."""
        for hypothesis in hypotheses:
            prefixed = bool(self.hypothesis_prefix.search(hypothesis))
            for clause in self.clauses(hypothesis):
                if _any(self.hypothesis_causal, clause) and not (
                    prefixed or _any(self.hypothesis_labels, clause)
                ):
                    return True
        return False


def _any(patterns: Patterns, text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _matches(patterns: Patterns, text: str) -> list[re.Match[str]]:
    return [match for pattern in patterns for match in pattern.finditer(text)]
