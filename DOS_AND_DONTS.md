# Seat Allocation — Code Standards: Dos and Don'ts

## Do

- Do keep seat ownership scoped by session/event.
- Do enforce conflicts in the database transaction, not only in the UI.
- Do compare hold expiry during every write operation.
- Do use idempotency keys for hold and confirmation retries.
- Do return stable error codes and request IDs.
- Do version seat-map updates and refetch after a version gap.
- Do test concurrent requests, retries, reconnects, and expired holds.
- Do make UI state accessible without relying on color alone.
- Do document assumptions and migration impacts.
- Do keep the phase 1 display name in `sessionStorage` and validate it before displaying or sending it.

## Don't

- Don't trust the frontend to prevent double booking.
- Don't use a delayed cleanup job as the only expiry check.
- Don't hold a database transaction open during payment or slow external calls.
- Don't expose another user's identity or sensitive hold data.
- Don't silently overwrite a newer seat version with stale client state.
- Don't make confirmation non-idempotent.
- Don't add a cache that can become the source of truth for seat ownership.
- Don't merge concurrency changes without a race-condition test.
- Don't treat a session-stored display name as a secure user identity.
