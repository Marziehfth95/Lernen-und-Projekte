# Ingestion pipeline

## Purpose

Phase 2 converts the immutable BPI Challenge 2013 Incidents XES.GZ artifact into
reproducible, columnar case and event tables. It profiles and validates the log but
does not perform process discovery or assert business conclusions.

## Operations

```bash
process-optimizer-profile \
  --input data/raw/BPI_Challenge_2013_incidents.xes.gz \
  --output-dir data/processed

process-optimizer-ingest \
  --input data/raw/BPI_Challenge_2013_incidents.xes.gz \
  --output-dir data/processed

process-optimizer-validate \
  --input data/raw/BPI_Challenge_2013_incidents.xes.gz \
  --output-dir data/processed
```

`profile` parses and writes aggregate schema metadata. `ingest` performs the full
conversion and validation. `validate` re-parses the source, validates existing
Parquet outputs, and checks them against manifest hashes without modifying the
governed artifacts. Missing input, invalid gzip, parse failure, or missing processed
files returns a non-zero exit code with an explicit error.

## Deterministic transformation

1. Confirm the file exists and read the complete gzip stream to validate its CRC.
2. Compute SHA-256 over the original compressed bytes.
3. Parse the XES.GZ with PM4Py as a legacy `EventLog`, retaining trace attributes.
4. Discover the union of trace and event attributes dynamically.
5. Prefix trace fields with `original_case__` and event fields with
   `original_event__`; the suffix remains the exact XES attribute key.
6. Convert timezone-aware datetimes to UTC. Preserve timezone-naive datetimes and
   flag their semantics as unknown.
7. Create stable IDs from the source checksum, source position, and canonicalized
   original attributes. IDs are stable for the same immutable source bytes and
   pipeline algorithm; they are not a cross-dataset master key.
8. Preserve trace order using `source_trace_index` and within-trace order using
   `event_index`. Timestamp ties are never reordered or removed.
9. Write PyArrow Parquet with Zstandard compression, then atomically replace each
   target. The raw XES.GZ is never opened for writing.

## Field contract

| Field | Table | Origin | Meaning |
|---|---|---|---|
| `case_id` | cases/events | Derived | Stable SHA-256-based case key |
| `event_id` | events | Derived | Stable SHA-256-based event key |
| `source_trace_index` | cases | Derived | Zero-based trace position in the XES log |
| `event_index` | events | Derived | Zero-based source position within its trace |
| `event_count` | cases | Derived | Number of source events in the trace |
| `original_case__<key>` | cases | Original | Trace attribute discovered from XES |
| `original_event__<key>` | events | Original | Event attribute discovered from XES |

No fallback activity, timestamp, case identifier, SLA, lifecycle, closure, calendar,
or duration field is invented. Missing standard XES attributes remain missing and
are reported.

## Validation contract

The report covers input existence, gzip integrity, XES parse success, generated and
source identifier quality, event-to-case integrity, missing activities/timestamps,
fully duplicated events, within-case timestamp ties, chronological source order,
unexpected or inconsistent types, per-attribute null percentages, incomplete
zero-event cases, and source/output row reconciliation.

The published counts of approximately 7,554 cases and 65,533 events are comparison
references only. Discrepancies produce a warning and are never corrected by adding
or deleting data.

## Failure and privacy behavior

Artifacts are written only under the configured output directory. Partial files
use temporary names and are atomically replaced. Logs and command output contain
paths, aggregate counts, checksums, and statuses only; attribute values are not
printed. JSON metadata includes field names and type/count statistics but no event
values.
