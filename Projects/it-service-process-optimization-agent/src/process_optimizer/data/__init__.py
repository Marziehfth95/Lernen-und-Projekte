"""Deterministic data ingestion and validation boundary."""

from process_optimizer.data.ingestion import (
    IngestionResult,
    ingest_xes,
    prepare_dataset,
    validate_processed,
    write_schema_profile,
)

__all__ = [
    "IngestionResult",
    "ingest_xes",
    "prepare_dataset",
    "validate_processed",
    "write_schema_profile",
]
