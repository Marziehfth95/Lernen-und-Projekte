# Automation architecture

The Phase 6 automation is deliberately outside LangGraph:

```text
Foundry-hosted LangGraph (future) -- aggregate result only --> workflow API
authenticated analyst -----------> deterministic revalidation + persistence
authenticated process owner -----> approval decision
workflow service ----------------> local improvement-ticket adapter
workflow transaction ------------> outbox + business audit
```

The local executor persists one improvement ticket. It does not update historical
BPI13 incidents, routing configuration, or any external ITSM system. Its execution key
is derived from the approval and immutable recommendation hash, and database uniqueness
prevents duplicate tickets during retries or concurrent attempts.

Notifications use a transactional outbox with structured aggregate-only payloads.
The safe demo adapter records local delivery. A Microsoft Teams adapter is represented
only as a port: there are no Teams credentials and no external delivery in Phase 6.
Attempts, safe error codes, and successful delivery times are persisted. `DELIVERED`
is assigned only after the configured adapter returns successfully.

The SQLAlchemy application boundary can later target an approved Azure relational
database. Selection requires an ADR covering concurrency, availability, retention,
backup, encryption, private networking, identity and compliance.
