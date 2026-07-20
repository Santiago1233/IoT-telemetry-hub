"""REST API blueprint for SensorHub.

Endpoints
---------
GET  /api/health                     Liveness probe.
POST /api/devices                    Register a device -> returns its api_key.
GET  /api/devices                    List devices (keys redacted).
POST /api/readings                   Ingest a reading (X-API-Key auth).
GET  /api/readings                   Query readings with filters.
GET  /api/stats                      min/max/avg/count + latest for a series.
POST /api/rules                      Create a threshold alert rule.
GET  /api/rules                      List rules.
GET  /api/alerts                     List fired alerts (newest first).
"""
from __future__ import annotations

import secrets
import sqlite3
from typing import Any

from flask import Blueprint, jsonify, request

from .db import get_db

bp = Blueprint("api", __name__, url_prefix="/api")

MAX_LIMIT = 1000


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _error(message: str, status: int):
    return jsonify({"error": message}), status


def _json_body() -> dict[str, Any] | None:
    """Return the request's JSON body as a dict, or None if invalid."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _authed_device() -> sqlite3.Row | None:
    """Resolve the device that owns the X-API-Key header, if any."""
    key = request.headers.get("X-API-Key", "")
    if not key:
        return None
    return (
        get_db()
        .execute("SELECT * FROM devices WHERE api_key = ?", (key,))
        .fetchone()
    )


def _parse_limit(default: int = 100) -> int:
    try:
        limit = int(request.args.get("limit", default))
    except ValueError:
        return default
    return max(1, min(limit, MAX_LIMIT))


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
@bp.get("/health")
def health():
    return jsonify({"status": "ok"})


# --------------------------------------------------------------------------- #
# Devices
# --------------------------------------------------------------------------- #
@bp.post("/devices")
def create_device():
    body = _json_body()
    if body is None:
        return _error("request body must be a JSON object", 400)
    name = str(body.get("name", "")).strip()
    if not name:
        return _error("'name' is required", 400)
    location = str(body.get("location", "")).strip()

    api_key = secrets.token_hex(16)
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO devices (name, location, api_key) VALUES (?, ?, ?)",
            (name, location, api_key),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return _error(f"device '{name}' already exists", 409)

    return (
        jsonify(
            {
                "id": cur.lastrowid,
                "name": name,
                "location": location,
                "api_key": api_key,  # shown once, at creation
            }
        ),
        201,
    )


@bp.get("/devices")
def list_devices():
    rows = get_db().execute(
        "SELECT id, name, location, created_at FROM devices ORDER BY id"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# --------------------------------------------------------------------------- #
# Readings
# --------------------------------------------------------------------------- #
@bp.post("/readings")
def ingest_reading():
    device = _authed_device()
    if device is None:
        return _error("missing or invalid X-API-Key header", 401)

    body = _json_body()
    if body is None:
        return _error("request body must be a JSON object", 400)

    metric = str(body.get("metric", "")).strip().lower()
    if not metric:
        return _error("'metric' is required", 400)
    try:
        value = float(body["value"])
    except (KeyError, TypeError, ValueError):
        return _error("'value' must be a number", 400)

    db = get_db()
    cur = db.execute(
        "INSERT INTO readings (device_id, metric, value) VALUES (?, ?, ?)",
        (device["id"], metric, value),
    )
    reading_id = cur.lastrowid

    # Evaluate alert rules for this series.
    fired = []
    rules = db.execute(
        "SELECT * FROM rules WHERE device_id = ? AND metric = ?",
        (device["id"], metric),
    ).fetchall()
    for rule in rules:
        breached = (rule["op"] == "gt" and value > rule["threshold"]) or (
            rule["op"] == "lt" and value < rule["threshold"]
        )
        if breached:
            db.execute(
                """INSERT INTO alerts
                   (rule_id, device_id, metric, value, threshold, op)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (rule["id"], device["id"], metric, value, rule["threshold"], rule["op"]),
            )
            fired.append(rule["id"])
    db.commit()

    return jsonify({"id": reading_id, "alerts_fired": len(fired)}), 201


@bp.get("/readings")
def query_readings():
    filters, params = [], []
    if device_id := request.args.get("device_id"):
        filters.append("device_id = ?")
        params.append(device_id)
    if metric := request.args.get("metric"):
        filters.append("metric = ?")
        params.append(metric.lower())
    if since := request.args.get("since"):
        filters.append("recorded_at >= ?")
        params.append(since)
    if until := request.args.get("until"):
        filters.append("recorded_at <= ?")
        params.append(until)

    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    rows = get_db().execute(
        f"""SELECT id, device_id, metric, value, recorded_at
            FROM readings {where}
            ORDER BY recorded_at DESC, id DESC
            LIMIT ?""",
        (*params, _parse_limit()),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@bp.get("/stats")
def series_stats():
    device_id = request.args.get("device_id")
    metric = request.args.get("metric", "").lower()
    if not device_id or not metric:
        return _error("'device_id' and 'metric' query params are required", 400)

    db = get_db()
    agg = db.execute(
        """SELECT COUNT(*) AS count, MIN(value) AS min, MAX(value) AS max,
                  AVG(value) AS avg
           FROM readings WHERE device_id = ? AND metric = ?""",
        (device_id, metric),
    ).fetchone()
    if agg["count"] == 0:
        return _error("no readings for that device/metric", 404)

    latest = db.execute(
        """SELECT value, recorded_at FROM readings
           WHERE device_id = ? AND metric = ?
           ORDER BY recorded_at DESC, id DESC LIMIT 1""",
        (device_id, metric),
    ).fetchone()

    return jsonify(
        {
            "device_id": int(device_id),
            "metric": metric,
            "count": agg["count"],
            "min": agg["min"],
            "max": agg["max"],
            "avg": round(agg["avg"], 4),
            "latest": dict(latest),
        }
    )


# --------------------------------------------------------------------------- #
# Rules & alerts
# --------------------------------------------------------------------------- #
@bp.post("/rules")
def create_rule():
    body = _json_body()
    if body is None:
        return _error("request body must be a JSON object", 400)

    try:
        device_id = int(body["device_id"])
        threshold = float(body["threshold"])
    except (KeyError, TypeError, ValueError):
        return _error("'device_id' and 'threshold' must be numbers", 400)
    metric = str(body.get("metric", "")).strip().lower()
    op = str(body.get("op", "")).strip().lower()
    if not metric or op not in ("gt", "lt"):
        return _error("'metric' is required and 'op' must be 'gt' or 'lt'", 400)

    db = get_db()
    exists = db.execute(
        "SELECT 1 FROM devices WHERE id = ?", (device_id,)
    ).fetchone()
    if not exists:
        return _error(f"device {device_id} not found", 404)

    cur = db.execute(
        "INSERT INTO rules (device_id, metric, op, threshold) VALUES (?, ?, ?, ?)",
        (device_id, metric, op, threshold),
    )
    db.commit()
    return jsonify({"id": cur.lastrowid, "device_id": device_id,
                    "metric": metric, "op": op, "threshold": threshold}), 201


@bp.get("/rules")
def list_rules():
    rows = get_db().execute("SELECT * FROM rules ORDER BY id").fetchall()
    return jsonify([dict(r) for r in rows])


@bp.get("/alerts")
def list_alerts():
    rows = get_db().execute(
        "SELECT * FROM alerts ORDER BY created_at DESC, id DESC LIMIT ?",
        (_parse_limit(),),
    ).fetchall()
    return jsonify([dict(r) for r in rows])
