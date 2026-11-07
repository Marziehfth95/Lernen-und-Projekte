"""Verified columns and defaults for deterministic analytics through Phase 3.5."""

from pathlib import Path

ANALYSIS_VERSION = "0.3.5"
DEFAULT_PHASE2_DIR = Path("data/processed")
DEFAULT_ANALYSIS_DIR = Path("data/processed/process_mining")

CASE_ID = "case_id"
EVENT_ID = "event_id"
EVENT_INDEX = "event_index"
ACTIVITY = "original_event__concept:name"
LIFECYCLE = "original_event__lifecycle:transition"
TIMESTAMP = "original_event__time:timestamp"
GROUP = "original_event__org:group"
RESOURCE = "original_event__org:resource"
PRODUCT = "original_event__product"
ORGANIZATION_INVOLVED = "original_event__organization involved"
IMPACT = "original_event__impact"
ORGANIZATION_COUNTRY = "original_event__organization country"

COMPARATIVE_DIMENSIONS = (
    PRODUCT,
    ORGANIZATION_INVOLVED,
    IMPACT,
    ORGANIZATION_COUNTRY,
)

REQUIRED_CASE_COLUMNS = (CASE_ID, "event_count")
REQUIRED_EVENT_COLUMNS = (
    EVENT_ID,
    CASE_ID,
    EVENT_INDEX,
    ACTIVITY,
    LIFECYCLE,
    TIMESTAMP,
    GROUP,
    PRODUCT,
    ORGANIZATION_INVOLVED,
)
