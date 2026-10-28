# Seat Allocation — Initial API Specification

Base path: `/api/v1`  
Content type: `application/json`  
All timestamps: ISO-8601 UTC.

## Common response shape

```json
{ "data": {}, "error": null, "requestId": "req_123" }
```

Errors use:

```json
{
  "data": null,
  "error": { "code": "SEAT_UNAVAILABLE", "message": "One or more seats are not available", "details": { "seatIds": ["A-10"] } },
  "requestId": "req_123"
}
```

## 1. Fetch seat availability

`GET /sessions/{sessionId}/seats`

Query parameters: `include=layout,pricing` (optional), `version` (optional client cache/version).

Response `200`:

```json
{
  "data": {
    "sessionId": "sess_123",
    "version": 42,
    "holdDurationSeconds": 120,
    "seats": [
      { "id": "A-10", "row": "A", "number": 10, "state": "available", "price": 12.5 },
      { "id": "A-11", "row": "A", "number": 11, "state": "held", "heldUntil": "2026-10-28T12:02:00Z" }
    ]
  },
  "error": null,
  "requestId": "req_123"
}
```

For `held` and `booked` seats, include `ownership: "self"|"other"` based only on the `X-Client-Token` header. Include `heldUntil` for held seats. Never return another visitor's token or display name.

## 1a. List current visitor's bookings

`GET /bookings`

Header: `X-Client-Token: <visitor-token>`

Response `200` contains only confirmed bookings owned by that token:

```json
{
  "data": {
    "bookings": [
      { "bookingId": "book_123", "sessionId": "sess_123", "title": "Friday Night Screening", "venue": "Northstar Cinema", "startsAt": "2026-10-28T20:00:00Z", "createdAt": "2026-10-28T12:00:00Z", "seatIds": ["A-10", "A-11"], "status": "confirmed" }
    ]
  },
  "error": null,
  "requestId": "req_126"
}
```

## 2. Create or extend a hold

`POST /sessions/{sessionId}/holds`

Headers: `Idempotency-Key: <client-generated-key>`

Request:

```json
{ "seatIds": ["A-10", "A-11"], "clientToken": "optional-guest-token" }
```

Response `201`:

```json
{
  "data": { "holdId": "hold_123", "seatIds": ["A-10", "A-11"], "expiresAt": "2026-10-28T12:02:00Z", "status": "active" },
  "error": null,
  "requestId": "req_124"
}
```

Conflict `409`: `SEAT_UNAVAILABLE` with conflicting seat IDs. Expired holds must be treated as unavailable only until the transaction evaluates them; cleanup may be asynchronous.

## 3. Release a hold

`DELETE /holds/{holdId}`

Response `204` for an already released/expired hold as well as a newly released hold (idempotent).

## 4. Confirm booking

`POST /holds/{holdId}/confirm`

Headers: `Idempotency-Key: <booking-attempt-key>`

Request:

```json
{ "customer": { "email": "user@example.com" } }
```

Response `201`:

```json
{ "data": { "bookingId": "book_123", "sessionId": "sess_123", "seatIds": ["A-10", "A-11"], "status": "confirmed" }, "error": null, "requestId": "req_125" }
```

Errors: `404 HOLD_NOT_FOUND`, `409 HOLD_EXPIRED`, `409 HOLD_NOT_OWNED`, `409 ALREADY_CONFIRMED`.

## 5. Real-time updates

`GET /sessions/{sessionId}/seat-events` using Server-Sent Events, or `WS /sessions/{sessionId}/seat-events` if bidirectional transport is required.

Event:

```json
{ "type": "seat.state_changed", "sessionId": "sess_123", "version": 43, "seatIds": ["A-10"], "state": "held", "heldUntil": "2026-10-28T12:02:00Z" }
```

Clients must refetch when versions are skipped or the connection resumes.

## API gotchas

- Do not rely on a periodic expiry worker for correctness; compare `expires_at` inside the hold/confirm transaction.
- Use a database uniqueness constraint or equivalent conditional write for `(session_id, seat_id)` active ownership.
- Use idempotency keys to prevent double bookings caused by retries.
- Avoid returning another user's identity in held-seat responses.
- Validate maximum seat count and reject duplicate seat IDs in a request.

## Phase 1 authentication note

Phase 1 APIs may run without login authentication. Use a temporary client/visitor token to distinguish the caller's own hold from another user's hold. Return only an ownership marker such as `self` or `other`, plus `heldUntil` for the UI countdown; never expose another user's identity. Add authentication, authorization, and rate limiting before production.

The frontend should ask for a display name on first use and keep it in browser `sessionStorage` (for example, key `seatAllocation.displayName`). It may send the name as optional booking metadata, but the server must not treat it as authentication or authorization. Do not return it to other users.
