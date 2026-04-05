from __future__ import annotations

import os
import secrets
import time
from datetime import timedelta

from flask import Flask, g
from werkzeug.middleware.proxy_fix import ProxyFix

from app.storage import db

SCHEMA_VERSION = "2.13.0"
DEFAULT_LOCATION_TYPES = ["bag", "carrier", "flat", "staging_track", "yard_track", "box"]


def _read_env_value(env_path: str, key: str) -> str | None:
    if not os.path.exists(env_path):
        return None
    with open(env_path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return None


def _upsert_env_value(env_path: str, key: str, value: str) -> None:
    lines: list[str] = []
    found = False
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as handle:
            lines = handle.readlines()
    updated_lines: list[str] = []
    for line in lines:
        if line.startswith(f"{key}="):
            updated_lines.append(f"{key}={value}\n")
            found = True
        else:
            updated_lines.append(line)
    if not found:
        if updated_lines and not updated_lines[-1].endswith("\n"):
            updated_lines[-1] = f"{updated_lines[-1]}\n"
        updated_lines.append(f"{key}={value}\n")
    with open(env_path, "w", encoding="utf-8") as handle:
        handle.writelines(updated_lines)


def ensure_local_secret_key(project_root: str) -> str:
    configured = (os.environ.get("SECRET_KEY") or "").strip()
    if configured and configured != "dev-secret-key":
        return configured
    env_file = os.environ.get("ENV_FILE") or os.path.join(project_root, ".env")
    file_value = (_read_env_value(env_file, "SECRET_KEY") or "").strip()
    if file_value and file_value != "dev-secret-key":
        os.environ["SECRET_KEY"] = file_value
        return file_value
    generated = secrets.token_urlsafe(48)
    os.makedirs(os.path.dirname(env_file) or ".", exist_ok=True)
    _upsert_env_value(env_file, "SECRET_KEY", generated)
    os.environ["SECRET_KEY"] = generated
    return generated


def create_app() -> Flask:
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[assignment]
    base_dir = os.path.abspath(os.path.dirname(__file__))
    project_root = os.path.dirname(base_dir)
    secret_key = ensure_local_secret_key(project_root)
    os.makedirs(os.path.join(os.path.dirname(base_dir), "data"), exist_ok=True)

    couchdb_url = os.environ.get("COUCHDB_URL")
    if not couchdb_url:
        user = os.environ.get("COUCHDB_USER", "admin")
        password = os.environ.get("COUCHDB_PASSWORD", "admin")
        host = os.environ.get("COUCHDB_HOST", "127.0.0.1")
        port = os.environ.get("COUCHDB_PORT", "5984")
        couchdb_url = f"http://{user}:{password}@{host}:{port}/"

    app.config.update(
        COUCHDB_URL=couchdb_url,
        COUCHDB_DATABASE=os.environ.get("COUCHDB_DATABASE", "model_inventory"),
        APP_STARTED_AT=time.time(),
        COUCHDB_COUNTERS=[
            "railroads",
            "car_classes",
            "locations",
            "cars",
            "consists",
            "loads",
            "load_placements",
            "car_inspections",
            "inspection_types",
            "railroad_color_schemes",
            "railroad_logos",
            "railroad_slogans",
            "app_settings",
            "tool_items",
            "part_items",
            "users",
        ],
        COUCHDB_TOTALS=[
            {"doc_type": "railroad", "counter_key": "railroads"},
            {"doc_type": "car_class", "counter_key": "car_classes"},
            {"doc_type": "location", "counter_key": "locations"},
            {"doc_type": "car", "counter_key": "cars"},
            {"doc_type": "consist", "counter_key": "consists"},
            {"doc_type": "load", "counter_key": "loads"},
            {"doc_type": "load_placement", "counter_key": "load_placements"},
            {"doc_type": "car_inspection", "counter_key": "car_inspections"},
            {"doc_type": "inspection_type", "counter_key": "inspection_types"},
            {"doc_type": "railroad_color_scheme", "counter_key": "railroad_color_schemes"},
            {"doc_type": "railroad_logo", "counter_key": "railroad_logos"},
            {"doc_type": "railroad_slogan", "counter_key": "railroad_slogans"},
            {"doc_type": "app_settings", "counter_key": "app_settings"},
            {"doc_type": "tool_item", "counter_key": "tool_items"},
            {"doc_type": "part_item", "counter_key": "part_items"},
            {"doc_type": "user", "counter_key": "users"},
        ],
        SCHEMA_VERSION=SCHEMA_VERSION,
        SECRET_KEY=secret_key,
        MAX_CONTENT_LENGTH=32 * 1024 * 1024,
        LOGO_UPLOAD_FOLDER=os.path.join(base_dir, "static", "uploads", "railroad-logos"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"},
        PERMANENT_SESSION_LIFETIME=timedelta(hours=1),
        SESSION_REFRESH_EACH_REQUEST=True,
        AUTH_SESSION_OVERALL_LIFETIME=timedelta(hours=24),
        AUTH_PENDING_LIFETIME=timedelta(minutes=10),
        AUTH_RECENT_AUTH_LIFETIME=timedelta(minutes=15),
        AUTH_LOCKOUT_LIFETIME=timedelta(minutes=15),
        AUTH_FAILURE_LIMIT=3,
        AUTH_PASSWORD_THROTTLE_WINDOW=timedelta(minutes=15),
        AUTH_PASSWORD_THROTTLE_LIMIT=5,
        AUTH_PASSWORD_THROTTLE_MAX_DELAY_SECONDS=30,
        WEBAUTHN_RP_NAME=os.environ.get("WEBAUTHN_RP_NAME", "Railroad Inventory"),
        WEBAUTHN_RP_ID=os.environ.get("WEBAUTHN_RP_ID", ""),
        WEBAUTHN_ORIGIN=os.environ.get("WEBAUTHN_ORIGIN", ""),
    )
    os.makedirs(app.config["LOGO_UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)

    from app.routes import main_bp

    app.register_blueprint(main_bp)

    @app.before_request
    def start_timer() -> None:
        g.request_start = time.perf_counter()
        g.db_time = 0.0

    @app.context_processor
    def inject_timing() -> dict:
        start = getattr(g, "request_start", None)
        if start is None:
            return {"page_timing": None}
        total_ms = (time.perf_counter() - start) * 1000
        db_ms = getattr(g, "db_time", 0.0) * 1000
        return {"page_timing": {"total_ms": total_ms, "db_ms": db_ms}}

    with app.app_context():
        from app.models import InspectionType, Location

        db_types = sorted({loc.location_type for loc in Location.query.all() if loc.location_type})
        merged_types = DEFAULT_LOCATION_TYPES + [
            location_type for location_type in db_types if location_type not in DEFAULT_LOCATION_TYPES
        ]
        app.config["LOCATION_TYPES"] = merged_types
        for inspection_name in ("NMRA Weight Check", "NMRA Weight Check (Loaded)"):
            if not InspectionType.query.filter_by(name=inspection_name).first():
                db.session.add(InspectionType(name=inspection_name))
                db.session.commit()

    return app
