from __future__ import annotations

import os
import time

from flask import Flask, g

from app.storage import db

SCHEMA_VERSION = "2.4.0"
DEFAULT_LOCATION_TYPES = ["bag", "carrier", "flat", "staging_track", "yard_track", "box"]


def create_app() -> Flask:
    app = Flask(__name__)
    base_dir = os.path.abspath(os.path.dirname(__file__))
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
        COUCHDB_COUNTERS=[
            "railroads",
            "car_classes",
            "locations",
            "cars",
            "loads",
            "load_placements",
            "car_inspections",
            "inspection_types",
            "railroad_color_schemes",
            "railroad_logos",
            "railroad_slogans",
            "app_settings",
        ],
        COUCHDB_TOTALS=[
            {"doc_type": "railroad", "counter_key": "railroads"},
            {"doc_type": "car_class", "counter_key": "car_classes"},
            {"doc_type": "location", "counter_key": "locations"},
            {"doc_type": "car", "counter_key": "cars"},
            {"doc_type": "load", "counter_key": "loads"},
            {"doc_type": "load_placement", "counter_key": "load_placements"},
            {"doc_type": "car_inspection", "counter_key": "car_inspections"},
            {"doc_type": "inspection_type", "counter_key": "inspection_types"},
            {"doc_type": "railroad_color_scheme", "counter_key": "railroad_color_schemes"},
            {"doc_type": "railroad_logo", "counter_key": "railroad_logos"},
            {"doc_type": "railroad_slogan", "counter_key": "railroad_slogans"},
            {"doc_type": "app_settings", "counter_key": "app_settings"},
        ],
        SCHEMA_VERSION=SCHEMA_VERSION,
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-key"),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        LOGO_UPLOAD_FOLDER=os.path.join(base_dir, "static", "uploads", "railroad-logos"),
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
        if not InspectionType.query.filter_by(name="NMRA Weight Check").first():
            db.session.add(InspectionType(name="NMRA Weight Check"))
            db.session.commit()

    return app
