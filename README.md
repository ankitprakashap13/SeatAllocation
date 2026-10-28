# Seat Allocation

A dependency-free local seat booking prototype for movie screenings and live events. The Python standard-library server serves the browser app and JSON API; SQLite is the source of truth for holds and bookings.

## Run locally

From this directory, run:

```powershell
python -m app.server --host 127.0.0.1 --port 8000 --db data/seat-allocation.sqlite3
```

Open <http://127.0.0.1:8000>. Press `Ctrl+C` in the terminal to stop the server. The first visit asks for a display name, stored in browser `sessionStorage`; a separate random visitor token is used for hold ownership.

The server seeds `SESSION-001` with 50 seats across five rows and three demo visitors with 3, 5, and 7 confirmed seats. Use the **Demo profile** selector to view each visitor's booked seats and booking list. `SESSION-002` is an irregular concert layout with a blocked seat. Holds last 120 seconds. New holds support up to 8 seats. No payment or login is included; demo profiles are for local preview only, not authentication.

Booked seats owned by the selected visitor are labeled **Booked by you**. Other booked seats appear as **Booked**. **My bookings** lists only confirmed bookings belonging to the current visitor token.

## Run the API acceptance suite

```powershell
python -m unittest discover -s tests -v
```

## Project notes

- [Requirements](requirement.md)
- [API specification](docs/api-spec.md)
- [Flow diagrams](docs/flow-diagrams.md)
- [QA test plan and running report](reports/qa-report.md)
- [Application workflow](APPLICATION_WORKFLOW.md)

This is a local prototype. Before production, add authenticated ownership, authorization, rate limiting, persistent/shared real-time fan-out, and operational monitoring.
