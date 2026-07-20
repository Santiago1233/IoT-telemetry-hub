"""SensorHub — a lightweight IoT telemetry hub built with Flask + SQLite."""
from __future__ import annotations

from pathlib import Path

from flask import Flask, send_from_directory

__version__ = "1.0.0"

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(config: dict | None = None) -> Flask:
    """Application factory.

    Pass ``{"DATABASE": ":memory:" or a path}`` to override the default DB
    location (used by the test suite).
    """
    app = Flask(__name__, static_folder=None)
    app.config["DATABASE"] = str(
        Path(__file__).resolve().parent.parent / "sensorhub.db"
    )
    if config:
        app.config.update(config)

    from . import api, db

    db.init_db(app)
    app.register_blueprint(api.bp)

    @app.get("/")
    def dashboard():
        return send_from_directory(STATIC_DIR, "index.html")

    return app
