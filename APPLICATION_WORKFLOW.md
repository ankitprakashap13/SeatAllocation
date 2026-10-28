# Application Creation Workflow

## Goal

Deliver a complete application from `requirements.md` using a test-driven loop between a QA analyst, two independent developers, an orchestrator, and an integration QA engineer. Keep the human in the loop for decisions that affect product behavior or cannot be resolved from the requirements.

## Roles

### Orchestrator (driver)

- Read `requirements.md`, inspect the existing repository, and break requirements into independently verifiable features.
- Coordinate the QA analyst, frontend developer, backend developer, and integration QA engineer.
- Keep the work moving through the stages below; maintain the shared status and decision log.
- Integrate the frontend and backend work, resolve interface mismatches, and route concrete failures back to the responsible developer.
- Ask the human only when requirements are ambiguous, conflicting, incomplete for a consequential choice, or when a decision needs product approval. Ask in the visible terminal/chat and wait for the answer before proceeding on that dependent decision.
- Do not claim completion until all acceptance criteria pass and integration QA has delivered its final report.

### QA analyst (requirements and test cases)

- Start from `requirements.md`; do not invent product behavior. Flag gaps as questions for the orchestrator to take to the human.
- Write use cases, acceptance criteria, and test cases before implementation.
- Cover happy paths, validation and error paths, permissions where relevant, and important boundary conditions.
- Add each case to `reports/qa-report.md` with a stable ID, requirement link, steps or inputs, expected result, and status (`Not run`, `Pass`, `Fail`, or `Blocked`).
- Hand the agreed test plan to both developers. For test-driven development, make the relevant automated tests available before the implementation work begins.

### Frontend developer (independent)

- Own the client application and frontend tests. Work from the requirements, QA use cases, and agreed API contract.
- Write or update a failing test first, implement the smallest change that makes it pass, then refactor while keeping tests green.
- Work on frontend files and report changed files, tests run, results, assumptions, and any blockers to the orchestrator.
- Do not silently change API behavior or requirements; raise contract questions to the orchestrator.

### Backend developer (independent)

- Own server-side behavior, data access, API contracts, and backend tests. Work from the requirements, QA use cases, and agreed API contract.
- Write or update a failing test first, implement the smallest change that makes it pass, then refactor while keeping tests green.
- Work on backend files and report changed files, tests run, results, assumptions, and any blockers to the orchestrator.
- Document the API contract and any setup or migration steps for integration. Raise requirement questions rather than guessing.

### Integration QA engineer (end-to-end)

- Begin after the orchestrator has integrated a runnable frontend and backend build.
- Execute the approved end-to-end scenarios against the integrated application, including cross-service behavior, error handling, and critical regressions.
- Add reproducible steps, environment/build details, actual and expected results, severity, evidence or logs where available, and status to `reports/qa-report.md`.
- Route failures to the orchestrator with enough detail to reproduce. Re-test fixes and update the original case; add regression coverage where appropriate.
- Publish a final report listing every planned test case and its result, plus any known limitations and blocked cases.

## Workflow and handoffs

1. **Intake:** The orchestrator reads `requirements.md` and repository guidance, records the application scope, identifies the run/build/test commands, and lists unclear requirements.
2. **Clarify:** The orchestrator presents consequential open questions in the visible terminal/chat. The human answers before affected work proceeds. Non-blocking details can be recorded as explicit assumptions and reviewed with the human.
3. **QA plan first:** The QA analyst writes use cases, acceptance criteria, and test cases into `reports/qa-report.md`. The orchestrator checks coverage against every requirement and confirms the plan is ready.
4. **Agree on interfaces:** The orchestrator establishes shared frontend/backend contracts, such as routes, request/response shapes, validation rules, and error behavior. Both developers receive the same approved contract and QA plan.
5. **Parallel TDD implementation:** The orchestrator assigns frontend work to the frontend developer and backend work to the backend developer. They work independently, each following red → green → refactor and reporting progress and blockers. Changes are integrated through the repository's normal branch/patch process; avoid overlapping ownership of files.
6. **Integration:** The orchestrator combines the changes, resolves merge and contract issues, runs the project's documented checks, and reviews the app against the requirements. A failing unit/component/contract test goes back to its owning developer with a reproducible failure and the relevant case ID.
7. **End-to-end QA:** Once the integrated build is runnable, integration QA runs the approved E2E plan. A failure goes back through the orchestrator to the responsible developer; QA re-tests the fix and records the result. New requirement ambiguity goes to the human through the orchestrator.
8. **Completion:** The orchestrator verifies that all acceptance criteria and planned cases have a terminal result, all required fixes are integrated, and no critical/high-severity defect remains unresolved. Integration QA publishes the complete final test report. The orchestrator summarizes delivered behavior, checks run, report location, and any accepted limitations to the human.

## Routing rules

| Finding | Route |
|---|---|
| Requirement is unclear, contradictory, or lacks a product decision | Orchestrator → human; pause dependent work |
| Frontend unit/component test fails | Orchestrator → frontend developer |
| Backend unit/API/data test fails | Orchestrator → backend developer |
| Frontend/backend contract mismatch | Orchestrator assigns the owning change; update the shared contract and tests |
| Integrated E2E case fails | Integration QA → orchestrator → responsible developer(s) → integration QA retest |
| Test is blocked by environment or missing access | QA records blocker; orchestrator resolves setup or asks human for access/decision |
| All acceptance criteria pass and QA report is complete | Orchestrator → human completion summary |

## Shared status and report

Keep `reports/qa-report.md` current throughout the workflow. Use this format:

```markdown
# QA Report

## Scope and build
- Requirements source: `requirements.md`
- Build/commit:
- Environment:
- Run instructions:

## Acceptance and test cases
| ID | Requirement | Type | Scenario / steps | Expected result | Actual result | Status | Defect / evidence |
|---|---|---|---|---|---|---|---|

## Defects and retests
| Defect | Severity | Owner | Fix/build | Retest result |
|---|---|---|---|---|

## Blocked cases and limitations

## Final QA assessment
```

Statuses: `Not run`, `Pass`, `Fail`, `Blocked`. Do not mark a case `Pass` without recording the observed result. Keep failed cases visible after a fix and record the retest against the same ID.

## Human-in-the-loop terminal behavior

- The orchestrator is the only role that asks the human for a decision.
- Make questions specific: include the requirement or case ID, the ambiguity, the available choices, and the impact of each choice.
- Ask in the visible terminal/chat, label it `Human decision required`, and wait for a response before making the dependent product decision.
- Record the question, answer, and resulting requirement/test update in the shared status or QA report.
- Do not ask the human to resolve routine implementation details that are already determined by requirements, repository conventions, or the agreed contract.
