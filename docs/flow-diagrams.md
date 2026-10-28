# Seat Allocation — Backend and Frontend Flows

## Backend flow

```mermaid
flowchart TD
  A[Client requests seat map] --> B[API reads session seats]
  B --> C[Return state + version]
  C --> D[Client requests hold]
  D --> E[Begin DB transaction]
  E --> F{All seats available or owned by same retry?}
  F -- No --> G[Rollback and return 409]
  F -- Yes --> H[Create/update hold with expires_at = now + 2 min]
  H --> I[Commit transaction]
  I --> J[Publish seat.state_changed]
  J --> K[Return hold + expiry]
  K --> L[Client confirms hold]
  L --> M[Begin DB transaction and lock hold/seats]
  M --> N{Hold active and unexpired?}
  N -- No --> O[Rollback and return 409]
  N -- Yes --> P[Create booking; mark seats booked]
  P --> Q[Commit transaction]
  Q --> R[Publish booked event and return booking]
```

## Frontend flow

```mermaid
flowchart TD
  A[Open event/session] --> B[Fetch seat map]
  B --> C[Render grid and legend]
  C --> D[Subscribe to real-time events]
  C --> E[User selects seats]
  E --> F{Seats still available locally?}
  F -- No --> G[Show conflict and refresh map]
  F -- Yes --> H[POST hold]
  H --> I{Hold succeeds?}
  I -- No --> G
  I -- Yes --> J[Show selected summary + 2-min countdown]
  J --> K{User confirms before expiry?}
  K -- No --> L[Release/expire and refresh]
  K -- Yes --> M[POST confirm]
  M --> N{Booking succeeds?}
  N -- No --> O[Show error and refetch]
  N -- Yes --> P[Show confirmation]
  D --> Q{Event version gap or reconnect?}
  Q -- Yes --> B
```

## Suggested contiguous-seat behavior

1. When a user selects a starting seat, inspect the same row/section for a contiguous run.
2. Suggest the smallest run that satisfies the requested count, preferring the same price tier.
3. Treat suggestions as hints only; the backend remains authoritative.

## Blocked-seat timer behavior

For a seat held by another user, render a blocked state and countdown using the server's `heldUntil` value. When the timer reaches zero, refetch the seat state; do not assume the seat is available until the backend confirms it.

## Phase 1 visitor-name flow

```mermaid
flowchart TD
  A[Open app] --> B{display name in sessionStorage?}
  B -- No --> C[Prompt for display name]
  C --> D[Validate non-empty name]
  D --> E[Save seatAllocation.displayName in sessionStorage]
  B -- Yes --> F[Load stored display name]
  E --> G[Show seat map]
  F --> G
  G --> H[Use name in this session's UI/booking summary]
```
