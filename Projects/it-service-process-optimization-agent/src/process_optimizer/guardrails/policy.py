"""Versioned, deterministic guardrail policy loaded from a packaged JSON file."""

from __future__ import annotations

import hashlib
import json
import os
import re
from functools import cached_property
from importlib import resources
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from process_optimizer.guardrails.claims import ClaimRules

SMALL_GROUP_THRESHOLD_ENV = "GUARDRAIL_SMALL_GROUP_THRESHOLD"
POLICY_FILE = "policy.v1.json"


class PolicyModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InputPolicyV1(PolicyModel):
    max_goal_characters: int = Field(ge=1, le=2000)
    max_base64_run_characters: int = Field(ge=16, le=10_000)
    injection_patterns: tuple[str, ...] = Field(min_length=1)


class ToolPolicyV1(PolicyModel):
    analyst: tuple[str, ...]
    investigator: tuple[str, ...]
    optimizer: tuple[str, ...]


class InterventionClaimPolicyV1(PolicyModel):
    """CA-04: no claimed or implied effect of an intervention on an outcome metric.

    Matching is per clause. Exemptions are scoped to one match: a negation or inquiry
    must precede it in the same clause without a scope break, and a non-promise must
    cover it, so a disclaimer elsewhere never cancels an affirmative claim.
    """

    fields: tuple[str, ...] = Field(min_length=1)
    # Fields that name planned measurements; there a match needs a claim marker.
    measurement_fields: tuple[str, ...]
    clause_boundary_pattern: str
    scope_break_pattern: str
    max_gap_characters: int = Field(ge=10, le=200)
    change_patterns: tuple[str, ...] = Field(min_length=1)
    # Comparatives ("fewer") describe cohorts unless the clause is also predictive.
    comparative_patterns: tuple[str, ...]
    predictive_patterns: tuple[str, ...] = Field(min_length=1)
    outcome_patterns: tuple[str, ...] = Field(min_length=1)
    measurement_claim_patterns: tuple[str, ...] = Field(min_length=1)
    # Applied to the text directly before a match ("will not reduce", "no reduction").
    direct_negation_pattern: str
    # Must precede the match in its clause ("does not show that", "test whether").
    scoped_exemption_patterns: tuple[str, ...]
    # Must cover the match ("no reduction in span is promised").
    nominal_non_promise_patterns: tuple[str, ...]
    # Must follow the match in its clause ("… is not promised").
    postfix_non_promise_patterns: tuple[str, ...]
    simulation_label_patterns: tuple[str, ...] = Field(min_length=1)


class InefficiencyClaimPolicyV1(PolicyModel):
    """CA-05: no assertion that a process pattern is inefficient or avoidable."""

    fields: tuple[str, ...] = Field(min_length=1)
    term_patterns: tuple[str, ...] = Field(min_length=1)
    # Applied to the text directly before a term (negation or explicit hedge).
    local_exemption_pattern: str
    scoped_exemption_patterns: tuple[str, ...]


class ExpectedOutcomePolicyV1(PolicyModel):
    """CA-06: an expected outcome from observational evidence must be qualified."""

    field: str
    qualifier_patterns: tuple[str, ...] = Field(min_length=1)
    simulation_label_patterns: tuple[str, ...] = Field(min_length=1)


class OutputPolicyV1(PolicyModel):
    assertive_causal_fields: tuple[str, ...]
    assertive_causal_patterns: tuple[str, ...]
    savings_fields: tuple[str, ...]
    savings_patterns: tuple[str, ...]
    # Matched case-sensitively so that e.g. "roi" inside prose does not trigger.
    savings_acronym_patterns: tuple[str, ...]
    # A hypothesis may use causal wording only when the same clause is explicitly
    # labelled as uncertain (or the hypothesis opens with a "Hypothesis:" prefix) and
    # the recommendation cites evidence.
    hypothesis_causal_patterns: tuple[str, ...]
    hypothesis_label_patterns: tuple[str, ...] = Field(min_length=1)
    hypothesis_prefix_pattern: str
    intervention_claim: InterventionClaimPolicyV1
    inefficiency_claim: InefficiencyClaimPolicyV1
    expected_outcome: ExpectedOutcomePolicyV1


