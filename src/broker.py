from __future__ import annotations

import heapq
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from common import env_list, http_json, now_ms, read_json, route_for, write_cors_preflight, write_json


BROKER_ID = os.getenv("BROKER_ID", "broker-a")
SECTOR_ID = os.getenv("SECTOR_ID", "sector-a")
PORT = int(os.getenv("PORT", "8000"))
PUBLIC_URL = os.getenv("PUBLIC_URL", f"http://{BROKER_ID}:{PORT}")
PEERS = env_list("PEERS")
DRONES = env_list("DRONES")
ALERT_THRESHOLD = int(os.getenv("ALERT_THRESHOLD", "7"))
CLAIM_TTL_SECONDS = float(os.getenv("CLAIM_TTL_SECONDS", "10"))
RETRY_SECONDS = float(os.getenv("RETRY_SECONDS", "3"))


class BrokerState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.sensors: dict[str, dict[str, Any]] = {}
        self.requests: dict[str, dict[str, Any]] = {}
        self.queue: list[tuple[int, int, str]] = []
        self.seen_forwarded: set[str] = set()

    def enqueue(self, request: dict[str, Any]) -> None:
        with self.lock:
            request_id = request["request_id"]
            stored = self.requests.get(request_id)
            if stored and stored.get("status") in {"dispatched", "completed"}:
                return
            self.requests[request_id] = {**request, **(stored or {}), "status": (stored or {}).get("status", "queued")}
            priority = -int(request.get("criticality", 1))
            created_at = int(request.get("created_at_ms", now_ms()))
            heapq.heappush(self.queue, (priority, created_at, request_id))

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "broker_id": BROKER_ID,
                "sector_id": SECTOR_ID,
                "peers": PEERS,
                "drones": DRONES,
                "sensors": self.sensors,
                "requests": self.requests,
                "queue_size": len(self.queue),
            }


state = BrokerState()


def build_request(payload: dict[str, Any]) -> dict[str, Any]:
    request_id = str(payload.get("request_id") or payload.get("event_id") or uuid.uuid4())
    return {
        "request_id": request_id,
        "origin_broker": payload.get("origin_broker", BROKER_ID),
        "origin_url": payload.get("origin_url", PUBLIC_URL),
        "sector_id": payload.get("sector_id", SECTOR_ID),
        "criticality": int(payload.get("criticality", payload.get("severity", 5))),
        "event_type": payload.get("event_type", "manual_request"),
        "area": {
            "lat": payload.get("lat"),
            "lon": payload.get("lon"),
        },
        "created_at_ms": int(payload.get("created_at_ms", payload.get("timestamp_ms", now_ms()))),
        "details": payload,
    }


def forward_to_peers(request: dict[str, Any]) -> None:
    for peer in PEERS:
        http_json("POST", f"{peer}/enqueue-peer", request, timeout=1.5)


def claim_origin(request: dict[str, Any]) -> tuple[bool, str | None]:
    request_id = request["request_id"]
    if request["origin_broker"] == BROKER_ID:
        with state.lock:
            stored = state.requests.get(request_id)
            if not stored or stored.get("status") in {"dispatched", "completed"}:
                return False, None
            claim_expires_at = float(stored.get("claim_expires_at", 0))
            if stored.get("status") == "claimed" and claim_expires_at > time.time():
                return False, None
            token = str(uuid.uuid4())
            stored["status"] = "claimed"
            stored["claim_token"] = token
            stored["claim_owner"] = BROKER_ID
            stored["claim_expires_at"] = time.time() + CLAIM_TTL_SECONDS
            return True, token

    status, payload = http_json(
        "POST",
        f"{request['origin_url']}/claim-request",
        {"request_id": request_id, "helper_broker": BROKER_ID},
        timeout=2,
    )
    if status == 200 and payload.get("granted"):
        return True, str(payload["claim_token"])
    return False, None


def release_origin(request: dict[str, Any], token: str) -> None:
    if request["origin_broker"] == BROKER_ID:
        with state.lock:
            stored = state.requests.get(request["request_id"])
            if stored and stored.get("claim_token") == token and stored.get("status") == "claimed":
                stored["status"] = "queued"
                stored.pop("claim_token", None)
                stored.pop("claim_owner", None)
                stored.pop("claim_expires_at", None)
                state.enqueue(stored)
        return

    http_json(
        "POST",
        f"{request['origin_url']}/release-claim",
        {"request_id": request["request_id"], "claim_token": token},
        timeout=1.5,
    )


def confirm_assignment(request: dict[str, Any], token: str, drone_id: str, drone_url: str) -> None:
    payload = {
        "request_id": request["request_id"],
        "claim_token": token,
        "assigned_drone": drone_id,
        "assigned_drone_url": drone_url,
        "assigned_by": BROKER_ID,
    }
    if request["origin_broker"] == BROKER_ID:
        apply_assignment(payload)
    else:
        http_json("POST", f"{request['origin_url']}/assignment", payload, timeout=2)


def apply_assignment(payload: dict[str, Any]) -> bool:
    with state.lock:
        stored = state.requests.get(str(payload.get("request_id")))
        if not stored or stored.get("claim_token") != payload.get("claim_token"):
            return False
        stored["status"] = "dispatched"
        stored["assigned_drone"] = payload.get("assigned_drone")
        stored["assigned_drone_url"] = payload.get("assigned_drone_url")
        stored["assigned_by"] = payload.get("assigned_by")
        return True


