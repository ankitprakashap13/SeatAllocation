"""Dependency-free HTTP API for the Seat Allocation prototype.

SQLite is the source of truth for seat ownership. All mutating operations use
``BEGIN IMMEDIATE`` and validate the server-side expiry inside that write
transaction, so concurrent hold requests cannot both acquire a seat.
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import queue
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse


HOLD_DURATION_SECONDS = 120
MAX_SEATS_PER_HOLD = 8
LOGGER = logging.getLogger("seat_allocation")


def _utc_now(clock: Callable[[], datetime]) -> datetime:
    current = clock()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _token() -> str:
    return "v_" + uuid.uuid4().hex + uuid.uuid4().hex


def _connect(db_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=10, isolation_level=None, factory=_ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


class _ClosingConnection(sqlite3.Connection):
    """Make ``with connection`` close the handle after commit/rollback.

    sqlite3's built-in context manager does not close its file handle, which
    can prevent temporary database files from being removed on Windows.
    """

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


def _initialize_database(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                event_type TEXT NOT NULL,
                venue TEXT NOT NULL,
                starts_at TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS seats (
                session_id TEXT NOT NULL REFERENCES sessions(session_id),
                seat_id TEXT NOT NULL,
                row_label TEXT NOT NULL,
                seat_number INTEGER NOT NULL,
                section TEXT NOT NULL DEFAULT 'Main',
                initial_state TEXT NOT NULL DEFAULT 'available'
                    CHECK(initial_state IN ('available', 'blocked')),
                PRIMARY KEY(session_id, seat_id)
            );
            CREATE TABLE IF NOT EXISTS holds (
                hold_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(session_id),
                client_token TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('active', 'released', 'expired', 'confirmed')),
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                booking_id TEXT
            );
            CREATE TABLE IF NOT EXISTS hold_seats (
                hold_id TEXT NOT NULL REFERENCES holds(hold_id),
                seat_id TEXT NOT NULL,
                PRIMARY KEY(hold_id, seat_id)
            );
            CREATE TABLE IF NOT EXISTS bookings (
                booking_id TEXT PRIMARY KEY,
                hold_id TEXT NOT NULL UNIQUE REFERENCES holds(hold_id),
                session_id TEXT NOT NULL REFERENCES sessions(session_id),
                client_token TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status = 'confirmed'),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS session_seats (
                session_id TEXT NOT NULL,
                seat_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('available', 'held', 'booked', 'blocked')),
                hold_id TEXT,
                booking_id TEXT,
                held_until TEXT,
                PRIMARY KEY(session_id, seat_id),
                FOREIGN KEY(session_id, seat_id) REFERENCES seats(session_id, seat_id)
            );
            CREATE INDEX IF NOT EXISTS idx_session_seats_state
                ON session_seats(session_id, state);
            CREATE TABLE IF NOT EXISTS idempotency (
                client_token TEXT NOT NULL,
                operation TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(client_token, operation, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        sample_start = "2030-01-01T20:00:00Z"
        connection.executemany(
            "INSERT OR IGNORE INTO sessions(session_id, title, event_type, venue, starts_at, version) VALUES (?, ?, ?, ?, ?, 1)",
            [
                ("SESSION-001", "Friday Night Screening", "movie", "Northstar Cinema", sample_start),
                ("SESSION-002", "Summer Live", "concert", "Riverside Stage", sample_start),
            ],
        )
        session_seats: list[tuple[str, str, str, int, str, str]] = []
        for row in "ABCDE":
            for number in range(1, 11):
                seat_id = f"{row}-{number:02d}"
                session_seats.append(("SESSION-001", seat_id, row, number, "Orchestra", "available"))
        # A deliberately irregular concert layout includes an unavailable seat.
        for row, numbers in (("A", (1, 2, 4)), ("C", (1, 3))):
            for number in numbers:
                seat_id = f"{row}-{number:02d}"
                initial = "blocked" if (row, number) == ("A", 4) else "available"
                session_seats.append(("SESSION-002", seat_id, row, number, "Floor", initial))
        connection.executemany(
            "INSERT OR IGNORE INTO seats(session_id, seat_id, row_label, seat_number, section, initial_state) VALUES (?, ?, ?, ?, ?, ?)",
            session_seats,
        )
        connection.executemany(
            "INSERT OR IGNORE INTO session_seats(session_id, seat_id, state) VALUES (?, ?, ?)",
            [(row[0], row[1], row[5]) for row in session_seats],
        )

        seed_key = "phase2-demo-bookings"
        if connection.execute("SELECT 1 FROM app_metadata WHERE key=?", (seed_key,)).fetchone() is None:
            demo_bookings = [
                ("demo-avery", [f"A-{number:02d}" for number in range(1, 4)]),
                ("demo-blair", [f"B-{number:02d}" for number in range(1, 6)]),
                ("demo-casey", [f"C-{number:02d}" for number in range(1, 8)]),
            ]
            all_available = {
                row["seat_id"]
                for row in connection.execute(
                    "SELECT seat_id FROM session_seats WHERE session_id='SESSION-001' AND state='available'"
                ).fetchall()
            }
            for client_token, preferred_ids in demo_bookings:
                seat_ids = preferred_ids if set(preferred_ids).issubset(all_available) else []
                if not seat_ids:
                    seat_ids = sorted(all_available)[:len(preferred_ids)]
                if len(seat_ids) != len(preferred_ids):
                    continue
                hold_id = "hold_seed_" + uuid.uuid4().hex
                booking_id = "book_seed_" + uuid.uuid4().hex
                now = _iso(datetime.now(timezone.utc))
                connection.execute(
                    "INSERT INTO holds(hold_id, session_id, client_token, status, expires_at, created_at, booking_id) VALUES (?, 'SESSION-001', ?, 'confirmed', ?, ?, ?)",
                    (hold_id, client_token, now, now, booking_id),
                )
                connection.executemany(
                    "INSERT INTO hold_seats(hold_id, seat_id) VALUES (?, ?)",
                    [(hold_id, seat_id) for seat_id in seat_ids],
                )
                connection.execute(
                    "INSERT INTO bookings(booking_id, hold_id, session_id, client_token, status, created_at) VALUES (?, ?, 'SESSION-001', ?, 'confirmed', ?)",
                    (booking_id, hold_id, client_token, now),
                )
                connection.executemany(
                    "UPDATE session_seats SET state='booked', booking_id=? WHERE session_id='SESSION-001' AND seat_id=? AND state='available'",
                    [(booking_id, seat_id) for seat_id in seat_ids],
                )
                all_available.difference_update(seat_ids)
            connection.execute("INSERT INTO app_metadata(key, value) VALUES (?, '1')", (seed_key,))


class EventHub:
    """Single-process SSE fan-out; clients refetch on reconnect/version gaps."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, set[queue.Queue[dict[str, Any]]]] = {}

    def subscribe(self, session_id: str) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.setdefault(session_id, set()).add(subscriber)
        return subscriber

    def unsubscribe(self, session_id: str, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            listeners = self._subscribers.get(session_id)
            if listeners is not None:
                listeners.discard(subscriber)
                if not listeners:
                    self._subscribers.pop(session_id, None)

    def publish(self, session_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            listeners = list(self._subscribers.get(session_id, ()))
        for subscriber in listeners:
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                # A slow consumer must refetch the authoritative snapshot.
                try:
                    while True:
                        subscriber.get_nowait()
                except queue.Empty:
                    pass
                try:
                    subscriber.put_nowait({"type": "seat.refetch_required", "sessionId": session_id})
                except queue.Full:
                    pass


def make_server(
    host: str,
    port: int,
    *,
    db_path: str,
    clock: Callable[[], datetime] | None = None,
) -> ThreadingHTTPServer:
    """Create a seeded testable HTTP server with an injectable UTC clock."""
    _initialize_database(db_path)
    actual_clock = clock or (lambda: datetime.now(timezone.utc))
    hub = EventHub()

    class SeatAllocationServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        @property
        def database_path(self) -> str:
            return db_path

        @property
        def server_clock(self) -> Callable[[], datetime]:
            return actual_clock

        def log_message(self, fmt: str, *args: Any) -> None:
            # Structured request completion is emitted in _send_json below.
            return

        def _request_id(self) -> str:
            return "req_" + uuid.uuid4().hex[:16]

        def _send_json(
            self,
            status: int,
            request_id: str,
            *,
            data: Any = None,
            error: dict[str, Any] | None = None,
        ) -> None:
            body = json.dumps(
                {"data": data, "error": error, "requestId": request_id},
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            LOGGER.info(json.dumps({"requestId": request_id, "method": self.command, "path": self.path, "status": status}))

        def _send_error(
            self,
            status: int,
            request_id: str,
            code: str,
            message: str,
            details: dict[str, Any] | None = None,
        ) -> None:
            self._send_json(status, request_id, error={"code": code, "message": message, "details": details or {}})

        def _send_empty(self, status: int, request_id: str) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            LOGGER.info(json.dumps({"requestId": request_id, "method": self.command, "path": self.path, "status": status}))

        def _serve_frontend(self, request_id: str, path: str) -> bool:
            """Serve only files rooted in the checked-in frontend directory."""
            frontend_root = (Path(__file__).resolve().parent.parent / "frontend").resolve()
            requested = "index.html" if path == "/" else path.lstrip("/")
            try:
                file_path = (frontend_root / requested).resolve()
                file_path.relative_to(frontend_root)
            except (OSError, ValueError):
                return False
            if not file_path.is_file():
                return False
            content = file_path.read_bytes()
            content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            if file_path.suffix == ".js":
                content_type = "text/javascript"
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
            LOGGER.info(json.dumps({"requestId": request_id, "method": self.command, "path": self.path, "status": 200}))
            return True

        def _read_json(self, request_id: str) -> dict[str, Any] | None:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_error(400, request_id, "INVALID_CONTENT_LENGTH", "Invalid Content-Length")
                return None
            if content_length > 64 * 1024:
                self._send_error(413, request_id, "BODY_TOO_LARGE", "Request body exceeds 64 KB")
                return None
            if content_length == 0:
                return {}
            try:
                parsed = json.loads(self.rfile.read(content_length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send_error(400, request_id, "INVALID_JSON", "Request body must be valid JSON")
                return None
            if not isinstance(parsed, dict):
                self._send_error(400, request_id, "INVALID_BODY", "Request body must be a JSON object")
                return None
            return parsed

        def _client_token(self, request_id: str, *, required: bool) -> str | None:
            value = self.headers.get("X-Client-Token", "").strip()
            if not value and required:
                self._send_error(401, request_id, "CLIENT_TOKEN_REQUIRED", "A visitor token is required for this action")
                return None
            if len(value) > 200:
                self._send_error(400, request_id, "INVALID_CLIENT_TOKEN", "Visitor token is too long")
                return None
            return value or None

        def _payload_hash(self, body: dict[str, Any]) -> str:
            canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
            return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        def _idempotency_replay(
            self,
            connection: sqlite3.Connection,
            *,
            client_token: str,
            operation: str,
            key: str,
            payload_hash: str,
            request_id: str,
        ) -> bool:
            row = connection.execute(
                "SELECT payload_hash, status_code, response_json FROM idempotency WHERE client_token=? AND operation=? AND idempotency_key=?",
                (client_token, operation, key),
            ).fetchone()
            if row is None:
                return False
            if row["payload_hash"] != payload_hash:
                self._send_error(409, request_id, "IDEMPOTENCY_KEY_REUSED", "Idempotency key was already used with a different request")
                return True
            response_data = json.loads(row["response_json"])
            self._send_json(row["status_code"], request_id, data=response_data)
            return True

        def _store_idempotency(
            self,
            connection: sqlite3.Connection,
            *,
            client_token: str,
            operation: str,
            key: str,
            payload_hash: str,
            status: int,
            response_data: dict[str, Any],
        ) -> None:
            connection.execute(
                "INSERT INTO idempotency(client_token, operation, idempotency_key, payload_hash, status_code, response_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (client_token, operation, key, payload_hash, status, json.dumps(response_data, separators=(",", ":")), _iso(_utc_now(self.server_clock))),
            )

        def _require_idempotency_key(self, request_id: str) -> str | None:
            key = self.headers.get("Idempotency-Key", "").strip()
            if not key:
                self._send_error(400, request_id, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
                return None
            if len(key) > 200:
                self._send_error(400, request_id, "INVALID_IDEMPOTENCY_KEY", "Idempotency-Key is too long")
                return None
            return key

        def _expire_session_holds(
            self,
            connection: sqlite3.Connection,
            session_id: str,
            request_id: str,
        ) -> None:
            now = _utc_now(self.server_clock)
            expired_rows = connection.execute(
                "SELECT hold_id FROM holds WHERE session_id=? AND status='active' AND expires_at<=?",
                (session_id, _iso(now)),
            ).fetchall()
            if not expired_rows:
                return
            expired_ids = [row["hold_id"] for row in expired_rows]
            placeholders = ",".join("?" for _ in expired_ids)
            seat_ids = [row["seat_id"] for row in connection.execute(
                f"SELECT seat_id FROM session_seats WHERE hold_id IN ({placeholders}) AND state='held'",
                expired_ids,
            ).fetchall()]
            connection.execute(f"UPDATE holds SET status='expired' WHERE hold_id IN ({placeholders})", expired_ids)
            connection.execute(
                f"UPDATE session_seats SET state='available', hold_id=NULL, held_until=NULL WHERE hold_id IN ({placeholders}) AND state='held'",
                expired_ids,
            )
            if seat_ids:
                connection.execute("UPDATE sessions SET version=version+1 WHERE session_id=?", (session_id,))
                version = connection.execute("SELECT version FROM sessions WHERE session_id=?", (session_id,)).fetchone()[0]
                event = {"type": "seat.state_changed", "sessionId": session_id, "version": version, "seatIds": seat_ids, "state": "available"}
                self.server.event_hub.publish(session_id, event)
            LOGGER.info(json.dumps({"requestId": request_id, "event": "hold.expired", "sessionId": session_id, "holdCount": len(expired_ids)}))

        def _begin(self, connection: sqlite3.Connection) -> None:
            connection.execute("BEGIN IMMEDIATE")

        def _commit_event(
            self,
            connection: sqlite3.Connection,
            session_id: str,
            seat_ids: list[str],
            state: str,
            *,
            held_until: str | None = None,
        ) -> dict[str, Any]:
            connection.execute("UPDATE sessions SET version=version+1 WHERE session_id=?", (session_id,))
            version = connection.execute("SELECT version FROM sessions WHERE session_id=?", (session_id,)).fetchone()[0]
            event: dict[str, Any] = {
                "type": "seat.state_changed", "sessionId": session_id,
                "version": version, "seatIds": seat_ids, "state": state,
            }
            if held_until is not None:
                event["heldUntil"] = held_until
            return event

        def do_GET(self) -> None:
            request_id = self._request_id()
            parsed = urlparse(self.path)
            path = parsed.path
            session_match = re.fullmatch(r"/api/v1/sessions/([^/]+)/seats", path)
            stream_match = re.fullmatch(r"/api/v1/sessions/([^/]+)/seat-events", path)
            try:
                if not path.startswith("/api/"):
                    if self._serve_frontend(request_id, path):
                        return
                    self._send_error(404, request_id, "NOT_FOUND", "Frontend asset was not found")
                    return
                if path == "/api/v1/sessions":
                    with _connect(self.database_path) as connection:
                        rows = connection.execute("SELECT session_id, title, event_type, venue, starts_at, version FROM sessions ORDER BY session_id").fetchall()
                    self._send_json(200, request_id, data={"sessions": [dict(row) for row in rows]})
                    return
                if path == "/api/v1/bookings":
                    client_token = self._client_token(request_id, required=True)
                    if not client_token:
                        return
                    with _connect(self.database_path) as connection:
                        rows = connection.execute(
                            """SELECT b.booking_id, b.session_id, b.created_at,
                                      s.title, s.venue, s.starts_at, hs.seat_id
                               FROM bookings b
                               JOIN sessions s ON s.session_id=b.session_id
                               JOIN hold_seats hs ON hs.hold_id=b.hold_id
                               WHERE b.client_token=? AND b.status='confirmed'
                               ORDER BY b.created_at DESC, hs.seat_id""",
                            (client_token,),
                        ).fetchall()
                    bookings_by_id: dict[str, dict[str, Any]] = {}
                    for row in rows:
                        booking = bookings_by_id.setdefault(
                            row["booking_id"],
                            {
                                "bookingId": row["booking_id"], "sessionId": row["session_id"],
                                "title": row["title"], "venue": row["venue"],
                                "startsAt": row["starts_at"], "createdAt": row["created_at"],
                                "status": "confirmed", "seatIds": [],
                            },
                        )
                        booking["seatIds"].append(row["seat_id"])
                    self._send_json(200, request_id, data={"bookings": list(bookings_by_id.values())})
                    return
                if session_match:
                    session_id = session_match.group(1)
                    client_token = self._client_token(request_id, required=False)
                    if self.wfile.closed:
                        return
                    with _connect(self.database_path) as connection:
                        self._begin(connection)
                        session = connection.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                        if session is None:
                            connection.rollback()
                            self._send_error(404, request_id, "SESSION_NOT_FOUND", "Session was not found")
                            return
                        self._expire_session_holds(connection, session_id, request_id)
                        rows = connection.execute(
                            """SELECT s.seat_id, s.row_label, s.seat_number, s.section,
                                      CASE WHEN s.initial_state='blocked' THEN 'blocked' ELSE ss.state END AS state,
                                      h.client_token AS holder_token, b.client_token AS booking_token, ss.held_until
                               FROM seats s JOIN session_seats ss USING(session_id, seat_id)
                               LEFT JOIN holds h ON h.hold_id=ss.hold_id
                               LEFT JOIN bookings b ON b.booking_id=ss.booking_id
                               WHERE s.session_id=? ORDER BY s.row_label, s.seat_number""",
                            (session_id,),
                        ).fetchall()
                        latest = connection.execute("SELECT version FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                        connection.commit()
                    seats: list[dict[str, Any]] = []
                    for row in rows:
                        seat: dict[str, Any] = {
                            "id": row["seat_id"], "row": row["row_label"],
                            "number": row["seat_number"], "section": row["section"], "state": row["state"],
                        }
                        if row["state"] == "held":
                            seat["ownership"] = "self" if client_token and row["holder_token"] == client_token else "other"
                            seat["heldUntil"] = row["held_until"]
                        elif row["state"] == "booked":
                            seat["ownership"] = "self" if client_token and row["booking_token"] == client_token else "other"
                        seats.append(seat)
                    data = {
                        "sessionId": session_id, "title": session["title"], "eventType": session["event_type"],
                        "venue": session["venue"], "startsAt": session["starts_at"],
                        "version": latest["version"], "holdDurationSeconds": HOLD_DURATION_SECONDS, "seats": seats,
                    }
                    self._send_json(200, request_id, data=data)
                    return
                if stream_match:
                    session_id = stream_match.group(1)
                    with _connect(self.database_path) as connection:
                        exists = connection.execute("SELECT 1 FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                    if exists is None:
                        self._send_error(404, request_id, "SESSION_NOT_FOUND", "Session was not found")
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache, no-transform")
                    self.send_header("Connection", "keep-alive")
                    self.send_header("X-Accel-Buffering", "no")
                    self.end_headers()
                    self.wfile.write(b": connected\n\n")
                    self.wfile.flush()
                    subscriber = self.server.event_hub.subscribe(session_id)
                    try:
                        while True:
                            try:
                                event = subscriber.get(timeout=10)
                                event_id = event.get("version", "")
                                packet = f"id: {event_id}\nevent: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
                            except queue.Empty:
                                packet = ": keep-alive\n\n"
                            self.wfile.write(packet.encode("utf-8"))
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass
                    finally:
                        self.server.event_hub.unsubscribe(session_id, subscriber)
                    return
                self._send_error(404, request_id, "NOT_FOUND", "Route was not found")
            except sqlite3.Error:
                LOGGER.exception(json.dumps({"requestId": request_id, "event": "database.error"}))
                self._send_error(500, request_id, "INTERNAL_ERROR", "A storage error occurred")

        def do_POST(self) -> None:
            request_id = self._request_id()
            body = self._read_json(request_id)
            if body is None:
                return
            path = urlparse(self.path).path
            hold_match = re.fullmatch(r"/api/v1/sessions/([^/]+)/holds", path)
            confirm_match = re.fullmatch(r"/api/v1/holds/([^/]+)/confirm", path)
            if hold_match:
                self._create_hold(request_id, hold_match.group(1), body)
                return
            if confirm_match:
                self._confirm_hold(request_id, confirm_match.group(1), body)
                return
            self._send_error(404, request_id, "NOT_FOUND", "Route was not found")

        def _create_hold(self, request_id: str, session_id: str, body: dict[str, Any]) -> None:
            client_token = self._client_token(request_id, required=True)
            if not client_token:
                return
            key = self._require_idempotency_key(request_id)
            if not key:
                return
            raw_seat_ids = body.get("seatIds")
            if not isinstance(raw_seat_ids, list) or not raw_seat_ids:
                self._send_error(422, request_id, "INVALID_SEAT_IDS", "seatIds must be a non-empty array")
                return
            if any(not isinstance(seat_id, str) or not seat_id.strip() for seat_id in raw_seat_ids):
                self._send_error(422, request_id, "INVALID_SEAT_IDS", "Every seat ID must be a non-empty string")
                return
            seat_ids = [seat_id.strip() for seat_id in raw_seat_ids]
            if len(set(seat_ids)) != len(seat_ids):
                self._send_error(422, request_id, "DUPLICATE_SEAT_IDS", "seatIds must not contain duplicates")
                return
            if len(seat_ids) > MAX_SEATS_PER_HOLD:
                self._send_error(422, request_id, "MAX_SEATS_EXCEEDED", f"A hold may contain at most {MAX_SEATS_PER_HOLD} seats")
                return
            payload_hash = self._payload_hash({"seatIds": seat_ids})
            connection = _connect(self.database_path)
            event = None
            try:
                self._begin(connection)
                if self._idempotency_replay(connection, client_token=client_token, operation=f"hold:{session_id}", key=key, payload_hash=payload_hash, request_id=request_id):
                    connection.rollback()
                    return
                session = connection.execute("SELECT 1 FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                if session is None:
                    connection.rollback()
                    self._send_error(404, request_id, "SESSION_NOT_FOUND", "Session was not found")
                    return
                self._expire_session_holds(connection, session_id, request_id)
                placeholders = ",".join("?" for _ in seat_ids)
                found = connection.execute(
                    f"SELECT seat_id, state FROM session_seats WHERE session_id=? AND seat_id IN ({placeholders})",
                    (session_id, *seat_ids),
                ).fetchall()
                found_map = {row["seat_id"]: row["state"] for row in found}
                unknown = [seat_id for seat_id in seat_ids if seat_id not in found_map]
                if unknown:
                    connection.rollback()
                    self._send_error(422, request_id, "UNKNOWN_SEAT", "One or more seat IDs are unknown", {"seatIds": unknown})
                    return
                conflicts = [seat_id for seat_id in seat_ids if found_map[seat_id] != "available"]
                if conflicts:
                    connection.rollback()
                    self._send_error(409, request_id, "SEAT_UNAVAILABLE", "One or more seats are not available", {"seatIds": conflicts})
                    return
                hold_id = "hold_" + uuid.uuid4().hex
                expires_at = _iso(_utc_now(self.server_clock) + timedelta(seconds=HOLD_DURATION_SECONDS))
                now = _iso(_utc_now(self.server_clock))
                connection.execute(
                    "INSERT INTO holds(hold_id, session_id, client_token, status, expires_at, created_at) VALUES (?, ?, ?, 'active', ?, ?)",
                    (hold_id, session_id, client_token, expires_at, now),
                )
                connection.executemany("INSERT INTO hold_seats(hold_id, seat_id) VALUES (?, ?)", [(hold_id, seat_id) for seat_id in seat_ids])
                connection.executemany(
                    "UPDATE session_seats SET state='held', hold_id=?, held_until=? WHERE session_id=? AND seat_id=? AND state='available'",
                    [(hold_id, expires_at, session_id, seat_id) for seat_id in seat_ids],
                )
                data = {"holdId": hold_id, "sessionId": session_id, "seatIds": seat_ids, "expiresAt": expires_at, "status": "active"}
                self._store_idempotency(connection, client_token=client_token, operation=f"hold:{session_id}", key=key, payload_hash=payload_hash, status=201, response_data=data)
                event = self._commit_event(connection, session_id, seat_ids, "held", held_until=expires_at)
                connection.commit()
                self._send_json(201, request_id, data=data)
            except sqlite3.Error:
                connection.rollback()
                LOGGER.exception(json.dumps({"requestId": request_id, "event": "hold.create_failed"}))
                self._send_error(500, request_id, "INTERNAL_ERROR", "Could not create seat hold")
            finally:
                connection.close()
            if event:
                self.server.event_hub.publish(session_id, event)
                LOGGER.info(json.dumps({"requestId": request_id, "event": "hold.created", "sessionId": session_id, "seatCount": len(seat_ids)}))

        def do_DELETE(self) -> None:
            request_id = self._request_id()
            match = re.fullmatch(r"/api/v1/holds/([^/]+)", urlparse(self.path).path)
            if not match:
                self._send_error(404, request_id, "NOT_FOUND", "Route was not found")
                return
            client_token = self._client_token(request_id, required=True)
            if not client_token:
                return
            hold_id = match.group(1)
            connection = _connect(self.database_path)
            event = None
            session_id = ""
            try:
                self._begin(connection)
                hold = connection.execute("SELECT * FROM holds WHERE hold_id=?", (hold_id,)).fetchone()
                if hold is None:
                    connection.rollback()
                    self._send_error(404, request_id, "HOLD_NOT_FOUND", "Hold was not found")
                    return
                session_id = hold["session_id"]
                if hold["client_token"] != client_token:
                    connection.rollback()
                    self._send_error(409, request_id, "HOLD_NOT_OWNED", "This hold belongs to another visitor")
                    return
                self._expire_session_holds(connection, session_id, request_id)
                hold = connection.execute("SELECT * FROM holds WHERE hold_id=?", (hold_id,)).fetchone()
                if hold["status"] == "active":
                    seat_ids = [row["seat_id"] for row in connection.execute("SELECT seat_id FROM hold_seats WHERE hold_id=?", (hold_id,)).fetchall()]
                    connection.execute("UPDATE holds SET status='released' WHERE hold_id=?", (hold_id,))
                    connection.execute("UPDATE session_seats SET state='available', hold_id=NULL, held_until=NULL WHERE hold_id=? AND state='held'", (hold_id,))
                    event = self._commit_event(connection, session_id, seat_ids, "available")
                connection.commit()
                self._send_empty(204, request_id)
            except sqlite3.Error:
                connection.rollback()
                LOGGER.exception(json.dumps({"requestId": request_id, "event": "hold.release_failed"}))
                self._send_error(500, request_id, "INTERNAL_ERROR", "Could not release seat hold")
            finally:
                connection.close()
            if event:
                self.server.event_hub.publish(session_id, event)
                LOGGER.info(json.dumps({"requestId": request_id, "event": "hold.released", "holdId": hold_id}))

        def _confirm_hold(self, request_id: str, hold_id: str, body: dict[str, Any]) -> None:
            client_token = self._client_token(request_id, required=True)
            if not client_token:
                return
            key = self._require_idempotency_key(request_id)
            if not key:
                return
            customer = body.get("customer")
            if customer is not None and not isinstance(customer, dict):
                self._send_error(422, request_id, "INVALID_CUSTOMER", "customer must be an object")
                return
            payload_hash = self._payload_hash({"holdId": hold_id, "customer": customer or {}})
            connection = _connect(self.database_path)
            event = None
            try:
                self._begin(connection)
                if self._idempotency_replay(connection, client_token=client_token, operation="confirm", key=key, payload_hash=payload_hash, request_id=request_id):
                    connection.rollback()
                    return
                hold = connection.execute("SELECT * FROM holds WHERE hold_id=?", (hold_id,)).fetchone()
                if hold is None:
                    connection.rollback()
                    self._send_error(404, request_id, "HOLD_NOT_FOUND", "Hold was not found")
                    return
                if hold["client_token"] != client_token:
                    connection.rollback()
                    self._send_error(409, request_id, "HOLD_NOT_OWNED", "This hold belongs to another visitor")
                    return
                session_id = hold["session_id"]
                self._expire_session_holds(connection, session_id, request_id)
                hold = connection.execute("SELECT * FROM holds WHERE hold_id=?", (hold_id,)).fetchone()
                if hold["status"] == "expired" or _parse_time(hold["expires_at"]) <= _utc_now(self.server_clock):
                    connection.rollback()
                    self._send_error(409, request_id, "HOLD_EXPIRED", "Hold has expired")
                    return
                if hold["status"] == "released":
                    connection.rollback()
                    self._send_error(409, request_id, "HOLD_RELEASED", "Hold has been released")
                    return
                if hold["status"] == "confirmed":
                    connection.rollback()
                    self._send_error(409, request_id, "ALREADY_CONFIRMED", "Hold was already confirmed")
                    return
                seat_ids = [row["seat_id"] for row in connection.execute("SELECT seat_id FROM hold_seats WHERE hold_id=? ORDER BY seat_id", (hold_id,)).fetchall()]
                booking_id = "book_" + uuid.uuid4().hex
                now = _iso(_utc_now(self.server_clock))
                connection.execute(
                    "INSERT INTO bookings(booking_id, hold_id, session_id, client_token, status, created_at) VALUES (?, ?, ?, ?, 'confirmed', ?)",
                    (booking_id, hold_id, session_id, client_token, now),
                )
                connection.execute("UPDATE holds SET status='confirmed', booking_id=? WHERE hold_id=?", (booking_id, hold_id))
                connection.execute("UPDATE session_seats SET state='booked', booking_id=?, hold_id=NULL, held_until=NULL WHERE hold_id=? AND state='held'", (booking_id, hold_id))
                data = {"bookingId": booking_id, "sessionId": session_id, "seatIds": seat_ids, "status": "confirmed"}
                self._store_idempotency(connection, client_token=client_token, operation="confirm", key=key, payload_hash=payload_hash, status=201, response_data=data)
                event = self._commit_event(connection, session_id, seat_ids, "booked")
                connection.commit()
                self._send_json(201, request_id, data=data)
            except sqlite3.Error:
                connection.rollback()
                LOGGER.exception(json.dumps({"requestId": request_id, "event": "booking.confirm_failed"}))
                self._send_error(500, request_id, "INTERNAL_ERROR", "Could not confirm booking")
            finally:
                connection.close()
            if event:
                self.server.event_hub.publish(session_id, event)
                LOGGER.info(json.dumps({"requestId": request_id, "event": "booking.confirmed", "bookingId": booking_id}))

    server = SeatAllocationServer((host, port), Handler)
    server.database_path = db_path
    server.clock = actual_clock
    server.event_hub = hub
    return server


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Seat Allocation API server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="data/seat-allocation.sqlite3")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    server = make_server(args.host, args.port, db_path=args.db)
    print(f"Seat Allocation API listening at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
