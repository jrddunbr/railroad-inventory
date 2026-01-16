from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(BASE_DIR))

from app import create_app, db  # noqa: E402
from app.models import (  # noqa: E402
    Car,
    CarClass,
    LoadPlacement,
    LoadType,
    Location,
    Railroad,
    RailroadColorScheme,
    RailroadLogo,
    RailroadSlogan,
)


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cursor.fetchone() is not None


def fetch_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, object]]:
    cursor = conn.execute(f"SELECT * FROM {table}")
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def to_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


def migrate_railroads(conn: sqlite3.Connection) -> None:
    for row in fetch_rows(conn, "railroads"):
        railroad = Railroad(
            id=int(row["id"]),
            reporting_mark=row.get("reporting_mark"),
            name=row.get("name"),
            start_date=row.get("start_date"),
            end_date=row.get("end_date"),
            merged_into=row.get("merged_into"),
            merged_from=row.get("merged_from"),
            notes=row.get("notes"),
            representative_logo_id=row.get("representative_logo_id"),
        )
        db.session.add(railroad)


def migrate_car_classes(conn: sqlite3.Connection) -> None:
    for row in fetch_rows(conn, "car_classes"):
        car_class = CarClass(
            id=int(row["id"]),
            code=row.get("code"),
            car_type=row.get("car_type"),
            wheel_arrangement=row.get("wheel_arrangement"),
            tender_axles=row.get("tender_axles"),
            is_locomotive=to_bool(row.get("is_locomotive")),
            era=row.get("era"),
            load_limit=row.get("load_limit"),
            capacity=row.get("capacity"),
            weight=row.get("weight"),
            notes=row.get("notes"),
            internal_length=row.get("internal_length"),
            internal_width=row.get("internal_width"),
            internal_height=row.get("internal_height"),
        )
        db.session.add(car_class)


def migrate_locations(conn: sqlite3.Connection) -> None:
    for row in fetch_rows(conn, "locations"):
        location = Location(
            id=int(row["id"]),
            name=row.get("name"),
            location_type=row.get("location_type"),
            parent_id=row.get("parent_id"),
        )
        db.session.add(location)


def migrate_cars(conn: sqlite3.Connection) -> None:
    for row in fetch_rows(conn, "cars"):
        car = Car(
            id=int(row["id"]),
            railroad_id=row.get("railroad_id"),
            car_class_id=row.get("car_class_id"),
            location_id=row.get("location_id"),
            car_number=row.get("car_number"),
            reporting_mark_override=row.get("reporting_mark_override"),
            brand=row.get("brand"),
            upc=row.get("upc"),
            dcc_id=row.get("dcc_id"),
            traction_drivers=to_bool(row.get("traction_drivers")),
            car_type_override=row.get("car_type_override"),
            wheel_arrangement_override=row.get("wheel_arrangement_override"),
            tender_axles_override=row.get("tender_axles_override"),
            is_locomotive_override=to_bool(row.get("is_locomotive_override")),
            capacity_override=row.get("capacity_override"),
            weight_override=row.get("weight_override"),
            load_limit_override=row.get("load_limit_override"),
            built=row.get("built"),
            alt_date=row.get("alt_date"),
            reweight_date=row.get("reweight_date"),
            repack_bearings_date=row.get("repack_bearings_date"),
            other_lettering=row.get("other_lettering"),
            msrp=row.get("msrp"),
            price=row.get("price"),
            load=row.get("load"),
            repairs_required=row.get("repairs_required"),
            notes=row.get("notes"),
            internal_length_override=row.get("internal_length_override"),
            internal_width_override=row.get("internal_width_override"),
            internal_height_override=row.get("internal_height_override"),
        )
        db.session.add(car)


def migrate_loads(conn: sqlite3.Connection) -> None:
    for row in fetch_rows(conn, "loads"):
        load = LoadType(
            id=int(row["id"]),
            name=row.get("name"),
            car_class_id=row.get("car_class_id"),
            railroad_id=row.get("railroad_id"),
            era=row.get("era"),
            brand=row.get("brand"),
            lettering=row.get("lettering"),
            msrp=row.get("msrp"),
            price=row.get("price"),
            upc=row.get("upc"),
            length=row.get("length"),
            width=row.get("width"),
            height=row.get("height"),
            repairs_required=row.get("repairs_required"),
            notes=row.get("notes"),
        )
        db.session.add(load)


def migrate_load_placements(conn: sqlite3.Connection) -> None:
    for row in fetch_rows(conn, "load_placements"):
        placement = LoadPlacement(
            id=int(row["id"]),
            load_id=row.get("load_id"),
            car_id=row.get("car_id"),
            location_id=row.get("location_id"),
            quantity=row.get("quantity") or 1,
        )
        db.session.add(placement)


def migrate_railroad_color_schemes(conn: sqlite3.Connection) -> None:
    for row in fetch_rows(conn, "railroad_color_schemes"):
        scheme = RailroadColorScheme(
            id=int(row["id"]),
            railroad_id=row.get("railroad_id"),
            description=row.get("description"),
            start_date=row.get("start_date"),
            end_date=row.get("end_date"),
            colors=row.get("colors"),
        )
        db.session.add(scheme)


def migrate_railroad_logos(conn: sqlite3.Connection) -> None:
    for row in fetch_rows(conn, "railroad_logos"):
        logo = RailroadLogo(
            id=int(row["id"]),
            railroad_id=row.get("railroad_id"),
            description=row.get("description"),
            start_date=row.get("start_date"),
            end_date=row.get("end_date"),
            image_path=row.get("image_path"),
        )
        db.session.add(logo)


def migrate_railroad_slogans(conn: sqlite3.Connection) -> None:
    for row in fetch_rows(conn, "railroad_slogans"):
        slogan = RailroadSlogan(
            id=int(row["id"]),
            railroad_id=row.get("railroad_id"),
            description=row.get("description"),
            slogan_text=row.get("slogan_text"),
            start_date=row.get("start_date"),
            end_date=row.get("end_date"),
        )
        db.session.add(slogan)


def main(sqlite_path: Path) -> None:
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite database not found at {sqlite_path}")
    app = create_app()
    with app.app_context():
        conn = sqlite3.connect(sqlite_path)
        conn.row_factory = sqlite3.Row

        if table_exists(conn, "railroads"):
            migrate_railroads(conn)
        if table_exists(conn, "car_classes"):
            migrate_car_classes(conn)
        if table_exists(conn, "locations"):
            migrate_locations(conn)
        if table_exists(conn, "cars"):
            migrate_cars(conn)
        if table_exists(conn, "loads"):
            migrate_loads(conn)
        if table_exists(conn, "load_placements"):
            migrate_load_placements(conn)
        if table_exists(conn, "railroad_color_schemes"):
            migrate_railroad_color_schemes(conn)
        if table_exists(conn, "railroad_logos"):
            migrate_railroad_logos(conn)
        if table_exists(conn, "railroad_slogans"):
            migrate_railroad_slogans(conn)

        db.session.commit()
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate a SQLite inventory database into CouchDB.")
    parser.add_argument("sqlite_path", type=Path, help="Path to the SQLite database file.")
    args = parser.parse_args()
    main(args.sqlite_path)
