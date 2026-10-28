# Seat Allocation — Skills and Tools Inventory

## Needed engineering skills

- REST API and HTTP status design.
- Relational database transactions, row locking, and unique constraints.
- Distributed concurrency and idempotency.
- WebSocket/SSE event delivery and reconnect handling.
- Accessible grid UI design.
- Automated unit, integration, contract, and race-condition testing.
- Observability: logs, metrics, traces, and correlation IDs.

## Suggested implementation choices (not final)

- Backend: framework with explicit transaction support (for example Node.js/TypeScript, Java, Go, or .NET).
- Database: PostgreSQL or another relational store with conditional uniqueness/locking.
- Frontend: React/TypeScript or equivalent component framework.
- Realtime: SSE for server-to-client seat updates; WebSocket if future bidirectional needs justify it.
- Tests: API contract tests plus an integration harness that runs concurrent hold requests.

## Decision rule

Choose tools that make atomic seat ownership and reliable expiry easy to test and operate; avoid adding infrastructure before traffic or product requirements justify it.
