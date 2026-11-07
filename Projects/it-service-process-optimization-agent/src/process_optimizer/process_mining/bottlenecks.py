"""Transparent statistical rules for bottleneck candidate observations."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import pandas as pd

from process_optimizer.process_mining.constants import ANALYSIS_VERSION
from process_optimizer.process_mining.models import (
    BottleneckConfig,
    EvidencePackage,
    EvidenceReference,
    ProcessFinding,
    TimePeriod,
)


def detect_bottlenecks(
    directly_follows: pd.DataFrame,
    handoffs: pd.DataFrame,
    duration_baseline: dict[str, float | int | None],
    *,
    dataset_sha256: str,
    time_period: TimePeriod,
    config: BottleneckConfig,
) -> EvidencePackage:
    """Apply deterministic rules and return aggregate observations, never causes."""
    baseline_median = _numeric(duration_baseline.get("median"))
    baseline_p90 = _numeric(duration_baseline.get("p90"))
    baseline_count = int(duration_baseline.get("observation_count") or 0)
    findings: list[ProcessFinding] = []

    eligible_dfg = directly_follows.loc[
        directly_follows["observation_count"] >= config.minimum_observations
    ]
    for row in eligible_dfg.itertuples(index=False):
        median = _numeric(row.elapsed_seconds_median)
        p75 = _numeric(row.elapsed_seconds_p75)
        p90 = _numeric(row.elapsed_seconds_p90)
        median_ratio = _safe_ratio(median, baseline_median)
        p90_ratio = _safe_ratio(p90, baseline_p90)
        if (
            median_ratio < config.duration_median_ratio_threshold
            and p90_ratio < config.duration_p90_ratio_threshold
        ):
            continue
        ranking_score = max(
            median_ratio / config.duration_median_ratio_threshold,
            p90_ratio / config.duration_p90_ratio_threshold,
        ) * math.log1p(int(row.observation_count))
        step = f"{row.source_activity} -> {row.target_activity}"
        findings.append(
            ProcessFinding(
                finding_id=_finding_id(
                    dataset_sha256,
                    "high_duration_transition",
                    step,
                    config,
                ),
                metric_name="high_duration_directly_follows_transition",
                metric_definition=(
                    "Consecutive source-ordered events whose median or p90 elapsed time "
                    "exceeds the configured multiple of the all-transition baseline."
                ),
                affected_process_step=step,
                affected_population="all cases containing the directly-follows transition",
                observation_count=int(row.observation_count),
                denominator=baseline_count,
                measured_values={
                    "elapsed_seconds_median": median,
                    "elapsed_seconds_p75": p75,
                    "elapsed_seconds_p90": p90,
                    "median_to_baseline_ratio": median_ratio,
                    "p90_to_baseline_ratio": p90_ratio,
                },
                comparison_baseline={
                    "name": "all non-negative consecutive-event elapsed times",
                    "elapsed_seconds_median": baseline_median,
                    "elapsed_seconds_p90": baseline_p90,
                },
                time_period=time_period,
                evidence_references=[
                    EvidenceReference(
                        artifact="directly_follows.parquet",
                        metric="elapsed-time distribution",
                        selector={
                            "source_activity": str(row.source_activity),
                            "target_activity": str(row.target_activity),
                        },
                    )
                ],
                limitations=[
                    "Elapsed time includes all observed waiting and inactive time.",
                    "No business calendar, SLA, or active-work duration is available.",
                    "The observation is not evidence of a root cause.",
                    (
                        "Median, p75, p90, count, and baseline ratios are reported "
                        "because durations are skewed."
                    ),
                ],
                ranking_score=round(ranking_score, 6),
            )
        )

    eligible_handoffs = handoffs.loc[
        handoffs["observation_count"] >= config.minimum_observations
    ]
    handoff_count_baseline = (
        float(eligible_handoffs["observation_count"].median())
        if not eligible_handoffs.empty
        else 0.0
    )
    total_handoffs = int(handoffs["observation_count"].sum()) if not handoffs.empty else 0
    for row in eligible_handoffs.itertuples(index=False):
        source_share = float(row.source_handoff_share)
        repeat_rate = float(row.repeated_visit_rate)
        if (
            source_share < config.handoff_source_share_threshold
            and repeat_rate < config.repeated_visit_rate_threshold
        ):
            continue
        ranking_score = max(
            source_share / config.handoff_source_share_threshold,
            repeat_rate / config.repeated_visit_rate_threshold,
        ) * math.log1p(int(row.observation_count))
        step = f"{row.source_group} -> {row.target_group}"
        findings.append(
            ProcessFinding(
                finding_id=_finding_id(
                    dataset_sha256,
                    "high_frequency_handoff",
                    step,
                    config,
                ),
                metric_name="high_frequency_or_repeated_group_handoff",
                metric_definition=(
                    "A consecutive group change meeting the configured source-share or "
                    "return-to-visited-group rate threshold."
                ),
                affected_group=step,
                affected_population="events with classified consecutive org:group changes",
                observation_count=int(row.observation_count),
                denominator=total_handoffs,
                measured_values={
                    "source_handoff_share": source_share,
                    "repeated_visit_count": int(row.repeated_visit_count),
                    "repeated_visit_rate": repeat_rate,
                    "elapsed_seconds_median": _numeric(row.elapsed_seconds_median),
                    "elapsed_seconds_p75": _numeric(row.elapsed_seconds_p75),
                    "elapsed_seconds_p90": _numeric(row.elapsed_seconds_p90),
                },
                comparison_baseline={
                    "name": "median observation count across eligible handoff patterns",
                    "observation_count_median": handoff_count_baseline,
                    "configured_source_share_threshold": config.handoff_source_share_threshold,
                    "configured_repeated_visit_rate_threshold": (
                        config.repeated_visit_rate_threshold
                    ),
                },
                time_period=time_period,
                evidence_references=[
                    EvidenceReference(
                        artifact="handoff_analysis.parquet",
                        metric="group handoff frequency and repeated-visit rate",
                        selector={
                            "source_group": str(row.source_group),
                            "target_group": str(row.target_group),
                        },
                    )
                ],
                limitations=[
                    "Group changes are observed routing patterns, not confirmed inefficiency.",
                    "The dataset does not expose staffing, queue capacity, or assignment intent.",
                    "The observation is not evidence of a root cause.",
                    "Missing group values are excluded rather than imputed.",
                ],
                ranking_score=round(ranking_score, 6),
            )
        )

    findings.sort(key=lambda finding: (-finding.ranking_score, finding.finding_id))
    findings = findings[: config.maximum_findings]
    return EvidencePackage(
        analysis_version=ANALYSIS_VERSION,
        dataset_sha256=dataset_sha256,
        detector_config=config,
        baselines={
            "transition_duration_observation_count": baseline_count,
            "transition_elapsed_seconds_median": baseline_median,
            "transition_elapsed_seconds_p90": baseline_p90,
            "eligible_handoff_pattern_count_median": handoff_count_baseline,
        },
        insufficient_sample_summary={
            "directly_follows_patterns_below_minimum": int(
                (directly_follows["observation_count"] < config.minimum_observations).sum()
            ),
            "handoff_patterns_below_minimum": int(
                (handoffs["observation_count"] < config.minimum_observations).sum()
            ),
        },
        findings=findings,
    )


def _finding_id(
    dataset_sha256: str,
    finding_type: str,
    affected_item: str,
    config: BottleneckConfig,
) -> str:
    payload: dict[str, Any] = {
        "dataset_sha256": dataset_sha256,
        "finding_type": finding_type,
        "affected_item": affected_item,
        "config": config.model_dump(mode="json"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"finding_{hashlib.sha256(encoded).hexdigest()[:24]}"


def _numeric(value: Any) -> float:
    if value is None or pd.isna(value):
        return 0.0
    return round(float(value), 6)


def _safe_ratio(value: float, baseline: float) -> float:
    if baseline <= 0:
        return 0.0
    return round(value / baseline, 6)
