"""Schema discovery without exposing event attribute values."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pyarrow as pa

from process_optimizer.data.constants import (
    CASE_ATTRIBUTE_PREFIX,
    EVENT_ATTRIBUTE_PREFIX,
)

SUPPORTED_SCALAR_TYPES = (str, bool, int, float, bytes, date, datetime)


def python_type_name(value: Any) -> str:
    """Return a stable, non-value-bearing source type name."""
    value_type = type(value)
    return f"{value_type.__module__}.{value_type.__qualname__}"


@dataclass
class AttributeObservation:
    """Aggregate source and transformation metadata for one XES attribute."""

    source_name: str
    output_column: str
    present_count: int = 0
    null_count: int = 0
    source_types: set[str] = field(default_factory=set)
    storage_types: set[str] = field(default_factory=set)
    transformations: set[str] = field(default_factory=set)
    unexpected_types: set[str] = field(default_factory=set)
    timezone_semantics: set[str] = field(default_factory=set)

    def observe(self, source_value: Any, storage_value: Any, transformation: str) -> None:
        self.present_count += 1
        if source_value is None:
            self.null_count += 1
            return

        source_type = python_type_name(source_value)
        self.source_types.add(source_type)
        self.storage_types.add(python_type_name(storage_value))
        self.transformations.add(transformation)
        if not isinstance(source_value, SUPPORTED_SCALAR_TYPES):
            self.unexpected_types.add(source_type)

        if isinstance(source_value, datetime):
            if source_value.tzinfo is None or source_value.utcoffset() is None:
                self.timezone_semantics.add("timezone_naive_source")
            else:
                self.timezone_semantics.add("timezone_aware_source")


class SchemaProfiler:
    """Collect schema observations while event rows are materialized."""

    def __init__(self) -> None:
        self._attributes: dict[str, dict[str, AttributeObservation]] = {
            "case": {},
            "event": {},
        }
        self._record_counts: defaultdict[str, int] = defaultdict(int)

    def begin_record(self, scope: str) -> None:
        self._record_counts[scope] += 1

    def observe(
        self,
        scope: str,
        source_name: str,
        source_value: Any,
        storage_value: Any,
        transformation: str,
    ) -> None:
        prefix = CASE_ATTRIBUTE_PREFIX if scope == "case" else EVENT_ATTRIBUTE_PREFIX
        observation = self._attributes[scope].setdefault(
            source_name,
            AttributeObservation(
                source_name=source_name,
                output_column=f"{prefix}{source_name}",
            ),
        )
        observation.observe(source_value, storage_value, transformation)

    def to_dict(self, case_schema: pa.Schema, event_schema: pa.Schema) -> dict[str, Any]:
        return {
            "format_version": 1,
            "field_naming": {
                "original_case_attributes": f"{CASE_ATTRIBUTE_PREFIX}<XES key>",
                "original_event_attributes": f"{EVENT_ATTRIBUTE_PREFIX}<XES key>",
                "derived_fields": [
                    "case_id",
                    "event_id",
                    "source_trace_index",
                    "event_index",
                    "event_count",
                ],
            },
            "case": self._scope_profile("case", case_schema),
            "event": self._scope_profile("event", event_schema),
        }

    def _scope_profile(self, scope: str, arrow_schema: pa.Schema) -> dict[str, Any]:
        total = self._record_counts[scope]
        fields = {field.name: str(field.type) for field in arrow_schema}
        attributes: dict[str, Any] = {}
        for source_name, item in sorted(self._attributes[scope].items()):
            implicit_missing = total - item.present_count
            missing_count = implicit_missing + item.null_count
            attributes[source_name] = {
                "output_column": item.output_column,
                "source_types": sorted(item.source_types),
                "storage_python_types": sorted(item.storage_types),
                "parquet_type": fields.get(item.output_column),
                "present_count": item.present_count,
                "missing_count": missing_count,
                "missing_percentage": round((missing_count / total * 100) if total else 0.0, 6),
                "transformations": sorted(item.transformations),
                "timezone_semantics": sorted(item.timezone_semantics),
                "unexpected_types": sorted(item.unexpected_types),
            }
        return {
            "record_count": total,
            "attributes": attributes,
            "parquet_schema": fields,
        }

