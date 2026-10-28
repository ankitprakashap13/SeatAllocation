# Seat Allocation — Initial Requirements & Brainstorming Draft

**Status:** Draft 0.1  
**Application:** Seat Allocation  
**Scope:** Mini movie/concert seat booking system

## 1. Product goal

Allow users to discover available seats, temporarily hold seats while they complete checkout, and confirm a booking without double-booking. The design should work for both movie screenings and concert events.

## 2. Core user requirements

- View an event's seat map and seat availability.
- Select one or more seats.
- Hold selected seats temporarily for 2 minutes.
- Confirm a booking for held seats.
- See a selected-seat summary, hold countdown, and booking result.
- Prefer contiguous seats when suggesting or selecting seats.
- Ensure two users cannot successfully book the same seat.

## 3. Assumptions to validate

- A seat belongs to an event/session, not globally to a venue.
- A booking is for one event/session and may contain multiple seats.
- A user is identified by an authenticated user ID or an anonymous checkout token.
- Payment is out of scope for the first draft; confirmation represents a successful reservation.
- Seat states are `available`, `held`, `booked`, and optionally `blocked`.
- Real-time updates can start with WebSocket/SSE and have polling as a fallback.

## 4. Functional requirements

### Backend

- API to fetch an event/session and its seat availability.
- API to create a temporary hold with a 2-minute expiry.
- API to release/cancel a hold explicitly.
- API to confirm a booking from a valid hold.
- Automatic expiry of holds; expired seats become available again.
- Atomic conflict handling so only one request wins for a seat.
- Idempotent confirmation and safe retry behavior.
- Publish seat-state changes to connected clients.

### Frontend

- Grid/map view with row and seat labels.
- Clear visual states for available, selected, held-by-me, held-by-other, booked, and blocked seats.
- Contiguous-seat preference: suggest a contiguous group before scattered seats.
- Selected seats summary with count, total (if pricing is added), and hold countdown.
- Disable or reject stale selections after a real-time update.
- Show actionable errors when a seat is taken while the user is selecting.

## 5. Non-functional requirements

- Correctness takes priority over optimistic UI.
- Seat hold and booking operations must be transactional.
- Phase 1 APIs may run without authentication or authorization. Add authentication, authorization, and rate limiting before production.
- Timestamps are stored and exchanged in UTC.
- Observability: structured logs, request IDs, metrics for conflicts, expiry, and booking success.
- Accessibility: keyboard navigation, non-color state indicators, screen-reader labels.

## 6. Initial domain model

```text
Event(id, type, title, venue_id, starts_at)
Session(id, event_id, starts_at, status)
Seat(id, venue_id, row_label, seat_number, section, metadata)
SessionSeat(session_id, seat_id, state, hold_id, booking_id, version)
Hold(id, session_id, user_id/token, expires_at, status)
HoldSeat(hold_id, seat_id)
Booking(id, session_id, user_id/token, status, idempotency_key, created_at)
BookingSeat(booking_id, seat_id)
```

## 7. Consistency rules

1. A seat can be associated with at most one active hold or confirmed booking for a session.
2. An expired hold is not valid for confirmation, even if its cleanup job has not run yet; validity must be checked against `expires_at`.
3. Hold creation must atomically verify all requested seats are available (or already held by the same hold owner for an idempotent retry).
4. Confirmation must atomically convert the hold to a booking.
5. A stale frontend event must never override the backend's current state.

## 8. Open questions

- Is login required, or should guest users receive a secure reservation token?
- Is pricing per seat required in v1?
- Are seat layouts fixed per venue or configurable per event?
- Should a user be limited to one active hold per session?
- What is the maximum seat count per hold?
- Is payment integration needed before a booking is considered confirmed?

## 9. Acceptance criteria for the first slice

- Two concurrent hold attempts for the same seat result in exactly one successful hold.
- A hold expires after 2 minutes and cannot be confirmed afterward.
- A successful confirmation makes all seats in the hold booked atomically.
- A second booking attempt for a booked seat returns a conflict without changing the existing booking.
- Connected clients receive a state update after hold, release, expiry, or booking.
- The UI can recover from a dropped real-time connection by refetching the seat map.

## 10. Companion drafts

- API specification: [docs/api-spec.md](docs/api-spec.md)
- Backend/frontend flow diagrams: [docs/flow-diagrams.md](docs/flow-diagrams.md)
- Engineering gotchas and decisions are included in the companion drafts and should be revisited before implementation.

## 11. Phase 1 authentication and blocked-seat timer change

- Phase 1 APIs may run without login authentication or authorization so the prototype can be tested quickly.
- Before production, add authentication, authorization, and rate limiting.
- A seat held by another user must show a countdown based on the server-provided hold expiry timestamp. Do not expose the other user's identity.
- On first use, ask the visitor for a display name and store it in browser `sessionStorage` for the current session.
- Reuse the stored display name during the session; allow the visitor to edit or clear it.
- The display name is for UI and booking-summary purposes only. It is not authentication or proof of hold ownership.

## 12. Current-user bookings and 50-seat demo hall

- A booked seat owned by the current visitor token is labeled `Booked by you`; other confirmed seats are labeled `Booked`.
- Add a `My bookings` page that lists only confirmed bookings belonging to the current visitor token, including event/session, venue, seat IDs, confirmation status, and booking reference.
- Expand the seeded current movie hall (`SESSION-001`) to 50 seats in five rows of ten.
- Seed three distinct demo visitor tokens with confirmed bookings for 3, 5, and 7 seats. The UI may expose a demo profile selector so the seeded ownership states and bookings can be inspected locally.
- Demo profiles are local preview data, not authentication. Keep each profile's token separate from the editable display name and do not return other visitors' identity or tokens from APIs.
- Increase the phase-one maximum hold size to at least 7 seats so the 7-seat demo booking remains representative of supported group size.
