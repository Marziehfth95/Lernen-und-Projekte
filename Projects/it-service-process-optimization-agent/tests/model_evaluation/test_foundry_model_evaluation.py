"""Explicitly opt-in real-model evaluation; never runs under the default test command."""

from __future__ import annotations

import os

import pytest

from process_optimizer.agents.provider import FoundryModelProvider
from process_optimizer.evaluation.runner import AgentEvaluationRunner
from process_optimizer.process_mining.constants import DEFAULT_ANALYSIS_DIR

pytestmark = [
    pytest.mark.model,
    pytest.mark.skipif(
        not (
            os.environ.get("ALLOW_PAID_MODEL_CALLS") == "true"
            and os.environ.get("RUN_REAL_MODEL_EVALS") == "true"
        ),
        reason="requires two explicit paid real-model evaluation opt-ins",
    ),
]


def test_opt_in_foundry_model_evaluation_records_metadata() -> None:
    report = AgentEvaluationRunner(DEFAULT_ANALYSIS_DIR, FoundryModelProvider()).run()

    assert report.execution_mode == "real_model"
    assert report.model_deployment == os.environ["FOUNDRY_MODEL_NAME"]
    assert report.invocation_records