class ClaimTypePolicyV1(PolicyModel):
    """CA-07: one claim type and the evidence interpretation it would need.

    The validator blocks a matched claim unless a cited finding carries one of the
    required interpretations. Evidence Contract V1 only provides
    ``observational_non_causal``, so today every declared claim type is unsupported.
    """

    name: str = Field(pattern=r"^[a-z_]{1,40}$")
    reason_code: str = Field(pattern=r"^UNSUPPORTED_[A-Z_]{1,60}$")
    required_evidence_interpretations: tuple[str, ...] = Field(min_length=1)
    fields: tuple[str, ...] = Field(min_length=1)
    # A labelled hypothesis ("may", "Hypothesis:") may name a candidate cause; it may
    # never state a quantified effect.
    labelled_hypotheses_exempt: bool
    # Added to the CA-04 exemptions for this claim type only (e.g. a conditional).
    scoped_exemption_patterns: tuple[str, ...]
    frames: tuple[str, ...] = Field(min_length=1)


class ClaimEvidencePolicyV1(PolicyModel):
    # Interpretations this policy knows; a cited finding with any other one fails closed.
    known_evidence_interpretations: tuple[str, ...] = Field(min_length=1)
    claim_types: tuple[ClaimTypePolicyV1, ...] = Field(min_length=1)


class HostedRuntimePolicyV1(PolicyModel):
    """Upper bounds the hosted runtime configuration must stay within."""

    max_model_calls_per_run: int = Field(ge=1, le=20)
    max_model_calls_per_process: int = Field(ge=1, le=20)
    max_model_retries: int = Field(ge=0, le=5)
    max_output_tokens: int = Field(ge=1, le=32768)
    # Conservative (3 characters per token) estimate of one request's input; a request
    # estimated above this is never sent, so it bounds every call's reservation.
    max_input_tokens_per_call: int = Field(ge=1000, le=200_000)
    # Input + output tokens per investigation, including failed attempts.
    max_run_tokens: int = Field(ge=1000, le=1_000_000)
    run_deadline_seconds: int = Field(ge=10, le=900)

    @property
    def call_reservation_tokens(self) -> int:
        """Worst-case tokens one permitted call can consume."""
        return self.max_input_tokens_per_call + self.max_output_tokens


