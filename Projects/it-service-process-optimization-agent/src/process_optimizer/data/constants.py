"""Constants for the deterministic Phase 2 data pipeline."""

from pathlib import Path

PIPELINE_VERSION = "0.2.0"
SOURCE_URL = "https://data.4tu.nl/datasets/0fc5c579-e544-4fab-9143-fab1f5192432"
DEFAULT_INPUT_PATH = Path("data/raw/BPI_Challenge_2013_incidents.xes.gz")
DEFAULT_OUTPUT_DIR = Path("data/processed")

REFERENCE_CASE_COUNT = 7_554
REFERENCE_EVENT_COUNT = 65_533

CASE_ID_COLUMN = "case_id"
EVENT_ID_COLUMN = "event_id"
EVENT_INDEX_COLUMN = "event_index"
TRACE_INDEX_COLUMN = "source_trace_index"
EVENT_COUNT_COLUMN = "event_count"

CASE_ATTRIBUTE_PREFIX = "original_case__"
EVENT_ATTRIBUTE_PREFIX = "original_event__"
XES_CASE_IDENTIFIER = "concept:name"
XES_ACTIVITY_ATTRIBUTE = "concept:name"
XES_TIMESTAMP_ATTRIBUTE = "time:timestamp"

