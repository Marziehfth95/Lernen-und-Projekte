"""Synthetic-only XES fixtures and test-wide safety defaults.

These fixtures are not BPI Challenge records and must not be used as empirical
process evidence.
"""

from __future__ import annotations

import gzip
import os
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree

import pytest

# Optional hosting SDKs must never export telemetry during local deterministic tests.
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

type XesValue = tuple[str, str]
type XesAttributes = dict[str, XesValue]
type SyntheticCase = tuple[XesAttributes, list[XesAttributes]]
type XesFactory = Callable[[list[SyntheticCase], str], Path]

XES_NAMESPACE = "http://www.xes-standard.org/"


@pytest.fixture
def xes_factory(tmp_path: Path) -> XesFactory:
    """Write a minimal explicitly synthetic XES.GZ fixture."""

    def factory(cases: list[SyntheticCase], filename: str = "synthetic.xes.gz") -> Path:
        ElementTree.register_namespace("", XES_NAMESPACE)
        log = ElementTree.Element(f"{{{XES_NAMESPACE}}}log", {"xes.version": "1.0"})
        for case_attributes, events in cases:
            trace = ElementTree.SubElement(log, f"{{{XES_NAMESPACE}}}trace")
            _append_attributes(trace, case_attributes)
            for event_attributes in events:
                event = ElementTree.SubElement(trace, f"{{{XES_NAMESPACE}}}event")
                _append_attributes(event, event_attributes)

        path = tmp_path / filename
        with gzip.GzipFile(filename=path, mode="wb", mtime=0) as stream:
            ElementTree.ElementTree(log).write(stream, encoding="utf-8", xml_declaration=True)
        return path

    return factory


def _append_attributes(parent: ElementTree.Element, attributes: XesAttributes) -> None:
    for key, (xes_type, value) in attributes.items():
        ElementTree.SubElement(
            parent,
            f"{{{XES_NAMESPACE}}}{xes_type}",
            {"key": key, "value": value},
        )


@pytest.fixture
def valid_synthetic_cases() -> list[SyntheticCase]:
    """Two cases with three synthetic events and a deliberate timestamp tie."""
    return [
        (
            {"concept:name": ("string", "SYNTHETIC-CASE-1")},
            [
                {
                    "concept:name": ("string", "Synthetic open"),
                    "time:timestamp": ("date", "2020-01-01T09:00:00+01:00"),
                    "synthetic:priority": ("int", "1"),
                },
                {
                    "concept:name": ("string", "Synthetic assign"),
                    "time:timestamp": ("date", "2020-01-01T09:00:00+01:00"),
                    "synthetic:priority": ("int", "1"),
                },
            ],
        ),
        (
            {"concept:name": ("string", "SYNTHETIC-CASE-2")},
            [
                {
                    "concept:name": ("string", "Synthetic close"),
                    "time:timestamp": ("date", "2020-01-02T10:00:00Z"),
                    "synthetic:priority": ("int", "2"),
                }
            ],
        ),
    ]
