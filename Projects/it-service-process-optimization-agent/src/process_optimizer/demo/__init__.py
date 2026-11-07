"""Local-only, MOCK-labeled Phase 8A demonstration launcher and dashboard.

Nothing in this package calls Azure, invokes a paid model, sends an external
notification, or is reachable from the production `process_optimizer.main`
application. It only orchestrates already-implemented components (the
deterministic mock agent, the simulation engine, and the durable workflow
service) against an isolated local database.
"""
