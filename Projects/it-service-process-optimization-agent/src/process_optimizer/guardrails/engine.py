"""In-process deterministic guardrail engine; no network, model or database calls.

Every check returns a `GuardrailDecisionV1`. Any exception inside a check, an unknown
checkpoint or an unknown validator error code fails closed to BLOCK. The model is never
the authority for a decision.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from process_optimizer.guardrails.compatibility import policy_compatibility_codes
from process_optimizer.guardrails.decisions import (
    Checkpoint,
    Decision,
    GuardrailDecisionV1,
    most_restrictive,
)
from process_optimizer.guardrails.policy import GuardrailPolicyV1, load_policy
from process_optimizer.guardrails.suppression import contains_small_group

if TYPE_CHECKING:  # Runtime import would cycle: agents -> tools -> guardrails -> agents.
    from process_optimizer.agents.schemas import (
        EvidenceValidationResult,
        RecommendationCandidate,
    )

GENAI_CONTENT_CAPTURE_ENV = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"

# Every code `validate_recommendations` can emit. A code outside this set means the
# validator changed without a reviewed guardrail mapping, so it blocks.
KNOWN_VALIDATOR_ERROR_CODES = frozenset(
    {
        "INVALID_EVIDENCE_ID",
        "MISSING_EVIDENCE",
        "INVALID_NUMERICAL_CITATION",
        "FABRICATED_OR_MISMATCHED_NUMERICAL_CLAIM",
        "AMBIGUOUS_NUMERICAL_CLAIM_IN_TEXT",
        "UNSUPPORTED_NUMERICAL_CLAIM_IN_TEXT",
        "NUMERICAL_FACT_STATEMENT_MISMATCH",
        "UNSUPPORTED_CAUSAL_STATEMENT",
        "COMPLETED_LIFECYCLE_SEMANTIC_TRAP",
        "HUMAN_APPROVAL_NOT_REQUIRED",
        "MISSING_DATA_LIMITATIONS",
        "MISSING_SIMULATION_CITATION",
        "SIMULATION_NOT_ESTIMABLE",
        "INVALID_SIMULATION_CITATION",
        "SIMULATION_ASSUMPTION_MISMATCH",
        # CA-07: claims that need an evidence interpretation no cited finding provides.
        "UNSUPPORTED_CAUSAL_ATTRIBUTION",
        "UNSUPPORTED_TIMING_EFFECT",
        "UNSUPPORTED_QUANTIFIED_EFFECT",
        "UNKNOWN_EVIDENCE_TYPE",
    }
)

# Unicode categories never needed in an investigation goal: controls, format characters
# (zero-width, bidi overrides), surrogates, private use and unassigned code points.
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})
_BASE64_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")

EVIDENCE_CONTRACT_SHA256_ENV = "EVIDENCE_CONTRACT_SHA256"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_RUNTIME_ENV = {
    "MODEL_MAX_CALLS": "max_model_calls_per_process",
    "MODEL_RUN_MAX_CALLS": "max_model_calls_per_run",
    "MODEL_MAX_RETRIES": "max_model_retries",
    "MODEL_MAX_OUTPUT_TOKENS": "max_output_tokens",
    "MODEL_RUN_TOKEN_BUDGET": "max_run_tokens",
    "RUN_DEADLINE_SECONDS": "run_deadline_seconds",
}


class GuardrailEngine:
    """Evaluate one checkpoint at a time against a loaded, versioned policy."""

    def __init__(self, policy: GuardrailPolicyV1 | None = None) -> None:
        self.policy = policy or load_policy()

    # -- generic entry point ---------------------------------------------------------

    def evaluate(self, checkpoint: Checkpoint | str, **context: Any) -> GuardrailDecisionV1:
        """Dispatch by checkpoint name; unknown checkpoints and bad context block."""
        handlers: dict[Checkpoint, Callable[..., GuardrailDecisionV1]] = {
            Checkpoint.HOST_STARTUP: self.check_host_startup,
            Checkpoint.INVOCATION_INPUT: self.check_invocation_input,
            Checkpoint.PRE_MODEL_CALL: self.check_model_call,
            Checkpoint.PRE_TOOL_CALL: self.check_tool_request,
            Checkpoint.POST_TOOL_RESULT: self.check_tool_result,
            Checkpoint.RECOMMENDATION_OUTPUT: self.check_recommendations,
            Checkpoint.WORKFLOW_SUBMIT: self.check_submission,
            Checkpoint.ACTION_EXECUTE: self.check_execution,
        }
        try:
            handler = handlers[Checkpoint(checkpoint)]
        except (KeyError, ValueError):
            return self._decide(Checkpoint.HOST_STARTUP, Decision.BLOCK, "UNKNOWN_CHECKPOINT")
        try:
            return handler(**context)
        except Exception:  # noqa: BLE001 - a guardrail defect must never allow.
            return self._decide(Checkpoint(checkpoint), Decision.BLOCK, "GUARDRAIL_INTERNAL_ERROR")

    # -- checkpoints -----------------------------------------------------------------

    def check_host_startup(
        self, *, environment: Mapping[str, str], provider_mode: str
    ) -> GuardrailDecisionV1:
        """Refuse to host unless telemetry capture is off and paid limits are explicit."""
        return self._guard(
            Checkpoint.HOST_STARTUP, lambda: self._host_startup(environment, provider_mode)
        )

    def check_policy_compatibility(self, *, remote_metadata: Any) -> GuardrailDecisionV1:
        """EV-04: the Workflow API must enforce exactly this policy (identity only)."""

        def rule() -> tuple[Decision, tuple[str, ...]]:
            codes = policy_compatibility_codes(self.policy, remote_metadata)
            return (Decision.BLOCK if codes else Decision.ALLOW), codes

        return self._guard(Checkpoint.HOST_STARTUP, rule)

    def check_invocation_input(self, *, goal: str) -> GuardrailDecisionV1:
        return self._guard(Checkpoint.INVOCATION_INPUT, lambda: self._input(goal))

    def check_model_call(
        self,
        *,
        calls_used: int,
        max_calls: int,
        deadline_epoch_seconds: float | None,
        now: float | None = None,
        tokens_used: int = 0,
        token_budget: int | None = None,
    ) -> GuardrailDecisionV1:
        limits = self.policy.hosted_runtime

        def rule() -> tuple[Decision, tuple[str, ...]]:
            codes: list[str] = []
            if calls_used >= min(max_calls, limits.max_model_calls_per_run):
                codes.append("MODEL_CALL_LIMIT_REACHED")
            # BU-03: the next call is permitted only if its worst case still fits.
            configured = limits.max_run_tokens if token_budget is None else token_budget
            budget = min(configured, limits.max_run_tokens)
            if tokens_used < 0 or tokens_used + limits.call_reservation_tokens > budget:
                codes.append("MODEL_TOKEN_BUDGET_EXHAUSTED")
            if _deadline_passed(deadline_epoch_seconds, now):
                codes.append("RUN_DEADLINE_EXCEEDED")
            return (Decision.BLOCK if codes else Decision.ALLOW), tuple(codes)

        return self._guard(Checkpoint.PRE_MODEL_CALL, rule)

    def check_tool_request(
        self,
        *,
        stage: str,
        tool_name: str,
        deadline_epoch_seconds: float | None = None,
        now: float | None = None,
    ) -> GuardrailDecisionV1:
        def rule() -> tuple[Decision, tuple[str, ...]]:
            codes: list[str] = []
            if tool_name not in self.policy.stage_tools(stage):
                codes.append("TOOL_NOT_ALLOWED_FOR_STAGE")
            if _deadline_passed(deadline_epoch_seconds, now):
                codes.append("RUN_DEADLINE_EXCEEDED")
            return (Decision.BLOCK if codes else Decision.ALLOW), tuple(codes)

        return self._guard(Checkpoint.PRE_TOOL_CALL, rule)

    def check_tool_result(self, *, items: Sequence[dict[str, Any]]) -> GuardrailDecisionV1:
        """Defence in depth: the aggregate tools already suppress before limiting."""

        def rule() -> tuple[Decision, tuple[str, ...]]:
            if contains_small_group(list(items), self.policy.small_group_threshold):
                return Decision.BLOCK, ("SMALL_GROUP_DISCLOSURE",)
            return Decision.ALLOW, ()

        return self._guard(Checkpoint.POST_TOOL_RESULT, rule)

    def check_recommendations(
        self,
        *,
        recommendations: Sequence[RecommendationCandidate],
        validation_results: Sequence[EvidenceValidationResult],
    ) -> GuardrailDecisionV1:
        return self._guard(
            Checkpoint.RECOMMENDATION_OUTPUT,
            lambda: self._recommendations(recommendations, validation_results),
        )

    def check_submission(
        self,
        *,
        recommendation: RecommendationCandidate,
        validation_result: EvidenceValidationResult,
        identity_type: str,
    ) -> GuardrailDecisionV1:
        def rule() -> tuple[Decision, tuple[str, ...]]:
            decision, codes = self._recommendations((recommendation,), (validation_result,))
            # "local" exists only under the local (non-production) authentication adapter.
            if identity_type not in {"human", "local"}:
                return Decision.BLOCK, ("HUMAN_IDENTITY_REQUIRED", *codes)
            return decision, codes

        return self._guard(Checkpoint.WORKFLOW_SUBMIT, rule)

    def check_execution(
        self, *, approval_state: str, decision: str | None
    ) -> GuardrailDecisionV1:
        def rule() -> tuple[Decision, tuple[str, ...]]:
            if approval_state == "EXECUTING" and decision == "APPROVED":
                return Decision.ALLOW, ()
            return Decision.BLOCK, ("EXECUTION_WITHOUT_APPROVAL",)

        return self._guard(Checkpoint.ACTION_EXECUTE, rule)

    def visible_finding_ids(self, findings: Sequence[Any]) -> frozenset[str]:
        """Finding IDs whose aggregates may be shown to a model (no small groups)."""
        return frozenset(
            finding.finding_id
            for finding in findings
            if not contains_small_group(
                finding.model_dump(mode="json"), self.policy.small_group_threshold
            )
        )

    # -- rules -----------------------------------------------------------------------

    def _host_startup(
        self, environment: Mapping[str, str], provider_mode: str
    ) -> tuple[Decision, tuple[str, ...]]:
        codes: list[str] = []
        if environment.get(GENAI_CONTENT_CAPTURE_ENV) != "false":
            codes.append("GENAI_CONTENT_CAPTURE_NOT_DISABLED")
        if provider_mode == "real_model":
            if environment.get("ALLOW_PAID_MODEL_CALLS") != "true":
                codes.append("PAID_MODEL_CALLS_NOT_AUTHORIZED")
            limits = self.policy.hosted_runtime
            if not _SHA256.fullmatch(environment.get(EVIDENCE_CONTRACT_SHA256_ENV, "")):
                codes.append("EVIDENCE_CONTRACT_SHA256_NOT_PINNED")
            for name, field in _RUNTIME_ENV.items():
                value = environment.get(name, "").strip()
                if not value.isdigit():
                    codes.append(f"{name}_NOT_EXPLICIT")
                    continue
                maximum = getattr(limits, field)
                minimum = 0 if field == "max_model_retries" else 1
                if not minimum <= int(value) <= maximum:
                    codes.append(f"{name}_OUT_OF_POLICY")
        elif provider_mode != "mocked_llm":
            codes.append("UNKNOWN_PROVIDER_MODE")
        return (Decision.BLOCK if codes else Decision.ALLOW), tuple(codes)

    def _input(self, goal: str) -> tuple[Decision, tuple[str, ...]]:
        codes: list[str] = []
        normalized = unicodedata.normalize("NFKC", goal)
        if len(normalized) > self.policy.input.max_goal_characters:
            codes.append("INPUT_TOO_LONG")
        if any(
            unicodedata.category(character) in _FORBIDDEN_CATEGORIES
            and character not in {" "}
            for character in normalized
        ):
            codes.append("INPUT_FORBIDDEN_CHARACTER")
        if any(pattern.search(normalized) for pattern in self.policy.compiled_injection_patterns):
            codes.append("INPUT_INJECTION_PATTERN")
        if _longest_base64_run(normalized) > self.policy.input.max_base64_run_characters:
            codes.append("INPUT_ENCODED_PAYLOAD")
        return (Decision.BLOCK if codes else Decision.ALLOW), tuple(codes)

    def _recommendations(
        self,
        recommendations: Sequence[RecommendationCandidate],
        validation_results: Sequence[EvidenceValidationResult],
    ) -> tuple[Decision, tuple[str, ...]]:
        if not recommendations or len(recommendations) != len(validation_results):
            return Decision.BLOCK, ("RECOMMENDATION_VALIDATION_INCOMPLETE",)
        by_id = {result.recommendation_id: result for result in validation_results}
        decisions: list[Decision] = []
        codes: list[str] = []
        for recommendation in recommendations:
            result = by_id.get(recommendation.recommendation_id)
            if result is None:
                decisions.append(Decision.BLOCK)
                codes.append("RECOMMENDATION_VALIDATION_INCOMPLETE")
                continue
            unknown = set(result.error_codes) - KNOWN_VALIDATOR_ERROR_CODES
            if unknown:
                codes.append("UNKNOWN_VALIDATOR_ERROR_CODE")
            codes.extend(code for code in result.error_codes if code not in unknown)
            if not result.valid or result.error_codes:
                decisions.append(Decision.BLOCK)
                if not result.error_codes:
                    codes.append("VALIDATION_FAILED")
                continue
            text_codes = self._output_text(recommendation)
            codes.extend(text_codes)
            if text_codes:
                decisions.append(Decision.BLOCK)
            elif recommendation.requires_human_approval is not True:
                decisions.append(Decision.BLOCK)
                codes.append("HUMAN_APPROVAL_NOT_REQUIRED")
            else:
                # A validated recommendation is never ALLOW: only a human may act on it.
                decisions.append(Decision.REQUIRE_APPROVAL)
        return most_restrictive(decisions), tuple(dict.fromkeys(codes))

    def _output_text(self, recommendation: RecommendationCandidate) -> list[str]:
        output = self.policy.output
        codes: list[str] = []
        causal_text = " ".join(_field_texts(recommendation, output.assertive_causal_fields))
        if any(pattern.search(causal_text) for pattern in self.policy.compiled_causal_patterns):
            codes.append("UNSUPPORTED_CAUSAL_STATEMENT")
        rules = self.policy.claim_rules
        causal_hypothesis = any(
            pattern.search(hypothesis)
            for hypothesis in recommendation.hypotheses
            for pattern in self.policy.compiled_hypothesis_causal_patterns
        )
        if rules.unlabelled_causal_hypothesis(recommendation.hypotheses) or (
            causal_hypothesis and not recommendation.evidence_ids
        ):
            codes.append("UNSUPPORTED_CAUSAL_CONCLUSION")
        savings_text = " ".join(_field_texts(recommendation, output.savings_fields))
        if any(pattern.search(savings_text) for pattern in self.policy.compiled_savings_patterns):
            codes.append("UNSUPPORTED_SAVINGS_CLAIM")
        texts = {
            field: getattr(recommendation, field)
            for field in (
                *rules.intervention_fields,
                *rules.inefficiency_fields,
                rules.outcome_field,
            )
        }
        simulation_cited = recommendation.simulation is not None
        if rules.intervention_claim(texts, simulation_cited=simulation_cited):
            codes.append("UNSUPPORTED_INTERVENTION_CLAIM")
        if rules.inefficiency_claim(texts):
            codes.append("UNSUPPORTED_INEFFICIENCY_CLAIM")
        if rules.unqualified_expected_outcome(texts, simulation_cited=simulation_cited):
            codes.append("UNQUALIFIED_EXPECTED_OUTCOME")
        return codes

    # -- plumbing --------------------------------------------------------------------

    def _guard(
        self,
        checkpoint: Checkpoint,
        rule: Callable[[], tuple[Decision, tuple[str, ...]]],
    ) -> GuardrailDecisionV1:
        try:
            decision, codes = rule()
        except Exception:  # noqa: BLE001 - fail closed on any defect.
            return self._decide(checkpoint, Decision.BLOCK, "GUARDRAIL_INTERNAL_ERROR")
        return self._decide(checkpoint, decision, *codes)

    def _decide(
        self, checkpoint: Checkpoint, decision: Decision, *codes: str
    ) -> GuardrailDecisionV1:
        return GuardrailDecisionV1(
            checkpoint=checkpoint,
            decision=decision,
            reason_codes=tuple(codes),
            policy_id=self.policy.policy_id,
            policy_version=self.policy.version,
        )


def evidence_contract_sha256(contract: Any) -> str:
    """EV-02: SHA-256 of the canonical JSON of the evidence contract as served."""
    payload = contract.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_evidence_contract_pin(contract: Any, pinned: str | None) -> bool:
    """Return True only when the fetched contract matches the pinned digest exactly."""
    return bool(pinned) and _SHA256.fullmatch(pinned or "") is not None and (
        evidence_contract_sha256(contract) == pinned
    )


def _deadline_passed(deadline: float | None, now: float | None) -> bool:
    if deadline is None:
        return False
    return (time.time() if now is None else now) >= deadline


def _field_texts(recommendation: RecommendationCandidate, fields: Sequence[str]) -> list[str]:
    texts: list[str] = []
    for field in fields:
        value = getattr(recommendation, field)
        if isinstance(value, str):
            texts.append(value)
        else:
            texts.extend(str(item) for item in value)
    return texts


def _longest_base64_run(text: str) -> int:
    longest = current = 0
    for character in text:
        current = current + 1 if character in _BASE64_ALPHABET else 0
        longest = max(longest, current)
    return longest
