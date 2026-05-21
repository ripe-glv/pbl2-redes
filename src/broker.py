from __future__ import annotations

import heapq
import math
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
AREA_X = float(os.getenv("AREA_X", "50"))
AREA_Y = float(os.getenv("AREA_Y", "50"))
AREA_SIZE = float(os.getenv("AREA_SIZE", "20"))
PEERS = env_list("PEERS")
DRONES = env_list("DRONES")
ALERT_THRESHOLD = int(os.getenv("ALERT_THRESHOLD", "7"))
CLAIM_TTL_SECONDS = float(os.getenv("CLAIM_TTL_SECONDS", "10"))
RETRY_SECONDS = float(os.getenv("RETRY_SECONDS", "3"))
MISSION_TIMEOUT_SECONDS = float(os.getenv("MISSION_TIMEOUT_SECONDS", "20"))
MAP_LAT_MIN = 24.0
MAP_LAT_RANGE = 4.8
MAP_LON_MIN = 52.0
MAP_LON_RANGE = 6.8


def clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def map_point_to_geo(x: float, y: float) -> tuple[float, float]:
    lat = MAP_LAT_MIN + ((100 - y) / 100) * MAP_LAT_RANGE
    lon = MAP_LON_MIN + (x / 100) * MAP_LON_RANGE
    return round(lat, 5), round(lon, 5)


def geo_to_map_point(lat: float, lon: float) -> tuple[float, float]:
    x = ((lon - MAP_LON_MIN) / MAP_LON_RANGE) * 100
    y = 100 - ((lat - MAP_LAT_MIN) / MAP_LAT_RANGE) * 100
    return x, y


def request_map_point(request: dict[str, Any]) -> tuple[float, float]:
    area = request.get("area") if isinstance(request.get("area"), dict) else {}
    lat = area.get("lat")
    lon = area.get("lon")
    if lat is None or lon is None:
        return AREA_X, AREA_Y
    return geo_to_map_point(float(lat), float(lon))


def point_distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return math.hypot(left[0] - right[0], left[1] - right[1])


def clamp_to_local_area(lat: float | None, lon: float | None) -> tuple[float, float]:
    half = AREA_SIZE / 2
    min_x = clamp(AREA_X - half, 0, 100)
    max_x = clamp(AREA_X + half, 0, 100)
    min_y = clamp(AREA_Y - half, 0, 100)
    max_y = clamp(AREA_Y + half, 0, 100)

    if lat is None or lon is None:
        return map_point_to_geo(AREA_X, AREA_Y)

    x, y = geo_to_map_point(float(lat), float(lon))
    return map_point_to_geo(clamp(x, min_x, max_x), clamp(y, min_y, max_y))


class BrokerState:
    """Thread-safe broker memory for sensors, requests and the priority queue."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.sensors: dict[str, dict[str, Any]] = {}
        self.requests: dict[str, dict[str, Any]] = {}
        self.queue: list[tuple[int, int, str]] = []
        self.seen_forwarded: set[str] = set()
        self.broker_alive = True
        self.sensor_alive = True

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
                "broker_alive": self.broker_alive,
                "sensor_alive": self.sensor_alive,
                "position": {"x": AREA_X, "y": AREA_Y},
                "area_size": AREA_SIZE,
                "peers": PEERS,
                "drones": DRONES,
                "sensors": self.sensors,
                "requests": self.requests,
                "queue_size": len(self.queue),
            }


state = BrokerState()


def build_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize sensor/manual payloads into the request format shared by brokers."""

    request_id = str(payload.get("request_id") or payload.get("event_id") or uuid.uuid4())
    existing_area = payload.get("area") if isinstance(payload.get("area"), dict) else {}
    lat = payload.get("lat", existing_area.get("lat"))
    lon = payload.get("lon", existing_area.get("lon"))
    origin_broker = payload.get("origin_broker", BROKER_ID)
    if origin_broker == BROKER_ID:
        lat, lon = clamp_to_local_area(float(lat) if lat is not None else None, float(lon) if lon is not None else None)
    return {
        "request_id": request_id,
        "origin_broker": origin_broker,
        "origin_url": payload.get("origin_url", PUBLIC_URL),
        "sector_id": payload.get("sector_id", SECTOR_ID),
        "criticality": int(payload.get("criticality", payload.get("severity", 5))),
        "event_type": payload.get("event_type", "manual_request"),
        "area": {
            "lat": lat,
            "lon": lon,
        },
        "created_at_ms": int(payload.get("created_at_ms", payload.get("timestamp_ms", now_ms()))),
        "details": payload,
    }


