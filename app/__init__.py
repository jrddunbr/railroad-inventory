from __future__ import annotations

import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

SCHEMA_VERSION = "1.2.0"


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
    )

    db.init_app(app)

    from app.routes import main_bp

    app.register_blueprint(main_bp)

    with app.app_context():
        db.create_all()
        from app.models import SchemaVersion

        if not SchemaVersion.query.first():
            db.session.add(SchemaVersion(version=SCHEMA_VERSION))
            db.session.commit()

    return app
