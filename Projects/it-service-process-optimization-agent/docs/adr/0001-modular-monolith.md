# ADR 0001: Start with a modular monolith

- Status: Accepted
- Date: 2026-10-15

## Context

The MVP needs clear enterprise boundaries without the delivery and
operational cost of distributed services. Analytics, LLM reasoning, approvals,
persistence, and observability have different trust and testing characteristics.

## Decision

Build one Python 3.12 FastAPI deployable using a `src` layout and explicit modules.
Keep deterministic process mining separate from probabilistic agent reasoning.
Expose persistence and side effects through typed application ports. Start with a
local SQLite adapter only when persistence is introduced. Decide cloud persistence
later using documented scale, compliance, resilience, and cost requirements.

## Consequences

- Local development and testing remain simple and require no Azure credentials.
- A single container can be deployed and observed as one unit.
- Boundaries must be enforced by code review and automated tests.
- Independent scaling and fault isolation are limited initially.
- Modules can be extracted later if measured load or governance needs justify it.

## Rejected alternatives

- Microservices now: unjustified complexity for the MVP.
- A single undifferentiated module: weakens safety and testability.
- Selecting a cloud database now: requirements needed for a responsible choice are
  not yet validated.