def forward_to_peers(request: dict[str, Any]) -> None:
    """Replicate a request to peer brokers and rely on their ACK/HTTP status."""

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
    if status == 409 and payload.get("reason") in {"completed", "dispatched"}:
        with state.lock:
            stored = state.requests.get(request_id)
            if stored:
                stored["status"] = str(payload["reason"])
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


def confirm_assignment(
    request: dict[str, Any],
    token: str,
    drone_id: str,
    drone_url: str,
    attempt: int,
    distance: float | None = None,
) -> None:
    payload = {
        "request_id": request["request_id"],
        "claim_token": token,
        "assigned_drone": drone_id,
        "assigned_drone_url": drone_url,
        "assigned_by": BROKER_ID,
        "attempt": attempt,
        "assigned_at_ms": now_ms(),
        "mission_deadline_ms": now_ms() + int(MISSION_TIMEOUT_SECONDS * 1000),
        "assigned_distance": None if distance is None else round(distance, 2),
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
        stored["attempt"] = int(payload.get("attempt", 1))
        stored["assigned_at_ms"] = payload.get("assigned_at_ms")
        stored["mission_deadline_ms"] = payload.get("mission_deadline_ms")
        stored["assigned_distance"] = payload.get("assigned_distance")
        return True


def is_drone_available(status_payload: dict[str, Any]) -> bool:
    if not status_payload.get("drone_alive"):
        return False
    if status_payload.get("status") != "idle":
        return False
    base_id = str(status_payload.get("base_id", ""))
    command_base = str(status_payload.get("command_base", ""))
    return bool(status_payload.get("base_alive")) or (command_base and command_base != base_id)


def available_drones_by_distance(request: dict[str, Any]) -> list[dict[str, Any]]:
    target = request_map_point(request)
    candidates: list[dict[str, Any]] = []
    for order, drone_url in enumerate(DRONES):
        status, payload = http_json("GET", f"{drone_url}/status", timeout=1.2)
        if status != 200 or not is_drone_available(payload):
            continue

        position = payload.get("position") if isinstance(payload.get("position"), dict) else {}
        drone_point = (float(position.get("x", 50)), float(position.get("y", 50)))
        distance = point_distance(target, drone_point)
        candidates.append(
            {
                "url": drone_url,
                "status": payload,
                "distance": distance,
                "order": order,
            }
        )
    return sorted(candidates, key=lambda item: (item["distance"], item["order"]))


def try_dispatch(request: dict[str, Any]) -> bool:
    granted, token = claim_origin(request)
    if not granted or token is None:
        return False

    callback_url = f"{request['origin_url']}/drone-result"
    attempt = int(request.get("attempt", 0)) + 1
    for candidate in available_drones_by_distance(request):
        drone_url = str(candidate["url"])
        status, payload = http_json(
            "POST",
            f"{drone_url}/reserve",
            {
                "request_id": request["request_id"],
                "broker_id": BROKER_ID,
                "callback_url": callback_url,
                "attempt": attempt,
                "mission": {**request, "attempt": attempt},
            },
            timeout=2,
        )
        if status in {200, 202} and payload.get("accepted"):
            now = now_ms()
            deadline = now + int(MISSION_TIMEOUT_SECONDS * 1000)
            drone_id = str(payload.get("drone_id", drone_url))
            confirm_assignment(request, token, drone_id, drone_url, attempt, float(candidate["distance"]))
            with state.lock:
                local = state.requests.get(request["request_id"])
                if local:
                    local["status"] = "dispatched"
                    local["assigned_drone"] = drone_id
                    local["assigned_drone_url"] = drone_url
                    local["assigned_by"] = BROKER_ID
                    local["attempt"] = attempt
                    local["assigned_at_ms"] = now
                    local["mission_deadline_ms"] = deadline
                    local["assigned_distance"] = round(float(candidate["distance"]), 2)
            print(f"[{BROKER_ID}] dispatched {request['request_id']} to {drone_id} distance={candidate['distance']:.2f}")
            return True

    release_origin(request, token)
    return False


def recover_timed_out_assignments() -> None:
    """Return stale dispatched requests to the queue when a drone never calls back."""

    current_ms = now_ms()
    with state.lock:
        for request_id, request in list(state.requests.items()):
            if request.get("status") != "dispatched" or request.get("origin_broker") != BROKER_ID:
                continue
            deadline = int(request.get("mission_deadline_ms") or 0)
            if deadline <= 0 or deadline > current_ms:
                continue
            request["status"] = "failed"
            request["last_result"] = {
                "request_id": request_id,
                "status": "timeout",
                "reason": "mission callback deadline exceeded",
                "attempt": request.get("attempt", 0),
            }
            request.pop("assigned_drone", None)
            request.pop("assigned_drone_url", None)
            request.pop("mission_deadline_ms", None)
            state.enqueue(request)
            print(f"[{BROKER_ID}] requeued {request_id} after mission timeout")


def scheduler_loop() -> None:
    while True:
        recover_timed_out_assignments()
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
            with state.lock:
                alive = state.broker_alive
            write_json(self, 200 if alive else 503, {"ok": alive, "broker_id": BROKER_ID, "sector_id": SECTOR_ID})
            return
        if route.path == "/state":
            write_json(self, 200, state.snapshot())
            return
        write_json(self, 404, {"error": "not found"})

    def do_POST(self) -> None:
        route = route_for(self)
        payload = read_json(self)

        if route.path == "/control":
            target = str(payload.get("target", "broker"))
            action = str(payload.get("action", "restore"))
            alive = action not in {"destroy", "down", "offline"}
            with state.lock:
                if target == "broker":
                    state.broker_alive = alive
                    if not alive:
                        state.queue.clear()
                elif target == "sensor":
                    state.sensor_alive = alive
                else:
                    write_json(self, 400, {"updated": False, "error": "target must be broker or sensor"})
                    return
            write_json(self, 200, {"updated": True, "target": target, "alive": alive, "state": state.snapshot()})
            return

        with state.lock:
            broker_alive = state.broker_alive
            sensor_alive = state.sensor_alive

        if not broker_alive:
            write_json(self, 503, {"error": "broker destroyed", "broker_id": BROKER_ID})
            return

        if route.path == "/sensor":
            if not sensor_alive:
                write_json(self, 503, {"error": "sensor destroyed", "broker_id": BROKER_ID})
                return
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
            if not sensor_alive:
                write_json(self, 503, {"error": "area inactive: broker and sensor must be active", "broker_id": BROKER_ID})
                return
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
                if not stored:
                    write_json(self, 409, {"granted": False, "reason": "not_found"})
                    return
                if stored.get("status") in {"dispatched", "completed"}:
                    write_json(self, 409, {"granted": False, "reason": stored.get("status")})
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
                    result_attempt = payload.get("attempt")
                    current_attempt = stored.get("attempt")
                    if result_attempt is not None and current_attempt is not None and int(result_attempt) != int(current_attempt):
                        write_json(self, 409, {"received": False, "reason": "stale_attempt"})
                        return
                    stored["last_result"] = payload
                    if payload.get("status") == "completed":
                        stored["status"] = "completed"
                        stored.pop("claim_token", None)
                        stored.pop("claim_owner", None)
                        stored.pop("claim_expires_at", None)
                        stored.pop("mission_deadline_ms", None)
                    else:
                        stored["status"] = "failed"
                        stored.pop("assigned_drone", None)
                        stored.pop("assigned_drone_url", None)
                        stored.pop("mission_deadline_ms", None)
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
