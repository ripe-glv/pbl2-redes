from __future__ import annotations

import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from common import http_json, read_json, route_for, write_cors_preflight, write_json


DRONE_ID = os.getenv("DRONE_ID", "drone-unknown")
PORT = int(os.getenv("PORT", "9000"))
FAIL_RATE = float(os.getenv("FAIL_RATE", "0.12"))
MIN_MISSION_SECONDS = float(os.getenv("MIN_MISSION_SECONDS", "3"))
MAX_MISSION_SECONDS = float(os.getenv("MAX_MISSION_SECONDS", "8"))


class DroneState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.status = "idle"
        self.current_request: str | None = None
        self.current_broker: str | None = None
        self.completed = 0
        self.failed = 0

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "drone_id": DRONE_ID,
                "status": self.status,
                "current_request": self.current_request,
                "current_broker": self.current_broker,
                "completed": self.completed,
                "failed": self.failed,
            }


state = DroneState()


def run_mission(request_id: str, broker_id: str, callback_url: str, mission: dict[str, Any]) -> None:
    duration = random.uniform(MIN_MISSION_SECONDS, MAX_MISSION_SECONDS)
    time.sleep(duration)
    failed = random.random() < FAIL_RATE
    result = {
        "request_id": request_id,
        "broker_id": broker_id,
        "drone_id": DRONE_ID,
        "mission": mission,
        "status": "failed" if failed else "completed",
        "duration_seconds": round(duration, 2),
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


class DroneHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        write_cors_preflight(self)

    def do_GET(self) -> None:
        route = route_for(self)
        if route.path == "/health":
            write_json(self, 200, {"ok": True, "drone_id": DRONE_ID})
            return
        if route.path == "/status":
            write_json(self, 200, state.snapshot())
            return
        write_json(self, 404, {"error": "not found"})

    def do_POST(self) -> None:
        route = route_for(self)
        if route.path != "/reserve":
            write_json(self, 404, {"error": "not found"})
            return

        payload = read_json(self)
        request_id = str(payload.get("request_id", ""))
        broker_id = str(payload.get("broker_id", ""))
        callback_url = str(payload.get("callback_url", ""))
        mission = payload.get("mission", {})

        if not request_id or not broker_id or not callback_url:
            write_json(self, 400, {"error": "request_id, broker_id and callback_url are required"})
            return

        with state.lock:
            if state.status != "idle":
                if state.current_request == request_id:
                    write_json(self, 200, {"accepted": True, "idempotent": True, "drone_id": DRONE_ID})
                    return
                write_json(self, 409, {"accepted": False, "reason": "busy", "drone_id": DRONE_ID})
                return
            state.status = "busy"
            state.current_request = request_id
            state.current_broker = broker_id

        worker = threading.Thread(
            target=run_mission,
            args=(request_id, broker_id, callback_url, mission),
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
