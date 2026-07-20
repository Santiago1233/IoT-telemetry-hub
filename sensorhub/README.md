# SensorHub

A lightweight IoT telemetry hub: devices stream sensor readings into a REST API, threshold rules fire alerts, and a live dashboard visualizes everything in real time.

Built with **Flask + SQLite** and a **zero-dependency frontend** (the chart is ~40 lines of hand-written canvas code — no chart library). The test suite runs with the standard library alone.

> Why this project? I come from a mechatronics background, so I built the kind of tool I'd actually deploy next to real hardware: a small server a CNC spindle or HVAC unit could report to over HTTP.

## Features

- **Device registry** — register devices, each gets a unique API key (shown once, redacted afterward)
- **Telemetry ingestion** — authenticated `POST /api/readings` with per-device API keys
- **Querying** — filter readings by device, metric, and time range, with clamped pagination
- **Aggregation** — `GET /api/stats` returns min/max/avg/count plus the latest reading
- **Alert rules** — define `gt`/`lt` thresholds per device+metric; alerts fire on ingestion
- **Live dashboard** — polling UI with stat tiles, a custom canvas chart, and an alert feed
- **Device simulator** — `scripts/simulate.py` streams noisy sine-wave telemetry for demos

## Quick start

```bash
git clone https://github.com/<you>/sensorhub && cd sensorhub
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python run.py                      # terminal 1 — server on http://127.0.0.1:5000
python scripts/simulate.py         # terminal 2 — stream demo telemetry
```

Open <http://127.0.0.1:5000> and watch the data flow in.

## API reference

| Method | Path            | Auth        | Description                                    |
|--------|-----------------|-------------|------------------------------------------------|
| GET    | `/api/health`   | —           | Liveness probe                                 |
| POST   | `/api/devices`  | —           | Register a device → returns its `api_key`      |
| GET    | `/api/devices`  | —           | List devices (keys redacted)                   |
| POST   | `/api/readings` | `X-API-Key` | Ingest a reading `{metric, value}`             |
| GET    | `/api/readings` | —           | Query: `device_id`, `metric`, `since`, `until`, `limit` |
| GET    | `/api/stats`    | —           | `device_id` + `metric` → min/max/avg/count/latest |
| POST   | `/api/rules`    | —           | `{device_id, metric, op: gt\|lt, threshold}`   |
| GET    | `/api/rules`    | —           | List alert rules                               |
| GET    | `/api/alerts`   | —           | Fired alerts, newest first                     |

### Example

```bash
# register a device
curl -X POST localhost:5000/api/devices \
  -H 'Content-Type: application/json' \
  -d '{"name": "press-01", "location": "line A"}'
# → {"id": 1, "name": "press-01", ..., "api_key": "3f9a..."}

# send a reading
curl -X POST localhost:5000/api/readings \
  -H 'Content-Type: application/json' -H 'X-API-Key: 3f9a...' \
  -d '{"metric": "temperature", "value": 72.4}'

# get stats
curl 'localhost:5000/api/stats?device_id=1&metric=temperature'
```

## Architecture

```
sensorhub/
├── sensorhub/
│   ├── __init__.py     # app factory (create_app)
│   ├── api.py          # REST endpoints (Blueprint)
│   └── db.py           # SQLite schema + request-scoped connections
├── static/index.html   # dashboard (vanilla JS, custom canvas chart)
├── scripts/simulate.py # stdlib-only device simulator
├── tests/test_api.py   # 15 end-to-end tests (unittest, pytest-compatible)
└── .github/workflows/ci.yml
```

Design decisions:

- **App factory pattern** so tests spin up isolated instances against temp databases.
- **SQLite over Postgres** — right-sized for the workload, zero setup, and an index on `(device_id, metric, recorded_at)` keeps series queries fast.
- **Alert evaluation at ingestion time** rather than a polling worker: simpler, no race conditions, and alerts are atomic with the reading that caused them (single transaction).
- **API keys per device, not per user** — mirrors how real fleet telemetry works; a compromised device only compromises itself.

## Tests

```bash
python -m unittest discover -s tests -v   # no dependencies needed
```

15 tests cover authentication, validation, conflict handling, metric normalization, limit clamping, aggregation math, and both alert rule directions. CI runs them on Python 3.11 and 3.12 on every push.

## License

MIT