class GuardrailPolicyV1(PolicyModel):
    policy_id: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    # 2.0.0 added required output sections (CA-04..07) and claim_evidence; neither a
    # 1.x reader nor a 2.x reader accepts the other's document, hence a major bump.
    policy_schema_version: Literal["2.0.0"]
    # Counts from 1 to k-1 are suppressed; 0 is a structural zero and stays visible.
    small_group_threshold: int = Field(ge=2, le=1000)
    input: InputPolicyV1
    tools: ToolPolicyV1
    output: OutputPolicyV1
    claim_evidence: ClaimEvidencePolicyV1
    hosted_runtime: HostedRuntimePolicyV1

    @model_validator(mode="after")
    def patterns_compile(self) -> GuardrailPolicyV1:
        intervention = self.output.intervention_claim
        inefficiency = self.output.inefficiency_claim
        outcome = self.output.expected_outcome
        for pattern in (
            *self.input.injection_patterns,
            *self.output.assertive_causal_patterns,
            *self.output.savings_patterns,
            *self.output.savings_acronym_patterns,
            *self.output.hypothesis_causal_patterns,
            *self.output.hypothesis_label_patterns,
            self.output.hypothesis_prefix_pattern,
            intervention.clause_boundary_pattern,
            intervention.scope_break_pattern,
            *intervention.change_patterns,
            *intervention.comparative_patterns,
            *intervention.predictive_patterns,
            *intervention.outcome_patterns,
            *intervention.measurement_claim_patterns,
            intervention.direct_negation_pattern,
            *intervention.scoped_exemption_patterns,
            *intervention.nominal_non_promise_patterns,
            *intervention.postfix_non_promise_patterns,
            *intervention.simulation_label_patterns,
            *inefficiency.term_patterns,
            inefficiency.local_exemption_pattern,
            *inefficiency.scoped_exemption_patterns,
            *outcome.qualifier_patterns,
            *outcome.simulation_label_patterns,
            *(frame for claim in self.claim_evidence.claim_types for frame in claim.frames),
            *(
                pattern
                for claim in self.claim_evidence.claim_types
                for pattern in claim.scoped_exemption_patterns
            ),
        ):
            re.compile(pattern)
        for claim in self.claim_evidence.claim_types:
            for frame in claim.frames:
                if "rel" not in re.compile(frame).groupindex:
                    raise ValueError("Every claim frame needs a named 'rel' group")
        names = [claim.name for claim in self.claim_evidence.claim_types]
        if len(names) != len(set(names)):
            raise ValueError("Claim type names must be unique")
        if not set(intervention.measurement_fields) <= set(intervention.fields):
            raise ValueError("Measurement fields must be intervention-claim fields")
        runtime = self.hosted_runtime
        if runtime.max_model_calls_per_run > runtime.max_model_calls_per_process:
            raise ValueError("Per-run model calls cannot exceed the per-process cap")
        if runtime.max_run_tokens < runtime.call_reservation_tokens:
            raise ValueError("The run token budget must cover at least one call reservation")
        return self

    @cached_property
    def version(self) -> str:
        """SHA-256 of the effective policy, carried by every decision."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @cached_property
    def compiled_injection_patterns(self) -> tuple[re.Pattern[str], ...]:
        return tuple(re.compile(item, re.IGNORECASE) for item in self.input.injection_patterns)

    @cached_property
    def compiled_causal_patterns(self) -> tuple[re.Pattern[str], ...]:
        return tuple(
            re.compile(item, re.IGNORECASE) for item in self.output.assertive_causal_patterns
        )

    @cached_property
    def compiled_hypothesis_causal_patterns(self) -> tuple[re.Pattern[str], ...]:
        return tuple(
            re.compile(item, re.IGNORECASE) for item in self.output.hypothesis_causal_patterns
        )

    @cached_property
    def compiled_hypothesis_label_patterns(self) -> tuple[re.Pattern[str], ...]:
        return tuple(
            re.compile(item, re.IGNORECASE) for item in self.output.hypothesis_label_patterns
        )

    @cached_property
    def compiled_savings_patterns(self) -> tuple[re.Pattern[str], ...]:
        return (
            *(re.compile(item, re.IGNORECASE) for item in self.output.savings_patterns),
            *(re.compile(item) for item in self.output.savings_acronym_patterns),
        )

    @cached_property
    def claim_rules(self) -> ClaimRules:
        return ClaimRules.from_policy(self.output, self.claim_evidence)

    def stage_tools(self, stage: str) -> tuple[str, ...]:
        """Return the tool allowlist for one graph stage; unknown stages get none."""
        return tuple(getattr(self.tools, stage, ())) if stage in ToolPolicyV1.model_fields else ()


class PolicyLoadError(RuntimeError):
    """Raised when the policy is missing, malformed or overridden out of range."""


def load_policy(path: Path | None = None) -> GuardrailPolicyV1:
    """Load the packaged policy (or an explicit file) and apply the validated k override."""
    try:
        if path is None:
            raw = resources.files(__package__).joinpath(POLICY_FILE).read_text(encoding="utf-8")
        else:
            raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
        override = os.environ.get(SMALL_GROUP_THRESHOLD_ENV, "").strip()
        if override:
            if not override.isdigit():
                raise ValueError(f"{SMALL_GROUP_THRESHOLD_ENV} must be an integer")
            data["small_group_threshold"] = int(override)
        return GuardrailPolicyV1.model_validate(data)
    except (OSError, ValueError, TypeError) as error:
        raise PolicyLoadError("Guardrail policy could not be loaded") from error
