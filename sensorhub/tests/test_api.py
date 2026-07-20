"""End-to-end API tests for SensorHub.

Written with the standard library's unittest so they run with zero extra
dependencies (`python -m unittest`), and they are also fully compatible
with pytest (`pytest`).
"""
from __future__ import annotations

import os
import tempfile
import unittest

from sensorhub import create_app


class SensorHubTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.app = create_app({"TESTING": True, "DATABASE": self.db_path})
        self.client = self.app.test_client()

    def tearDown(self):
        os.unlink(self.db_path)

    # -- helpers ----------------------------------------------------------- #
    def _make_device(self, name="press-01", location="line A"):
        res = self.client.post(
            "/api/devices", json={"name": name, "location": location}
        )
        self.assertEqual(res.status_code, 201)
        return res.get_json()

    def _post_reading(self, key, metric="temperature", value=25.0):
        return self.client.post(
            "/api/readings",
            json={"metric": metric, "value": value},
            headers={"X-API-Key": key},
        )

    # -- health ------------------------------------------------------------ #
    def test_health(self):
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["status"], "ok")

    # -- devices ----------------------------------------------------------- #
    def test_create_and_list_devices(self):
        dev = self._make_device()
        self.assertIn("api_key", dev)
        self.assertEqual(dev["name"], "press-01")

        res = self.client.get("/api/devices")
        devices = res.get_json()
        self.assertEqual(len(devices), 1)
        self.assertNotIn("api_key", devices[0])  # key must be redacted

    def test_duplicate_device_name_conflicts(self):
        self._make_device("robot-arm")
        res = self.client.post("/api/devices", json={"name": "robot-arm"})
        self.assertEqual(res.status_code, 409)

    def test_device_requires_name(self):
        res = self.client.post("/api/devices", json={"location": "x"})
        self.assertEqual(res.status_code, 400)

    def test_device_rejects_non_json(self):
        res = self.client.post("/api/devices", data="not json")
        self.assertEqual(res.status_code, 400)

    # -- readings ---------------------------------------------------------- #
    def test_reading_requires_valid_api_key(self):
        self.assertEqual(self._post_reading("wrong-key").status_code, 401)
        res = self.client.post("/api/readings", json={"metric": "t", "value": 1})
        self.assertEqual(res.status_code, 401)

    def test_ingest_and_query_readings(self):
        dev = self._make_device()
        for v in (20.0, 21.5, 23.0):
            self.assertEqual(self._post_reading(dev["api_key"], value=v).status_code, 201)

        res = self.client.get(f"/api/readings?device_id={dev['id']}&metric=temperature")
        rows = res.get_json()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["value"], 23.0)  # newest first

    def test_reading_validation(self):
        dev = self._make_device()
        bad_value = self._post_reading(dev["api_key"], value="hot")
        self.assertEqual(bad_value.status_code, 400)
        no_metric = self.client.post(
            "/api/readings", json={"value": 1},
            headers={"X-API-Key": dev["api_key"]},
        )
        self.assertEqual(no_metric.status_code, 400)

    def test_metric_is_normalized_to_lowercase(self):
        dev = self._make_device()
        self._post_reading(dev["api_key"], metric="Temperature", value=19)
        res = self.client.get(f"/api/readings?device_id={dev['id']}&metric=temperature")
        self.assertEqual(len(res.get_json()), 1)

    def test_limit_is_clamped(self):
        dev = self._make_device()
        self._post_reading(dev["api_key"])
        res = self.client.get("/api/readings?limit=999999")
        self.assertEqual(res.status_code, 200)  # clamped, not an error
        res = self.client.get("/api/readings?limit=banana")
        self.assertEqual(res.status_code, 200)  # falls back to default

    # -- stats ------------------------------------------------------------- #
    def test_stats(self):
        dev = self._make_device()
        for v in (10.0, 20.0, 30.0):
            self._post_reading(dev["api_key"], value=v)
        res = self.client.get(f"/api/stats?device_id={dev['id']}&metric=temperature")
        stats = res.get_json()
        self.assertEqual(stats["count"], 3)
        self.assertEqual(stats["min"], 10.0)
        self.assertEqual(stats["max"], 30.0)
        self.assertEqual(stats["avg"], 20.0)
        self.assertEqual(stats["latest"]["value"], 30.0)

    def test_stats_missing_params_and_empty_series(self):
        self.assertEqual(self.client.get("/api/stats").status_code, 400)
        dev = self._make_device()
        res = self.client.get(f"/api/stats?device_id={dev['id']}&metric=nothing")
        self.assertEqual(res.status_code, 404)

    # -- rules & alerts ---------------------------------------------------- #
    def test_alert_fires_when_threshold_breached(self):
        dev = self._make_device()
        rule = self.client.post(
            "/api/rules",
            json={"device_id": dev["id"], "metric": "temperature",
                  "op": "gt", "threshold": 80},
        )
        self.assertEqual(rule.status_code, 201)

        ok = self._post_reading(dev["api_key"], value=75)
        self.assertEqual(ok.get_json()["alerts_fired"], 0)

        hot = self._post_reading(dev["api_key"], value=95)
        self.assertEqual(hot.get_json()["alerts_fired"], 1)

        alerts = self.client.get("/api/alerts").get_json()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["value"], 95)
        self.assertEqual(alerts[0]["op"], "gt")

    def test_lt_rule(self):
        dev = self._make_device()
        self.client.post(
            "/api/rules",
            json={"device_id": dev["id"], "metric": "pressure",
                  "op": "lt", "threshold": 1.0},
        )
        low = self._post_reading(dev["api_key"], metric="pressure", value=0.4)
        self.assertEqual(low.get_json()["alerts_fired"], 1)

    def test_rule_validation(self):
        dev = self._make_device()
        bad_op = self.client.post(
            "/api/rules",
            json={"device_id": dev["id"], "metric": "t", "op": "eq", "threshold": 1},
        )
        self.assertEqual(bad_op.status_code, 400)
        missing_device = self.client.post(
            "/api/rules",
            json={"device_id": 9999, "metric": "t", "op": "gt", "threshold": 1},
        )
        self.assertEqual(missing_device.status_code, 404)


if __name__ == "__main__":
    unittest.main()
