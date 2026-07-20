"""Simulate IoT devices streaming telemetry into SensorHub.

Registers demo devices (if needed), sets alert rules, then posts noisy
sine-wave readings forever. Run it next to a running server and open the
dashboard to watch data flow in live.

Usage:
    python scripts/simulate.py [--host http://127.0.0.1:5000] [--period 1.0]

Uses only the standard library (urllib), so no extra installs are needed.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
import urllib.error
import urllib.request

DEVICES = [
    {"name": "cnc-spindle-01", "location": "machine shop"},
    {"name": "hvac-roof-unit", "location": "building B"},
]

# metric: (baseline, amplitude, alert rule)
METRICS = {
    "temperature": (45.0, 20.0, {"op": "gt", "threshold": 60.0}),
    "vibration": (2.0, 1.5, {"op": "gt", "threshold": 3.2}),
}


def call(host: str, path: str, payload: dict | None = None,
         headers: dict | None = None) -> dict | list:
    req = urllib.request.Request(
        f"{host}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req) as res:
        return json.loads(res.read())


def ensure_devices(host: str) -> list[dict]:
    """Register demo devices, or fail loudly if they already exist.

    API keys are only revealed at creation time, so re-runs against an old
    database need a fresh DB (delete sensorhub.db) or their own devices.
    """
    registered = []
    for spec in DEVICES:
        try:
            dev = call(host, "/api/devices", spec)
        except urllib.error.HTTPError as err:
            if err.code == 409:
                raise SystemExit(
                    f"Device '{spec['name']}' already exists. Delete "
                    "sensorhub.db and restart the server for a fresh demo."
                ) from err
            raise
        registered.append(dev)
        for metric, (_, _, rule) in METRICS.items():
            call(host, "/api/rules",
                 {"device_id": dev["id"], "metric": metric, **rule})
        print(f"registered {dev['name']} (id={dev['id']})")
    return registered


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="http://127.0.0.1:5000")
    parser.add_argument("--period", type=float, default=1.0,
                        help="seconds between readings per device")
    parser.add_argument("--iterations", type=int, default=0,
                        help="stop after N cycles (0 = run forever)")
    args = parser.parse_args()

    devices = ensure_devices(args.host)
    print("streaming telemetry — Ctrl+C to stop")

    t, cycles = 0.0, 0
    while args.iterations == 0 or cycles < args.iterations:
        for i, dev in enumerate(devices):
            for metric, (base, amp, _) in METRICS.items():
                value = (base
                         + amp * math.sin(t / 7 + i * 1.3)
                         + random.uniform(-amp * 0.2, amp * 0.2))
                res = call(args.host, "/api/readings",
                           {"metric": metric, "value": round(value, 3)},
                           headers={"X-API-Key": dev["api_key"]})
                if res.get("alerts_fired"):
                    print(f"  ALERT  {dev['name']} {metric}={value:.2f}")
        t += 1
        cycles += 1
        time.sleep(args.period)


if __name__ == "__main__":
    main()
