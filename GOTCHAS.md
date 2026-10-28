# Seat Allocation — Gotchas to Resolve Before Production

- **Expiry race:** a hold can expire between a client countdown tick and confirmation. The server must validate the timestamp inside the transaction.
- **Double-booking race:** checking availability and writing ownership as separate operations is unsafe. Use row locks, conditional updates, and a uniqueness constraint.
- **Retry ambiguity:** mobile networks retry requests. Without idempotency keys, a timeout can create duplicate holds or bookings.
- **Real-time ordering:** events can arrive late or out of order. Include a monotonically increasing session version and refetch after gaps.
- **Cleanup lag:** an expiry worker is for cleanup, not correctness. Expired records may remain visible briefly.
- **Multi-seat atomicity:** never partially hold or book a requested group unless partial allocation is explicitly a product decision.
- **Time zones:** store UTC and calculate expiry on the server; never trust a browser clock.
- **Guest security:** reservation tokens must be unguessable and scoped; do not expose other users' contact details.
- **Venue layouts:** movie and concert layouts may have irregular rows, sections, standing areas, or blocked seats; avoid assuming a perfect rectangle.
- **Cache invalidation:** cached seat maps must be versioned and short-lived; cache must never decide ownership.
- **Payment boundary:** if payment is added, do not keep database locks open while waiting on a payment provider.
- **Load behavior:** popular events create hot rows and connection spikes; plan rate limits, backpressure, and event fan-out.
- **Unauthenticated phase 1:** a client token must still identify the caller's own hold so the UI can distinguish `self` from `other`; never use a browser-supplied user ID as proof of ownership.
- **Blocked-seat timer:** calculate the countdown from the server-provided `heldUntil` timestamp, not from the browser clock alone; refresh when it reaches zero or when a real-time update arrives.
- **Session storage is not identity:** a display name in browser `sessionStorage` is easy to change or clear. Never use it as authentication, authorization, or proof that a hold belongs to a person.
