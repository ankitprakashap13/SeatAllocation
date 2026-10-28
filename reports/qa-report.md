# Seat Allocation — QA Plan and Running Report

## Scope and assumptions

- Requirements source: `requirement.md` (draft 0.1), supported by `docs/api-spec.md`, `docs/flow-diagrams.md`, `GOTCHAS.md`, and `DOS_AND_DONTS.md`.
- Application: phase-one movie/concert seat reservation prototype.
- Payment, production authentication/authorization, and rate limiting are out of scope. The browser display name is presentation metadata only and never proves ownership.
- Hold length: 120 seconds. Time is UTC and the server is authoritative.
- Test fixture: session `SESSION-001` has 50 seats across rows A–E; three demo visitor tokens own 3, 5, and 7 seeded seats; additional visitors and a controllable server clock cover conflicts and expiry.
- Build / commit: local workspace implementation; no commit recorded.
- Environment: Windows, Python 3.14, stdlib `unittest`, isolated temporary SQLite database, in-process HTTP server, injectable UTC clock.
- Run instructions: `python -m unittest discover -s tests -v`; start the app with `python -m app.server --host 127.0.0.1 --port 8000 --db data/seat-allocation.sqlite3`.

## Test-first handoff contract

1. Treat the cases below as acceptance criteria. Implement the smallest testable vertical slice while preserving the documented API response envelope and error codes.
2. Backend starts with domain/API tests for validation, atomic all-or-nothing holds, expiry-at-write-time, idempotency, confirmation, and two-request races. Use an injectable clock and a persistent transaction/conditional-write boundary; never rely on a cleanup timer for correctness.
3. Frontend starts with component tests for accessible seat states, keyboard operation, visitor-name session storage, authoritative hold timer, stale-state refresh, and conflict handling. Mock only the API/event boundary.
4. Integration QA uses two independent clients against one running backend and runs the E2E cases after both implementations are integrated. Include one simultaneous hold race and one simultaneous confirmation retry.
5. Every failing test or manual case records the stable case ID here. Developers return the case IDs, changed files, test command, result, and known gaps. QA retests the same IDs after fixes and adds regressions where needed.
6. A test is `Pass` only after observing the expected result. Failed cases remain visible after fixes, with the retest result and build recorded. Do not release with unresolved critical/high-severity failures.

### Backend test seam and API expectations

The stdlib suite in `tests/test_seat_api.py` is deliberately written before the backend. Its proposed seam is `app.server.make_server(host, port, db_path, clock)`, returning a `ThreadingHTTPServer`; `clock` is a zero-argument callable returning an aware UTC `datetime`. The module should seed `SESSION-001` in an isolated SQLite database at `db_path`. The test starts the server on port `0`, exercises documented HTTP paths with only `unittest`/`urllib`, then shuts it down. This can be adapted if implementation chooses another simple injectable seam, while preserving the tested behavior.