def try_dispatch(request: dict[str, Any]) -> bool:
    granted, token = claim_origin(request)
    if not granted or token is None:
        return False

    callback_url = f"{request['origin_url']}/drone-result"
    for drone_url in DRONES:
        status, payload = http_json(
            "POST",
            f"{drone_url}/reserve",
            {
                "request_id": request["request_id"],
                "broker_id": BROKER_ID,
                "callback_url": callback_url,
                "mission": request,
            },
            timeout=2,
        )
        if status in {200, 202} and payload.get("accepted"):
            confirm_assignment(request, token, str(payload.get("drone_id", drone_url)), drone_url)
            with state.lock:
                local = state.requests.get(request["request_id"])
                if local:
                    local["status"] = "dispatched"
                    local["assigned_drone"] = payload.get("drone_id", drone_url)
            print(f"[{BROKER_ID}] dispatched {request['request_id']} to {payload.get('drone_id', drone_url)}")
            return True

    release_origin(request, token)
    return False


def scheduler_loop() -> None:
    while True:
        request: dict[str, Any] | None = None
        with state.lock:
            while state.queue:
                _, _, request_id = heapq.heappop(state.queue)
                candidate = state.requests.get(request_id)
                if candidate and candidate.get("status") in {"queued", "failed"}:
                    request = candidate
                    break
        if request is None:
            time.sleep(0.5)
            continue

        dispatched = try_dispatch(request)
        if not dispatched:
            time.sleep(RETRY_SECONDS)
            with state.lock:
                stored = state.requests.get(request["request_id"])
                if stored and stored.get("status") in {"queued", "failed"}:
                    stored["status"] = "queued"
                    state.enqueue(stored)


class BrokerHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        write_cors_preflight(self)

    def do_GET(self) -> None:
        route = route_for(self)
        if route.path == "/health":
            write_json(self, 200, {"ok": True, "broker_id": BROKER_ID, "sector_id": SECTOR_ID})
            return
        if route.path == "/state":
            write_json(self, 200, state.snapshot())
            return
        write_json(self, 404, {"error": "not found"})

    def do_POST(self) -> None:
        route = route_for(self)
        payload = read_json(self)

        if route.path == "/sensor":
            sensor_id = str(payload.get("sensor_id", "unknown"))
            with state.lock:
                state.sensors[sensor_id] = payload
            if payload.get("requires_drone") or int(payload.get("severity", 0)) >= ALERT_THRESHOLD:
                request = build_request(payload)
                state.enqueue(request)
                threading.Thread(target=forward_to_peers, args=(request,), daemon=True).start()
                write_json(self, 202, {"queued": True, "request_id": request["request_id"]})
                return
            write_json(self, 200, {"queued": False, "sensor_id": sensor_id})
            return

        if route.path == "/request-drone":
            request = build_request(payload)
            state.enqueue(request)
            threading.Thread(target=forward_to_peers, args=(request,), daemon=True).start()
            write_json(self, 202, {"queued": True, "request_id": request["request_id"]})
            return

        if route.path == "/enqueue-peer":
            request = build_request(payload)
            with state.lock:
                if request["request_id"] in state.seen_forwarded:
                    write_json(self, 200, {"queued": False, "reason": "already_seen"})
                    return
                state.seen_forwarded.add(request["request_id"])
            state.enqueue(request)
            write_json(self, 202, {"queued": True, "request_id": request["request_id"]})
            return

        if route.path == "/claim-request":
            request_id = str(payload.get("request_id", ""))
            helper = str(payload.get("helper_broker", "unknown"))
            with state.lock:
                stored = state.requests.get(request_id)
                if not stored or stored.get("status") in {"dispatched", "completed"}:
                    write_json(self, 409, {"granted": False})
                    return
                claim_expires_at = float(stored.get("claim_expires_at", 0))
                if stored.get("status") == "claimed" and claim_expires_at > time.time():
                    write_json(self, 409, {"granted": False, "reason": "already_claimed"})
                    return
                token = str(uuid.uuid4())
                stored["status"] = "claimed"
                stored["claim_token"] = token
                stored["claim_owner"] = helper
                stored["claim_expires_at"] = time.time() + CLAIM_TTL_SECONDS
            write_json(self, 200, {"granted": True, "claim_token": token})
            return

        if route.path == "/release-claim":
            request_id = str(payload.get("request_id", ""))
            token = str(payload.get("claim_token", ""))
            with state.lock:
                stored = state.requests.get(request_id)
                if stored and stored.get("claim_token") == token and stored.get("status") == "claimed":
                    stored["status"] = "queued"
                    stored.pop("claim_token", None)
                    stored.pop("claim_owner", None)
                    stored.pop("claim_expires_at", None)
                    state.enqueue(stored)
            write_json(self, 200, {"released": True})
            return

        if route.path == "/assignment":
            ok = apply_assignment(payload)
            write_json(self, 200 if ok else 409, {"accepted": ok})
            return

        if route.path == "/drone-result":
            request_id = str(payload.get("request_id", ""))
            with state.lock:
                stored = state.requests.get(request_id)
                if stored:
                    stored["last_result"] = payload
                    if payload.get("status") == "completed":
                        stored["status"] = "completed"
                    else:
                        stored["status"] = "failed"
                        stored.pop("assigned_drone", None)
                        state.enqueue(stored)
            write_json(self, 200, {"received": True})
            return

        write_json(self, 404, {"error": "not found"})

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{BROKER_ID}] {self.address_string()} - {fmt % args}")


def main() -> None:
    threading.Thread(target=scheduler_loop, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), BrokerHandler)
    print(f"[{BROKER_ID}] sector={SECTOR_ID} listening on port {PORT}")
    print(f"[{BROKER_ID}] peers={PEERS} drones={DRONES}")
    server.serve_forever()


if __name__ == "__main__":
    main()
