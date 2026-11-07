# Data governance

## Canonical source

The canonical source is the
[BPI Challenge 2013 Incidents dataset](https://data.4tu.nl/datasets/0fc5c579-e544-4fab-9143-fab1f5192432)
published by 4TU.ResearchData. The repository does not track the XES file, derived
Parquet records, or any fabricated sample presented as real data.

The Phase 2 pipeline operates on a user-provided, unmodified local copy. Its SHA-256,
filename, canonical URL, processing time, library versions, transformations,
discovered schema, and validation results are recorded locally.

Canonical landing-page metadata was checked on 2026-10-15. It identifies Version 1,
DOI `10.4121/uuid:500573e6-accc-4b0c-9576-aa5468b10cee`, and the
[4TU General Terms of Use](https://doi.org/10.4121/resource:terms_of_use). The local
file is 1,322,247 bytes and its MD5 `d4809bd55e3e1c15b017ab4e58228297`
matches the publisher's file listing. SHA-256 remains the pipeline's primary
integrity digest. Legal review is still required before redistribution or commercial
use; metadata discovery is not legal advice.

Publisher metadata does not establish the timezone semantics needed to interpret
business-local time. The XES values are timezone-aware, so the pipeline can preserve
their instants in UTC, but no working-time interpretation is inferred.

The generated schema profile and quality report are authoritative for the local
artifact's observed attributes, types, completeness, ordering, and counts. They do
not establish business semantics. Do not infer SLA targets, closure events,
working-time calendars, or active processing duration from field names alone.

## Handling controls

- Treat raw incident data as sensitive, read-only source material.
- Keep raw and processed content out of Git; `.gitkeep` only preserves directories.
- Store derived artifacts separately and attach provenance to every transformation.
- Never silently alter the raw file. Reprocessing creates a new versioned artifact.
- Minimize fields used for analysis and redact identifiers from logs, traces,
  prompts, examples, and test fixtures.
- Define access, retention, deletion, and incident-response controls before cloud
  storage is introduced.
- Send aggregate evidence to any Foundry-hosted model only after an approved data-flow and privacy review;
  prefer aggregated evidence over event-level content.
- Use synthetic fixtures clearly labeled as synthetic for automated tests.

## Local artifact classification

| Artifact | Classification | Git policy | Mutation policy |
|---|---|---|---|
| Original `.xes.gz` | Sensitive raw source | Ignored | Read-only; checksum before parsing |
| Cases/events Parquet | Sensitive derived records | Ignored | Rebuilt atomically from raw source |
| Schema profile | Aggregate metadata | Ignored with processed output | Rebuilt with pipeline run |
| Ingestion manifest | Provenance metadata | Ignored with processed output | Rebuilt with pipeline run |
| Quality report | Aggregate validation metadata | Ignored with processed output | Rebuilt by ingestion |
| Process-analysis tables | Aggregate variants, transitions, and handoffs | Ignored with processed output | Rebuilt deterministically from verified Parquet |
| Evidence package/report | Aggregate observations and limitations | Ignored with processed output | Rebuilt deterministically; no case/event/resource IDs |
| Evidence Contract V1 | Versioned aggregate interface with artifact hashes | Ignored with processed output | Frozen schema; version changes for semantic changes |
| Synthetic test fixtures | Non-production test data | Test code only | Must remain explicitly synthetic |

Generated IDs are deterministic hashes, not anonymization guarantees. The source
attributes remain present in Parquet and therefore require the same access controls
as the raw dataset. Console output and exceptions do not include attribute values.

Phases 3 and 3.5 load only the columns required for aggregate analysis and explicitly
excludes `org:resource`. Future agent-facing evidence contains aggregate activity or
group selectors, counts, distributions, baselines, and limitations. It must never
contain case identifiers, event identifiers, resource identifiers, or event rows.
Agent-accessible tools accept no raw identifiers or arbitrary paths, enforce bounded
results, and read only allow-listed precomputed aggregate artifacts.

Phase 5 simulation outputs are derived aggregate hypotheses. They contain stable
evidence references and assumptions but no record-level data. They inherit the
aggregate evidence classification and must not be relabelled as historical
observations, causal estimates, demonstrated improvements, or SLA results.

## Timestamp policy

Timezone-aware XES datetimes are converted to UTC while representing the same
instant. Timezone-naive datetimes are preserved and flagged because assigning a
timezone without publisher evidence would fabricate semantics. `event_index`
preserves original trace ordering and deterministically orders tied timestamps.

## Dependency licensing risk

The selected PM4Py distribution reports AGPLv3/community licensing terms at import
time. Before proprietary distribution or enterprise deployment, legal review must
confirm that the intended use and distribution model is compatible or that an
appropriate commercial license is obtained. This pipeline implementation does not
resolve that licensing decision.
