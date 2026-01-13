from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from flask import Flask
from sqlalchemy import text
from flask_sqlalchemy import SQLAlchemy

SCHEMA_VERSION = "1.7.0"
DEFAULT_LOCATION_TYPES = ["bag", "carrier", "flat", "staging_track", "yard_track", "box"]


db = SQLAlchemy()


def create_app() -> Flask:
    app = Flask(__name__)
    base_dir = os.path.abspath(os.path.dirname(__file__))
    data_dir = os.path.join(os.path.dirname(base_dir), "data")
    os.makedirs(data_dir, exist_ok=True)
    db_path = os.path.join(data_dir, "inventory.db")

    app.config.update(
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_path}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-key"),
        DB_PATH=db_path,
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        LOGO_UPLOAD_FOLDER=os.path.join(base_dir, "static", "uploads", "railroad-logos"),
    )
    os.makedirs(app.config["LOGO_UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)

    from app.routes import main_bp

    app.register_blueprint(main_bp)

    def parse_version(value: str) -> tuple[int, ...]:
        return tuple(int(part) for part in re.findall(r"\d+", value))

    def column_exists(table: str, column: str) -> bool:
        result = db.session.execute(text(f"PRAGMA table_info({table})")).fetchall()
        return any(row[1] == column for row in result)

    def table_exists(table: str) -> bool:
        result = db.session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:table"),
            {"table": table},
        ).fetchone()
        return result is not None

    with app.app_context():
        db.create_all()
        from app.models import Location, SchemaVersion

        schema_row = SchemaVersion.query.first()
        if not schema_row:
            schema_row = SchemaVersion(version=SCHEMA_VERSION)
            db.session.add(schema_row)
            db.session.commit()
        else:
            current = parse_version(schema_row.version)
            target = parse_version(SCHEMA_VERSION)
            if current < target:
                db_path = app.config.get("DB_PATH")
                if db_path:
                    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    backup_name = (
                        f"inventory-schema-backup-{timestamp}-from-{schema_row.version}-to-{SCHEMA_VERSION}.db"
                    )
                    backup_path = os.path.join(os.path.dirname(db_path), backup_name)
                    if not os.path.exists(backup_path):
                        shutil.copy2(db_path, backup_path)
                schema_row.version = SCHEMA_VERSION
                db.session.commit()

        if not column_exists("car_classes", "era"):
            db.session.execute(text("ALTER TABLE car_classes ADD COLUMN era TEXT"))
        if not column_exists("cars", "repack_bearings_date"):
            db.session.execute(text("ALTER TABLE cars ADD COLUMN repack_bearings_date TEXT"))
        if table_exists("railroads") and not column_exists("railroads", "representative_logo_id"):
            db.session.execute(text("ALTER TABLE railroads ADD COLUMN representative_logo_id INTEGER"))
        if table_exists("railroad_color_schemes") and not column_exists("railroad_color_schemes", "colors"):
            db.session.execute(text("ALTER TABLE railroad_color_schemes ADD COLUMN colors TEXT"))
        if table_exists("railroad_logos") and not column_exists("railroad_logos", "image_path"):
            db.session.execute(text("ALTER TABLE railroad_logos ADD COLUMN image_path TEXT"))
        if table_exists("railroad_slogans") and not column_exists("railroad_slogans", "slogan_text"):
            db.session.execute(text("ALTER TABLE railroad_slogans ADD COLUMN slogan_text TEXT"))
        if table_exists("car_classes") and not column_exists("car_classes", "internal_length"):
            db.session.execute(text("ALTER TABLE car_classes ADD COLUMN internal_length TEXT"))
        if table_exists("car_classes") and not column_exists("car_classes", "internal_width"):
            db.session.execute(text("ALTER TABLE car_classes ADD COLUMN internal_width TEXT"))
        if table_exists("car_classes") and not column_exists("car_classes", "internal_height"):
            db.session.execute(text("ALTER TABLE car_classes ADD COLUMN internal_height TEXT"))
        if table_exists("cars") and not column_exists("cars", "internal_length_override"):
            db.session.execute(text("ALTER TABLE cars ADD COLUMN internal_length_override TEXT"))
        if table_exists("cars") and not column_exists("cars", "internal_width_override"):
            db.session.execute(text("ALTER TABLE cars ADD COLUMN internal_width_override TEXT"))
        if table_exists("cars") and not column_exists("cars", "internal_height_override"):
            db.session.execute(text("ALTER TABLE cars ADD COLUMN internal_height_override TEXT"))
        db.session.commit()

        db_types = [row[0] for row in db.session.query(Location.location_type).distinct().all()]
        merged_types = DEFAULT_LOCATION_TYPES + sorted(
            [location_type for location_type in db_types if location_type and location_type not in DEFAULT_LOCATION_TYPES]
        )
        app.config["LOCATION_TYPES"] = merged_types

    return app
