from __future__ import annotations

import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from common import http_json, read_json, route_for, write_cors_preflight, write_json


DRONE_ID = os.getenv("DRONE_ID", "drone-unknown")
BASE_ID = os.getenv("BASE_ID", DRONE_ID)
BASE_X = float(os.getenv("BASE_X", "50"))
BASE_Y = float(os.getenv("BASE_Y", "50"))
PORT = int(os.getenv("PORT", "9000"))
FAIL_RATE = float(os.getenv("FAIL_RATE", "0.12"))
MIN_MISSION_SECONDS = float(os.getenv("MIN_MISSION_SECONDS", "3"))
MAX_MISSION_SECONDS = float(os.getenv("MAX_MISSION_SECONDS", "8"))


class DroneState:
    """Thread-safe local state that enforces one mission per drone."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.status = "idle"
        self.drone_alive = True
        self.base_alive = True
        self.home_base = BASE_ID
        self.command_base = BASE_ID
        self.current_request: str | None = None
        self.current_broker: str | None = None
        self.current_attempt: int | None = None
        self.current_mission: dict[str, Any] | None = None
        self.mission_started_at: float | None = None
        self.mission_ends_at: float | None = None
        self.completed = 0
        self.failed = 0

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            progress = 0.0
            if self.mission_started_at and self.mission_ends_at and self.mission_ends_at > self.mission_started_at:
                progress = (time.time() - self.mission_started_at) / (self.mission_ends_at - self.mission_started_at)
                progress = min(1.0, max(0.0, progress))
            return {
                "drone_id": DRONE_ID,
                "base_id": BASE_ID,
                "home_base": self.home_base,
                "command_base": self.command_base,
                "drone_alive": self.drone_alive,
                "base_alive": self.base_alive,
                "position": {"x": BASE_X, "y": BASE_Y},
                "status": self.status,
                "current_request": self.current_request,
                "current_broker": self.current_broker,
                "current_attempt": self.current_attempt,
                "current_mission": self.current_mission,
                "mission_progress": progress,
                "completed": self.completed,
                "failed": self.failed,
            }


state = DroneState()


def run_mission(request_id: str, broker_id: str, callback_url: str, mission: dict[str, Any], attempt: int) -> None:
    """Simulate mission execution and ACK the broker through its callback URL."""

    duration = random.uniform(MIN_MISSION_SECONDS, MAX_MISSION_SECONDS)
    with state.lock:
        state.mission_started_at = time.time()
        state.mission_ends_at = state.mission_started_at + duration
    time.sleep(duration)
    with state.lock:
        if not state.drone_alive or state.current_request != request_id or state.current_attempt != attempt:
            return
    failed = random.random() < FAIL_RATE
    result = {
        "request_id": request_id,
        "broker_id": broker_id,
        "drone_id": DRONE_ID,
        "command_base": state.snapshot()["command_base"],
        "mission": mission,
        "status": "failed" if failed else "completed",
        "duration_seconds": round(duration, 2),
        "attempt": attempt,
    }
    http_json("POST", callback_url, result, timeout=2)
    with state.lock:
        if failed:
            state.failed += 1
        else:
            state.completed += 1
        if state.current_request == request_id:
            state.status = "idle"
            state.current_request = None
            state.current_broker = None
            state.current_attempt = None
            state.current_mission = None
            state.mission_started_at = None
            state.mission_ends_at = None


class DroneHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        write_cors_preflight(self)

    def do_GET(self) -> None:
        route = route_for(self)
        if route.path == "/health":
            with state.lock:
                alive = state.drone_alive
            write_json(self, 200 if alive else 503, {"ok": alive, "drone_id": DRONE_ID, "base_id": BASE_ID})
            return
        if route.path == "/status":
            write_json(self, 200, state.snapshot())
            return
        write_json(self, 404, {"error": "not found"})

    def do_POST(self) -> None:
        route = route_for(self)
        if route.path == "/control":
            payload = read_json(self)
            target = str(payload.get("target", "drone"))
            action = str(payload.get("action", "restore"))
            alive = action not in {"destroy", "down", "offline"}
            command_base = str(payload.get("command_base", "")).strip()
            with state.lock:
                if target == "drone":
                    state.drone_alive = alive
                    if not alive:
                        state.status = "destroyed"
                        state.current_request = None
                        state.current_broker = None
                        state.current_attempt = None
                        state.current_mission = None
                        state.mission_started_at = None
                        state.mission_ends_at = None
                    elif state.status == "destroyed":
                        state.status = "idle"
                elif target == "base":
                    state.base_alive = alive
                    if alive:
                        state.command_base = BASE_ID
                    elif command_base:
                        state.command_base = command_base
                else:
                    write_json(self, 400, {"updated": False, "error": "target must be drone or base"})
                    return
            write_json(self, 200, {"updated": True, "target": target, "alive": alive, "state": state.snapshot()})
            return

        if route.path != "/reserve":
            write_json(self, 404, {"error": "not found"})
            return

        payload = read_json(self)
        request_id = str(payload.get("request_id", ""))
        broker_id = str(payload.get("broker_id", ""))
        callback_url = str(payload.get("callback_url", ""))
        attempt = int(payload.get("attempt", 1))
        mission = payload.get("mission", {})

        if not request_id or not broker_id or not callback_url:
            write_json(self, 400, {"error": "request_id, broker_id and callback_url are required"})
            return

        with state.lock:
            if not state.drone_alive:
                write_json(self, 503, {"accepted": False, "reason": "drone_destroyed", "drone_id": DRONE_ID})
                return
            if not state.base_alive and state.command_base == BASE_ID:
                write_json(self, 503, {"accepted": False, "reason": "base_destroyed", "drone_id": DRONE_ID})
                return
            if state.status != "idle":
                if state.current_request == request_id and state.current_attempt == attempt:
                    write_json(self, 200, {"accepted": True, "idempotent": True, "drone_id": DRONE_ID})
                    return
                write_json(self, 409, {"accepted": False, "reason": "busy", "drone_id": DRONE_ID})
                return
            state.status = "busy"
            state.current_request = request_id
            state.current_broker = broker_id
            state.current_attempt = attempt
            state.current_mission = mission

        worker = threading.Thread(
            target=run_mission,
            args=(request_id, broker_id, callback_url, mission, attempt),
            daemon=True,
        )
        worker.start()
        write_json(self, 202, {"accepted": True, "drone_id": DRONE_ID})

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{DRONE_ID}] {self.address_string()} - {fmt % args}")


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), DroneHandler)
    print(f"[{DRONE_ID}] listening on port {PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
