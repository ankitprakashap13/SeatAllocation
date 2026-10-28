# Seat Allocation — Agent Guidance

This file is intentionally named `agenets.md` to match the requested filename.

## Working agreement

- Read `requirement.md` and the API/flow drafts before changing behavior.
- Keep booking correctness and transaction boundaries explicit in code reviews.
- Prefer small, testable changes; update requirements when assumptions change.
- Do not invent payment, identity, pricing, or venue behavior without recording it as an assumption.
- Every concurrency-related change needs a test for two competing requests.

## Suggested roles

- **Backend agent:** domain model, transactional hold/confirm APIs, expiry, constraints, tests.
- **Frontend agent:** seat grid, accessibility, countdown, real-time reconciliation, tests.
- **QA/review agent:** race-condition tests, API contract checks, failure/reconnect scenarios.
- **Documentation agent:** keep requirements, API examples, diagrams, and gotchas synchronized.

## Handoff format

Each change should state: intent, files changed, assumptions, tests run, known risks, and follow-up decisions.