Guest hold ownership must be distinct from the display name. For prototype requests, use `X-Client-Token` (or consistently accept the API draft's `clientToken` request field) as an unguessable visitor token; ownership checks for hold release/confirmation must require that token. Seat-map responses may return only `ownership: self|other` and `heldUntil`, never the token or display name. Idempotency keys are scoped to the visitor and operation; reusing one with a different request body must not silently change the original operation.

Run after the backend module exists: `python -m unittest discover -s tests -v`. Before then the expected initial red result is an import failure for `app.server`; that is an implementation handoff state, not a passing test run.

## Acceptance and test cases

Statuses start as `Not run`. Types: **UI** (component/accessibility), **API** (unit/API/contract), **E2E** (integrated browser/API), **Ops** (logging/metrics).

| ID | Requirement | Type | Scenario / steps or input | Expected result | Actual result | Status | Defect / evidence |
|---|---|---|---|---|---|---|---|
| T01 | §11 visitor name | E2E | Clear session storage and open app | Visitor name prompt is shown before booking actions | — | Not run | — |
| T02 | §11 visitor name | UI | Enter “Alex”; inspect `seatAllocation.displayName` | Exact name stored in tab session storage | — | Not run | — |
| T03 | §11 visitor name | E2E | Reload in same browser session | No second prompt; stored name is reused | — | Not run | — |
| T04 | §11 visitor name | UI | Edit then clear visitor name | UI and session storage update; clear returns to prompt state | — | Not run | — |
| T05 | §3, §11 ownership | API | Change display name / use same name from second client | Name is never used as identity or hold ownership credential | Different visitor tokens could not reuse a held seat; display name is not part of the API ownership contract | Pass | `tests/test_seat_api.py::test_display_name_is_not_required_or_used_as_ownership` |
| T06 | §4 seat map | E2E | Fetch and render session seat map | Session, row/seat labels, and authoritative states render | — | Not run | — |
| T07 | §4 accessibility | UI | Inspect all seat states and legend | Available, selected, self-held, other-held, booked, blocked are distinguishable by text/icon/label as well as color | — | Not run | — |
| T08 | §2 selection | UI | Select A-01 and A-02 | Both are selected; summary lists both and correct count | — | Not run | — |
| T09 | API validation | API | Submit duplicate and unknown seat IDs | Stable validation error; no partial hold or state mutation | Empty, duplicate, and unknown seat lists returned validation errors; suite passed | Pass | `tests/test_seat_api.py::test_duplicate_unknown_and_empty_seat_requests_are_rejected` |
| T10 | §4 contiguous preference | API/UI | Request 2 seats where contiguous and scattered groups exist | Suggest same-row/section contiguous pair (same tier when pricing exists); server still validates availability | — | Not run | — |
| T11 | §2 two-minute hold | E2E | Visitor A holds A-01 and A-02 | Success returns hold ID, both seats, active status, and server expiry ~120s ahead | API suite held two seats; HTTP smoke held A-01 and received `2030-01-01T12:02:00Z` from a clock at 12:00 UTC | Pass | `tests/test_seat_api.py::test_create_hold_returns_two_minute_server_expiry`; isolated HTTP smoke |
| T12 | §4 ownership states | E2E | View active hold in both visitor sessions | Owner sees held-by-me; other client sees held-by-other | Other client observed `state=held`, `ownership=other`; owner map view after hold was not separately checked | Not run | Two-client smoke only asserted the other-client view; next step: verify `ownership=self` with owner token |
| T13 | §11 blocked timer/privacy | E2E | Visitor B views A-01 held by A | Seat unavailable with countdown from server expiry; no holder name or identity leaks | B saw held state, `ownership=other`, UTC `heldUntil`; payload contained no client-A token | Pass | Isolated two-client HTTP smoke; countdown rendering itself not browser-verified |
| T14 | §5 server clock | E2E | Skew browser clock and reload a held seat | Remaining time is derived from server timestamp; zero triggers refetch; client never assumes availability | — | Not run | — |
| T15 | §4 release | E2E | A explicitly releases active hold | Release is idempotent; authoritative state/event makes seats available | HTTP DELETE returned 204 and subsequent map showed A-02 available | Pass | Isolated HTTP smoke; idempotent repeat release not separately checked |
| T16 | §4 expiry consistency | API | Advance server test clock beyond 120s; fetch/confirm hold | Expired hold cannot confirm even before cleanup; seats become available on fetch/event | At +121s confirmation returned 409 `HOLD_EXPIRED`; next map refetch showed A-03 available | Pass | `tests/test_seat_api.py::test_expired_hold_cannot_be_confirmed_and_seats_are_released`; isolated smoke |
| T17 | §7 atomic conflict | API | B attempts A-01 while A holds it | HTTP 409 `SEAT_UNAVAILABLE`, conflicting seat ID included; no partial hold | Competing request returned 409; A-02 in the mixed request remained available | Pass | `tests/test_seat_api.py::test_hold_is_atomic_when_one_seat_is_taken` |
| T18 | §4 same-owner retry | API | Retry same request with same idempotency key/client token | Same logical hold returned; no duplicate hold record | Repeated hold returned the same hold ID | Pass | `tests/test_seat_api.py::test_same_idempotency_key_returns_same_hold` |
| T19 | §7 concurrent ownership | API | Simultaneous A and B hold requests for A-03 | Exactly one succeeds; loser receives conflict; one active owner remains | Concurrent requests returned exactly 201 and 409 | Pass | `tests/test_seat_api.py::test_concurrent_hold_race_has_exactly_one_winner` |
| T20 | §7 confirmation | E2E | Confirm an active, unexpired hold | One booking created atomically; every held seat becomes booked | Confirm returned 201 confirmed booking; A-01 map state became booked; two-seat API test verified all held seats booked | Pass | Isolated HTTP smoke; `tests/test_seat_api.py::test_confirmation_is_idempotent_and_books_all_seats` |
| T21 | §7 expiry | API | Confirm hold after server-clock expiry | HTTP 409 `HOLD_EXPIRED`; no booking created | Confirmation at +121s returned 409 `HOLD_EXPIRED`; map showed seat available | Pass | Same expiry test and isolated HTTP smoke |
| T22 | §4 idempotent confirm | API | Simultaneously/repeatedly confirm one hold with same key | One booking exists; retry returns same result or documented `ALREADY_CONFIRMED` | — | Not run | — |
| T23 | §7 no double booking | API | B attempts to hold a booked seat | HTTP 409; existing booking remains unchanged | — | Not run | — |
| T24 | §7 all-or-nothing group | API | Request group with one unavailable seat | Entire request fails; no subset is held/booked | Request containing available A-02 plus held A-01 failed 409; A-02 stayed available | Pass | `tests/test_seat_api.py::test_hold_is_atomic_when_one_seat_is_taken` |
| T25 | §4 realtime hold | E2E | Hold seats with another client connected to event stream | `seat.state_changed` delivered with session version and held state | Live stream delivered `seat.state_changed`, version 2, A-01, held, and UTC heldUntil after the hold | Pass | Isolated HTTP smoke |
| T26 | §4 realtime transitions | E2E | Release, expire, and book held seats | State events delivered for each transition; clients converge to backend state | — | Not run | — |
| T27 | §7 event versions | UI | Deliver skipped/out-of-order version | Client detects version gap and refetches full seat map | — | Not run | — |
| T28 | §2 reconnect | E2E | Disconnect, change seat, reconnect | Client refetches and shows authoritative current state | — | Not run | — |
| T29 | §4 stale selection | E2E | B selects seat; A holds/books before B submits | B receives conflict, actionable notice, refreshed seat state; stale selection removed | — | Not run | — |
| T30 | API §1 | API | `GET /api/v1/sessions/{id}/seats` | Envelope includes session, monotonic version, 120s duration, seat states, UTC expiry, request ID | Endpoint returned session ID, version, 120-second duration, seats, and requestId; hold map exposed UTC heldUntil | Pass | `tests/test_seat_api.py::test_seat_map_returns_version_duration_utc_and_request_id`; isolated smoke |
| T31 | API §2 validation | API | Empty list, duplicate/unknown IDs, over-limit group | Stable validation codes; no writes or partial hold | Empty, duplicate, unknown, and nine-seat over-limit requests were rejected | Pass | `test_duplicate_unknown_and_empty_seat_requests_are_rejected`; `test_seven_seat_hold_is_supported_and_nine_is_rejected` |
| T32 | API §4 errors | API | Confirm unknown, expired, released, or already-confirmed hold | Documented 404/409 codes and safe idempotent result | — | Not run | — |
| T33 | §4 idempotency | API | Retry hold and confirmation after simulated timeout with same keys | No duplicate holds or bookings; same logical result returned | Hold retry reused hold ID; confirmation retry reused booking ID | Pass | `tests/test_seat_api.py::test_same_idempotency_key_returns_same_hold`; `test_confirmation_is_idempotent_and_books_all_seats` |
| T34 | §5 privacy | API | Inspect responses for another visitor's hold | Only ownership marker and expiry returned; no name/token/contact data | Other-client response exposed only `ownership=other` and expiry; secret visitor token absent | Pass | `tests/test_seat_api.py::test_other_clients_see_only_ownership_marker_and_expiry`; isolated smoke |
| T35 | §5 UTC | API/UI | Run under differing client time zones | API timestamps are ISO-8601 UTC; countdown remains correct | Hold API and SSE emitted `2030-01-01T12:02:00Z` from injected UTC clock; differing browser timezone not tested | Pass | UTC API timestamp verified; browser countdown remains unverified |
| T36 | §5 accessibility | UI | Use keyboard only to navigate/select seats | Every operable seat is reachable and usable without pointer | — | Not run | — |
| T37 | §5 screen readers | UI | Inspect accessible names for seats and held timers | Labels expose row, seat number, state, and remaining time when held | — | Not run | — |
| T38 | §5 non-color state | UI | View states in grayscale/high-contrast mode | State remains understandable through text/icon/pattern in addition to color | — | Not run | — |
| T39 | §4 conflict feedback | E2E | Submit a seat taken since last map load | Clear actionable conflict message and refreshed map shown | — | Not run | — |
| T40 | §5 observability | API/Ops | Make request and inspect response/logs | Response `requestId` matches structured server log correlation ID | API suite verified response requestId is present; structured log correlation match was not captured | Not run | Next step: configure/collect server logs and match response requestId |
| T41 | §5 observability | Ops | Exercise hold success/conflict, expiry, booking, reconnect | Structured logs or measurable counters cover each event category | — | Not run | — |
| T42 | §2 movie/concert support | E2E | Run core scenario on regular rows and irregular concert layout with blocked seats | Layout supports irregularity; blocked seats cannot be held; core flow still passes | — | Not run | — |
| T43 | §7 cross-session isolation | API | Hold a seat in session 1; request same venue seat in session 2 | Session 2 ownership is independent; no global-seat conflict | — | Not run | — |
| T44 | §7 hold owner | API | Visitor B attempts to release/confirm A's active hold using hold ID | No unauthorized transition; stable ownership conflict and state unchanged | Wrong-token release and confirm both rejected; existing hold remains protected | Pass | `tests/test_seat_api.py::test_only_owner_can_release_or_confirm_hold` |
| T45 | §4 max-hold rule | API | Request greater than configured maximum of 8 seats | Request rejected with `MAX_SEATS_EXCEEDED` and no mutation | Nine-seat request returned 422 `MAX_SEATS_EXCEEDED` after a valid 7-seat request | Pass | `test_seven_seat_hold_is_supported_and_nine_is_rejected` |
| T46 | §7 concurrent disjoint seats | API | Concurrently hold disjoint seats in same session | Both valid requests succeed; no avoidable session-wide serialization failure | — | Not run | — |
| T47 | §4 SSE lifecycle | E2E | Open event stream, change session seat state, close client | Correct session-only events delivered; closed clients release resources | — | Not run | — |
| T48 | §7 expiry/refetch race | API | Expiry and new hold contend for same seat at boundary | At most one active owner; boundary evaluated using server time inside atomic write | — | Not run | — |
| T49 | §12 booked ownership | API | Request seat map as Avery and another demo visitor | Avery's 3 seeded booked seats report `ownership=self`; the other 12 report `ownership=other`; no token is returned | Fresh DB returned 3 `self` and 12 `other` booked seats with no token field | Pass | `test_current_hall_has_fifty_seats_and_three_seeded_bookings` |
| T50 | §12 current visitor bookings | API | Fetch bookings for Avery, Blair, Casey, and a new visitor; confirm a new booking | Demo tokens see only 3, 5, and 7 seats respectively; new booking appears only for its owner token | API returned 3, 5, and 7 seat lists for demo tokens; a newly confirmed visitor booking returned only to its token | Pass | `test_bookings_page_api_returns_only_current_visitors_bookings`; `test_new_booking_appears_in_current_visitors_booking_list` |
| T51 | §12 50-seat hall and seed data | API/E2E | Load `SESSION-001` in a fresh database | Exactly 50 seats render in five rows; the demo visitors own three distinct bookings for 3, 5, and 7 seats (15 booked total) | Fresh DB test returned 50 seats and 15 seeded booked seats | Pass | `test_current_hall_has_fifty_seats_and_three_seeded_bookings` |
| T52 | §12 safe seed migration | API | Upgrade a prior six-seat database with an existing booking | Add missing seats and demo bookings without deleting or overwriting existing bookings | Migration test retained the existing `book_legacy` reservation and its A-01 ownership while adding 44 seats and 3 demo bookings | Pass | `test_migration_adds_demo_seed_without_replacing_existing_booking` |
| T53 | §12 booked state labels | UI | View seats as Avery and as the current visitor | Own booked seats visibly and accessibly say `Booked by you`; other bookings say `Booked` | — | Not run | Browser rendering not inspected |
| T54 | §12 bookings page | UI/E2E | Open My bookings under each demo profile and under a visitor with a new booking | Page lists only the active visitor's confirmed booking(s), with event, seat list, and reference; empty state is helpful | API contract passed; page rendering and navigation not inspected | Not run | Browser rendering not inspected |
| T55 | §12 group size | API | Hold 7 contiguous seats; then request 9 | Seven-seat request succeeds; requests above the configured maximum of 8 fail without partial mutation | Seven-seat hold returned 201; nine-seat hold returned 422 `MAX_SEATS_EXCEEDED` | Pass | `test_seven_seat_hold_is_supported_and_nine_is_rejected` |
| T56 | §12 D: migration | API/Ops | Start the updated app on the existing six-seat database | Existing records remain and the current hall grows to 50 seats with the 3/5/7-seat demo bookings | Database grew from 6 to 50 seats; 3 demo bookings/15 seats seeded. The existing database had 0 prior bookings to preserve | Pass | D: database migration smoke; API profile checks |
| T57 | API §2 validation | API | Request seats for an unknown session | Stable `SESSION_NOT_FOUND` response; no data is changed | — | Not run | — |

Executable backend coverage for the related cases is in `tests/test_seat_api.py`; browser-only scenarios remain specified here because no browser automation dependency is assumed.

## Defects and retests

| Defect | Severity | Owner | Case IDs | Fix/build | Retest result |
|---|---|---|---|---|---|

## Execution record

### Phase-two feature update

- Command: `python -m unittest discover -s tests -v` from `D:\MyProjects\SeatAllocation`.
- Result: **18 tests passed**, 0 failures/errors. `node --check frontend/app.js` passed.
- The new cases verified 50 seats over rows A–E, 15 seed-booked seats, Avery/Blair/Casey ownership counts of 3/5/7, current-token-only booking lists, a new booking appearing for its owner, and 7-seat acceptance / 9-seat rejection.
- A migration test started from a simulated prior six-seat database with one existing A-01 reservation; it grew to 50 seats and added the three demo bookings without replacing the existing reservation. The live D: database itself started with 6 seats and 0 bookings, then grew to 50 seats and received 3 demo bookings (15 seats).
- Live D: HTTP checks returned 50 seats, 15 booked seats, 3 self-owned seats for Avery, booking-list sizes 3/5/7, and `GET /` returned 200.
- UI browser rendering/navigation and keyboard/screen-reader checks remain unverified; see T53–T54 and browser-only next steps.

### Phase-one backend baseline (before §12 feature update)

- Command: `python -m unittest discover -s tests -v`
- Result: **13 tests passed**, 0 failures/errors (`Ran 13 tests in 9.391s`, `OK`). Python emitted `ResourceWarning` messages while handling expected HTTP 409/422 responses; test outcomes remained successful.
- Covered hold duration, all-or-nothing conflict, input validation, idempotent hold/confirmation, expiry, ownership checks, privacy, concurrent hold race, booking state, session map metadata, and SSE endpoint availability.

### Isolated HTTP integration smoke

- Setup: in-process server from `app.server.make_server`, temporary SQLite file, injected clock set to `2030-01-01T12:00:00Z`, two visitor tokens, then clock advanced by 121 seconds for expiry. No persistent app database was modified.
- `GET /`: 200 HTML, 8,323 bytes. `GET /styles.css`: 200 CSS, 17,690 bytes. `GET /app.js`: 200 JavaScript, 23,442 bytes.
- `GET /api/v1/sessions`: 200, returned `SESSION-001` and `SESSION-002`. Seat map for `SESSION-001`: 200 with 6 seats.
- Visitor A held A-01: 201, hold ID returned, expiry exactly `2030-01-01T12:02:00Z`.
- Visitor B then saw A-01 as `held`, `ownership=other`, with the same UTC expiry. Response did not contain visitor A's token.
- Connected SSE client received `seat.state_changed` for A-01 with session version 2, state `held`, and expiry.
- Visitor A confirmed: 201 confirmed booking. Refetched map showed A-01 as `booked`.
- Visitor A released a separate A-02 hold: DELETE returned 204; refetched map showed `available`.
- Visitor A held A-03; after advancing server clock by 121 seconds, confirmation returned 409 `HOLD_EXPIRED`; map refetch showed `available`.

## Browser-only checks and next steps

- T06–T08, T10, T12 (owner-side visual state), T14, T27–T29, T36–T39, and UI portions of T35 are not verified by HTTP tests. Next step: open the app in two real browser sessions, set different display names, exercise keyboard-only seat navigation and name session storage, inspect the live countdown with a skewed local clock, and simulate reconnect/version gaps. Use a screen reader/high-contrast mode for T37–T38.
- T26 only has a verified hold event. Release, expiry, and booking event fan-out still need explicit event assertions.
- T31 validation suite covered empty, duplicate, and unknown seat lists; missing session, over-limit seat count, and missing request fields remain unverified.
- T32 unknown/released/already-confirmed hold error variants remain unverified. T22 simultaneous confirmation race remains unverified; current test checks sequential idempotent retry.
- T40 verified request ID presence but did not capture a server log and compare correlation ID. Enable structured log capture and assert exact match.
- T41 operational metrics/log coverage, T42 irregular concert layout in browser E2E, T43 cross-session isolation, T45 maximum seat count, T46 concurrent disjoint holds, and T47 stream cleanup/session filtering remain unverified.
- T12 is left `Not run` because the requested two-client check established B's `other` view but did not assert A's `self` marker after hold.
- Browser automation was not configured for this pass. HTTP checks do not establish that the page executes correctly in a browser or that accessibility behavior is correct.

## Blocked cases and limitations

- Browser-level rendering, keyboard, and screen-reader verification is unrun as described above.
- Draft requirements leave login, prices, venue editing, max seats, one-active-hold policy, and payment unresolved. Phase one assumes no login/payment, static seeded layouts, and a documented configurable maximum; confirm product decisions before production.
- Production readiness additionally requires authentication, authorization, rate limiting, deployment/security review, and durable database operations.

## Final QA assessment

**Backend/API checks passed for the exercised core paths:** all 18 tests pass, including migration preservation, current-user booking lists, 50-seat/3-5-7 seed data, 7-seat holds, and the earlier hold/confirm/release/expiry coverage. The existing D: database migrated from 6 to 50 seats and now includes the three demo bookings. Browser rendering, navigation, keyboard and screen-reader behavior, and remaining listed API/realtime edge cases still need verification before full acceptance.
