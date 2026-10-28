"""Test-first HTTP contract for the phase-one Seat Allocation API.

The backend contract is documented in reports/qa-report.md. This module uses
only Python's standard library so it can run before choosing a test framework.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class SeatApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Deliberately imported inside setup: this test-first suite is expected
        # to be red until the backend implements the agreed server seam.
        from app.server import make_server

        cls._temp_root = tempfile.TemporaryDirectory(prefix="seat-api-")
        cls._temp_path = Path(cls._temp_root.name)
        cls._make_server = staticmethod(make_server)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temp_root.cleanup()

    def setUp(self) -> None:
        self.now = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
        self._db = self._temp_path / f"{self.id().split('.')[-1]}.sqlite3"
        self.server = self._make_server(
            "127.0.0.1", 0, db_path=str(self._db), clock=lambda: self.now
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/api/v1"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self._db.unlink(missing_ok=True)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        token: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[int, dict[str, Any] | None, dict[str, str]]:
        headers = {"Accept": "application/json"}
        encoded = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            encoded = json.dumps(body).encode("utf-8")
        if token is not None:
            headers["X-Client-Token"] = token
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        req = urllib.request.Request(
            f"{self.base}{path}", data=encoded, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                raw = response.read()
                payload = json.loads(raw) if raw else None
                return response.status, payload, dict(response.headers.items())
        except urllib.error.HTTPError as error:
            raw = error.read()
            payload = json.loads(raw) if raw else None
            return error.code, payload, dict(error.headers.items())

    @staticmethod
    def data(response: dict[str, Any] | None) -> dict[str, Any]:
        if response is None:
            raise AssertionError("expected a JSON response")
        return response.get("data") or {}

    @staticmethod
    def error_code(response: dict[str, Any] | None) -> str | None:
        if response is None:
            return None
        error = response.get("error") or {}
        return error.get("code")

    def hold(
        self,
        seats: list[str],
        *,
        token: str = "visitor-A",
        key: str = "hold-key",
    ) -> tuple[int, dict[str, Any] | None]:
        status, payload, _ = self.request(
            "POST",
            "/sessions/SESSION-001/holds",
            body={"seatIds": seats},
            token=token,
            idempotency_key=key,
        )
        return status, payload

    def test_seat_map_returns_version_duration_utc_and_request_id(self) -> None:
        status, payload, _ = self.request(
            "GET", "/sessions/SESSION-001/seats", token="visitor-A"
        )
        self.assertEqual(status, 200)
        self.assertIsNone(payload["error"])
        data = self.data(payload)
        self.assertEqual(data["sessionId"], "SESSION-001")
        self.assertGreaterEqual(data["version"], 1)
        self.assertEqual(data["holdDurationSeconds"], 120)
        self.assertTrue(data["seats"])
        self.assertTrue(payload.get("requestId"))

    def test_current_hall_has_fifty_seats_and_three_seeded_bookings(self) -> None:
        status, payload, _ = self.request(
            "GET", "/sessions/SESSION-001/seats", token="demo-avery"
        )
        self.assertEqual(status, 200)
        data = self.data(payload)
        self.assertEqual(len(data["seats"]), 50)
        self.assertEqual({seat["row"] for seat in data["seats"]}, set("ABCDE"))
        booked = [seat for seat in data["seats"] if seat["state"] == "booked"]
        self.assertEqual(len(booked), 15)
        owned = [seat for seat in booked if seat.get("ownership") == "self"]
        self.assertEqual(len(owned), 3)
        other = [seat for seat in booked if seat.get("ownership") == "other"]
        self.assertEqual(len(other), 12)

    def test_bookings_page_api_returns_only_current_visitors_bookings(self) -> None:
        status, payload, _ = self.request("GET", "/bookings", token="demo-avery")
        self.assertEqual(status, 200)
        bookings = self.data(payload)["bookings"]
        self.assertEqual([len(booking["seatIds"]) for booking in bookings], [3])
        self.assertNotIn("clientToken", json.dumps(payload))

        status, payload, _ = self.request("GET", "/bookings", token="demo-blair")
        self.assertEqual(status, 200)
        self.assertEqual([len(booking["seatIds"]) for booking in self.data(payload)["bookings"]], [5])

        status, payload, _ = self.request("GET", "/bookings", token="demo-casey")
        self.assertEqual(status, 200)
        self.assertEqual([len(booking["seatIds"]) for booking in self.data(payload)["bookings"]], [7])

    def test_new_booking_appears_in_current_visitors_booking_list(self) -> None:
        status, held = self.hold(["E-09", "E-10"], token="fresh-visitor", key="new-booking")
        self.assertEqual(status, 201)
        hold_id = self.data(held)["holdId"]
        status, booking, _ = self.request(
            "POST", f"/holds/{hold_id}/confirm", body={}, token="fresh-visitor",
            idempotency_key="new-booking-confirm",
        )
        self.assertEqual(status, 201)
        booking_id = self.data(booking)["bookingId"]
        status, listing, _ = self.request("GET", "/bookings", token="fresh-visitor")
        self.assertEqual(status, 200)
        records = self.data(listing)["bookings"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["bookingId"], booking_id)
        self.assertEqual(records[0]["seatIds"], ["E-09", "E-10"])

    def test_seven_seat_hold_is_supported_and_nine_is_rejected(self) -> None:
        seven = [f"D-{number:02d}" for number in range(1, 8)]
        status, payload = self.hold(seven, token="large-party", key="seven-seat-hold")
        self.assertEqual(status, 201)
        self.assertEqual(self.data(payload)["seatIds"], seven)

        nine = [f"E-{number:02d}" for number in range(1, 10)]
        status, payload = self.hold(nine, token="large-party", key="nine-seat-hold")
        self.assertEqual(status, 422)
        self.assertEqual(self.error_code(payload), "MAX_SEATS_EXCEEDED")

    def test_migration_adds_demo_seed_without_replacing_existing_booking(self) -> None:
        connection = sqlite3.connect(self._db)
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM bookings")
        connection.execute("DELETE FROM holds")
        connection.execute("DELETE FROM hold_seats")
        connection.execute("DELETE FROM app_metadata WHERE key='phase2-demo-bookings'")
        connection.execute(
            "UPDATE session_seats SET state='available', hold_id=NULL, booking_id=NULL, held_until=NULL WHERE session_id='SESSION-001'"
        )
        legacy_seats = {"A-01", "A-02", "A-03", "A-04", "B-01", "B-02"}
        for seat_id in [f"{row}-{number:02d}" for row in "ABCDE" for number in range(1, 11) if f"{row}-{number:02d}" not in legacy_seats]:
            connection.execute("DELETE FROM session_seats WHERE session_id='SESSION-001' AND seat_id=?", (seat_id,))
            connection.execute("DELETE FROM seats WHERE session_id='SESSION-001' AND seat_id=?", (seat_id,))
        connection.execute(
            "INSERT INTO holds(hold_id, session_id, client_token, status, expires_at, created_at, booking_id) VALUES ('hold_legacy', 'SESSION-001', 'legacy-owner', 'confirmed', '2030-01-01T12:00:00Z', '2030-01-01T12:00:00Z', 'book_legacy')"
        )
        connection.execute(
            "INSERT INTO hold_seats(hold_id, seat_id) VALUES ('hold_legacy', 'A-01')"
        )
        connection.execute(
            "INSERT INTO bookings(booking_id, hold_id, session_id, client_token, status, created_at) VALUES ('book_legacy', 'hold_legacy', 'SESSION-001', 'legacy-owner', 'confirmed', '2030-01-01T12:00:00Z')"
        )
        connection.execute(
            "UPDATE session_seats SET state='booked', booking_id='book_legacy' WHERE session_id='SESSION-001' AND seat_id='A-01'"
        )
        connection.commit()
        connection.close()

        migrated = self._make_server(
            "127.0.0.1", 0, db_path=str(self._db), clock=lambda: self.now
        )
        migrated.server_close()
        connection = sqlite3.connect(self._db)
        self.assertEqual(
            connection.execute("SELECT count(*) FROM seats WHERE session_id='SESSION-001'").fetchone()[0],
            50,
        )
        self.assertEqual(
            connection.execute("SELECT count(*) FROM bookings").fetchone()[0], 4
        )
        self.assertEqual(
            connection.execute("SELECT booking_id FROM session_seats WHERE session_id='SESSION-001' AND seat_id='A-01'").fetchone()[0],
            "book_legacy",
        )
        connection.close()

    def test_create_hold_returns_two_minute_server_expiry(self) -> None:
        status, payload = self.hold(["D-08", "D-09"])
        self.assertEqual(status, 201)
        hold = self.data(payload)
        self.assertEqual(hold["seatIds"], ["D-08", "D-09"])
        expires = datetime.fromisoformat(hold["expiresAt"].replace("Z", "+00:00"))
        self.assertEqual(expires, self.now + timedelta(seconds=120))

    def test_hold_is_atomic_when_one_seat_is_taken(self) -> None:
        status, _ = self.hold(["E-01"], token="visitor-A", key="first")
        self.assertEqual(status, 201)
        status, payload = self.hold(
            ["E-02", "E-01"], token="visitor-B", key="all-or-nothing"
        )
        self.assertEqual(status, 409)
        self.assertEqual(self.error_code(payload), "SEAT_UNAVAILABLE")
        status, seat_map, _ = self.request(
            "GET", "/sessions/SESSION-001/seats", token="visitor-B"
        )
        self.assertEqual(status, 200)
        states = {seat["id"]: seat["state"] for seat in self.data(seat_map)["seats"]}
        self.assertEqual(states["E-02"], "available")

    def test_duplicate_unknown_and_empty_seat_requests_are_rejected(self) -> None:
        for seats in ([], ["D-08", "D-08"], ["Z-99"]):
            with self.subTest(seats=seats):
                status, payload = self.hold(seats, key=f"invalid-{len(seats)}-{seats}")
                self.assertIn(status, (400, 422))
                self.assertTrue(self.error_code(payload))

    def test_same_idempotency_key_returns_same_hold(self) -> None:
        first_status, first = self.hold(["D-08"], key="retry-hold")
        second_status, second = self.hold(["D-08"], key="retry-hold")
        self.assertEqual(first_status, 201)
        self.assertEqual(second_status, 201)
        self.assertEqual(self.data(first)["holdId"], self.data(second)["holdId"])

    def test_idempotency_key_cannot_be_reused_for_different_payload(self) -> None:
        self.assertEqual(self.hold(["D-08"], key="fixed-key")[0], 201)
        status, payload = self.hold(["D-09"], key="fixed-key")
        self.assertEqual(status, 409)
        self.assertIn(self.error_code(payload), {"IDEMPOTENCY_KEY_REUSED", "SEAT_UNAVAILABLE"})

    def test_expired_hold_cannot_be_confirmed_and_seats_are_released(self) -> None:
        status, payload = self.hold(["D-08"], key="will-expire")
        self.assertEqual(status, 201)
        hold_id = self.data(payload)["holdId"]
        self.now += timedelta(seconds=121)
        status, confirm, _ = self.request(
            "POST", f"/holds/{hold_id}/confirm", body={}, token="visitor-A",
            idempotency_key="expired-confirm",
        )
        self.assertEqual(status, 409)
        self.assertEqual(self.error_code(confirm), "HOLD_EXPIRED")
        status, seat_map, _ = self.request(
            "GET", "/sessions/SESSION-001/seats", token="visitor-B"
        )
        self.assertEqual(status, 200)
        seat = next(s for s in self.data(seat_map)["seats"] if s["id"] == "D-08")
        self.assertEqual(seat["state"], "available")

    def test_other_clients_see_only_ownership_marker_and_expiry(self) -> None:
        status, _ = self.hold(["D-08"], token="secret-token-alice", key="privacy")
        self.assertEqual(status, 201)
        status, payload, _ = self.request(
            "GET", "/sessions/SESSION-001/seats", token="visitor-B"
        )
        self.assertEqual(status, 200)
        seat = next(s for s in self.data(payload)["seats"] if s["id"] == "D-08")
        self.assertEqual(seat.get("ownership"), "other")
        self.assertTrue(seat.get("heldUntil"))
        serialized = json.dumps(payload)
        self.assertNotIn("secret-token-alice", serialized)
        self.assertNotIn("displayName", serialized)
        self.assertNotIn("visitor-A", serialized)

    def test_display_name_is_not_required_or_used_as_ownership(self) -> None:
        status, _ = self.hold(["D-08"], token="token-A", key="token-ownership")
        self.assertEqual(status, 201)
        status, payload = self.hold(["D-08"], token="token-B", key="same-display-name")
        self.assertEqual(status, 409)
        self.assertEqual(self.error_code(payload), "SEAT_UNAVAILABLE")

    def test_only_owner_can_release_or_confirm_hold(self) -> None:
        status, payload = self.hold(["D-08"], token="visitor-A", key="owner-check")
        self.assertEqual(status, 201)
        hold_id = self.data(payload)["holdId"]
        status, released, _ = self.request(
            "DELETE", f"/holds/{hold_id}", token="visitor-B"
        )
        self.assertIn(status, (403, 409))
        self.assertTrue(self.error_code(released))
        status, confirm, _ = self.request(
            "POST", f"/holds/{hold_id}/confirm", body={}, token="visitor-B",
            idempotency_key="wrong-owner-confirm",
        )
        self.assertIn(status, (403, 409))
        self.assertTrue(self.error_code(confirm))

    def test_concurrent_hold_race_has_exactly_one_winner(self) -> None:
        barrier = threading.Barrier(2)
        results: list[tuple[int, dict[str, Any] | None]] = []
        result_lock = threading.Lock()

        def compete(token: str) -> None:
            barrier.wait(timeout=2)
            result = self.hold(["D-10"], token=token, key=f"race-{token}")
            with result_lock:
                results.append(result)

        threads = [
            threading.Thread(target=compete, args=("visitor-A",)),
            threading.Thread(target=compete, args=("visitor-B",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=4)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sorted(status for status, _ in results), [201, 409])

    def test_confirmation_is_idempotent_and_books_all_seats(self) -> None:
        status, payload = self.hold(["E-03", "E-04"], key="confirm-group")
        self.assertEqual(status, 201)
        hold_id = self.data(payload)["holdId"]
        path = f"/holds/{hold_id}/confirm"
        first_status, first, _ = self.request(
            "POST", path, body={}, token="visitor-A", idempotency_key="confirm-once"
        )
        retry_status, retry, _ = self.request(
            "POST", path, body={}, token="visitor-A", idempotency_key="confirm-once"
        )
        self.assertEqual(first_status, 201)
        self.assertEqual(retry_status, 201)
        self.assertEqual(self.data(first)["bookingId"], self.data(retry)["bookingId"])
        self.assertCountEqual(self.data(first)["seatIds"], ["E-03", "E-04"])
        status, seat_map, _ = self.request(
            "GET", "/sessions/SESSION-001/seats", token="visitor-B"
        )
        states = {s["id"]: s["state"] for s in self.data(seat_map)["seats"]}
        self.assertEqual(states["E-03"], "booked")
        self.assertEqual(states["E-04"], "booked")

    def test_realtime_stream_endpoint_is_available(self) -> None:
        # Basic contract check only; client reconnect/version-gap behavior is in
        # the browser/E2E report and should use the stream's documented format.
        req = urllib.request.Request(
            f"{self.base}/sessions/SESSION-001/seat-events",
            headers={"Accept": "text/event-stream"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=1) as response:
            self.assertIn(response.status, (200, 204))
            self.assertIn("text/event-stream", response.headers.get("Content-Type", ""))


if __name__ == "__main__":
    unittest.main()
