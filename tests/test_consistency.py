from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
BROKERS = [
    ("broker-a", "area-1-north-route", "http://127.0.0.1:18101"),
    ("broker-b", "area-2-central-route", "http://127.0.0.1:18102"),
    ("broker-c", "area-3-south-route", "http://127.0.0.1:18103"),
    ("broker-d", "area-4-east-route", "http://127.0.0.1:18104"),
    ("broker-e", "area-5-west-route", "http://127.0.0.1:18105"),
]
DRONES = [
    ("drone-base-1", "http://127.0.0.1:19101"),
    ("drone-base-2", "http://127.0.0.1:19102"),
    ("drone-base-3", "http://127.0.0.1:19103"),
]
DRONE_POSITIONS = {
    "drone-base-1": (12, 82),
    "drone-base-2": (50, 50),
    "drone-base-3": (86, 24),
}


def http_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: float = 2.0) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"content-type": "application/json"}, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        return json.loads(body) if body else {}


def wait_for(url: str, timeout: float = 8.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            http_json("GET", url, timeout=0.5)
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.1)
    raise AssertionError(f"service did not become ready: {url}")


class DistributedConsistencyTest(unittest.TestCase):
    processes: list[subprocess.Popen[bytes]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.processes = []
        drone_urls = ",".join(url for _, url in DRONES)

        for index, (drone_id, url) in enumerate(DRONES, start=1):
            base_x, base_y = DRONE_POSITIONS[drone_id]
            env = {
                **os.environ,
                "DRONE_ID": drone_id,
                "PORT": str(19100 + index),
                "BASE_X": str(base_x),
                "BASE_Y": str(base_y),
                "FAIL_RATE": "0",
                "MIN_MISSION_SECONDS": "0.1",
                "MAX_MISSION_SECONDS": "0.2",
            }
            cls.processes.append(
                subprocess.Popen([sys.executable, "drone.py"], cwd=SRC, env=env, stdout=subprocess.DEVNULL)
            )

        for index, (broker_id, sector_id, public_url) in enumerate(BROKERS, start=1):
            peers = ",".join(url for peer_id, _, url in BROKERS if peer_id != broker_id)
            env = {
                **os.environ,
                "BROKER_ID": broker_id,
                "SECTOR_ID": sector_id,
                "PORT": str(18100 + index),
                "PUBLIC_URL": public_url,
                "PEERS": peers,
                "DRONES": drone_urls,
                "RETRY_SECONDS": "0.2",
                "CLAIM_TTL_SECONDS": "1",
                "MISSION_TIMEOUT_SECONDS": "3",
                "AREA_SIZE": "100",
            }
            cls.processes.append(
                subprocess.Popen([sys.executable, "broker.py"], cwd=SRC, env=env, stdout=subprocess.DEVNULL)
            )

        for _, url in DRONES:
            wait_for(f"{url}/health")

    def wait_for_idle_drones(self, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            statuses = [http_json("GET", f"{url}/status") for _, url in DRONES]
            if all(status["status"] == "idle" for status in statuses):
                return
            time.sleep(0.1)
        self.fail(f"drones did not become idle: {statuses}")
        for _, _, url in BROKERS:
            wait_for(f"{url}/health")

    @classmethod
    def tearDownClass(cls) -> None:
        for process in cls.processes:
            process.terminate()
        for process in cls.processes:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()

    def test_same_replicated_request_is_dispatched_once(self) -> None:
        before_states = [http_json("GET", f"{url}/status") for _, url in DRONES]
        completed_before = sum(int(drone["completed"]) for drone in before_states)
        request_id = "same-area-load-test"
        payload = {
            "request_id": request_id,
            "origin_broker": "broker-a",
            "origin_url": "http://127.0.0.1:18101",
            "event_type": "unknown_object",
            "criticality": 10,
            "lat": 26.55,
            "lon": 56.25,
        }

        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [
                pool.submit(http_json, "POST", f"{url}/request-drone", payload)
                for _, _, url in BROKERS
                for _ in range(3)
            ]
            for future in futures:
                future.result(timeout=3)

        deadline = time.time() + 8
        origin_request: dict[str, Any] | None = None
        while time.time() < deadline:
            state = http_json("GET", "http://127.0.0.1:18101/state")
            origin_request = state["requests"].get(request_id)
            if origin_request and origin_request.get("status") == "completed":
                break
            time.sleep(0.2)

        self.assertIsNotNone(origin_request)
        self.assertEqual(origin_request["status"], "completed")
        self.assertEqual(origin_request.get("attempt"), 1)

        drone_states = [http_json("GET", f"{url}/status") for _, url in DRONES]
        completed_total = sum(int(drone["completed"]) for drone in drone_states)
        self.assertEqual(completed_total - completed_before, 1, drone_states)

    def test_many_concurrent_requests_are_completed_without_busy_drone_overlap(self) -> None:
        payloads = [
            {
                "request_id": f"bulk-{index}",
                "event_type": "traffic_congestion",
                "criticality": 7 + (index % 4),
                "lat": 25.0 + index / 100,
                "lon": 55.0 + index / 100,
            }
            for index in range(15)
        ]

        with ThreadPoolExecutor(max_workers=15) as pool:
            futures = [
                pool.submit(http_json, "POST", f"{BROKERS[index % len(BROKERS)][2]}/request-drone", payload)
                for index, payload in enumerate(payloads)
            ]
            for future in futures:
                future.result(timeout=3)

        expected_ids = {payload["request_id"] for payload in payloads}
        deadline = time.time() + 12
        completed: set[str] = set()
        while time.time() < deadline and completed != expected_ids:
            completed.clear()
            for _, _, url in BROKERS:
                state = http_json("GET", f"{url}/state")
                for request_id, request in state["requests"].items():
                    if request_id in expected_ids and request.get("status") == "completed":
                        completed.add(request_id)
            time.sleep(0.2)

        final_states = [http_json("GET", f"{url}/state") for _, _, url in BROKERS]
        self.assertEqual(completed, expected_ids, final_states)
        for _, url in DRONES:
            status = http_json("GET", f"{url}/status")
            self.assertIn(status["status"], {"idle", "busy"})

    def test_nearest_available_drone_is_selected(self) -> None:
        self.wait_for_idle_drones()
        request_id = "nearest-drone-selection-test"
        payload = {
            "request_id": request_id,
            "event_type": "urgent_visual_inspection",
            "criticality": 10,
            "lat": 27.45,
            "lon": 57.85,
        }

        http_json("POST", "http://127.0.0.1:18101/request-drone", payload)

        deadline = time.time() + 8
        origin_request: dict[str, Any] | None = None
        while time.time() < deadline:
            state = http_json("GET", "http://127.0.0.1:18101/state")
            origin_request = state["requests"].get(request_id)
            if origin_request and origin_request.get("status") == "completed":
                break
            time.sleep(0.2)

        self.assertIsNotNone(origin_request)
        self.assertEqual(origin_request["status"], "completed")
        self.assertEqual(origin_request.get("assigned_drone"), "drone-base-3")
        self.assertIsInstance(origin_request.get("assigned_distance"), float)

    def test_destroyed_base_reroutes_living_drone_to_nearest_base(self) -> None:
        target = DRONES[0]
        fallback = DRONES[1]
        http_json("POST", f"{target[1]}/control", {"target": "base", "action": "destroy", "command_base": fallback[0]})

        status = http_json("GET", f"{target[1]}/status")
        self.assertFalse(status["base_alive"])
        self.assertTrue(status["drone_alive"])
        self.assertEqual(status["command_base"], fallback[0])

        http_json("POST", f"{target[1]}/control", {"target": "base", "action": "restore"})
        restored = http_json("GET", f"{target[1]}/status")
        self.assertTrue(restored["base_alive"])
        self.assertEqual(restored["command_base"], target[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
