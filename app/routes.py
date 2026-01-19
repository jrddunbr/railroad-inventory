from __future__ import annotations

import csv
import io
import math
import os
import re
from datetime import datetime
from typing import Optional

from flask import Blueprint, Response, current_app, jsonify, redirect, render_template, request, url_for
from PIL import Image, ImageDraw, ImageFont
from werkzeug.utils import secure_filename

from app import db
from app.backup import ensure_periodic_backup
from app.models import (
    AppSettings,
    Car,
    CarInspection,
    CarClass,
    InspectionType,
    Location,
    LoadPlacement,
    LoadType,
    Railroad,
    RailroadColorScheme,
    RailroadLogo,
    RailroadSlogan,
)


main_bp = Blueprint("main", __name__)

PAGINATION_OPTIONS = ["25", "50", "100", "250", "all"]
DEFAULT_PAGE_SIZE = "50"
DEFAULT_SCALE_OPTIONS = [
    "G|1:22.5",
    "F|1:20.3",
    "O|1:48",
    "S|1:64",
    "HO|1:87",
    "TT|1:120",
    "N|1:160",
    "Z|1:220",
    "1:6",
    "1:8",
    "1:12",
    "1:16",
    "1:24",
    "1:29",
    "1:32",
]
DEFAULT_GAUGE_OPTIONS = [
    "16.5 mm|HO, HOn3, HOn30",
    "12 mm|TT",
    "9 mm|N, Nn3",
    "6.5 mm|Z",
    "14.2 mm|OO",
    "18.2 mm|S",
    "21 mm|Sn3",
    "32 mm|O, On30, On3",
    "45 mm|G, 1:20.3, 1:22.5, 1:29",
    "64 mm|1",
    "89 mm|2",
    "127 mm|3",
    "3.5 in|1 in scale",
    "4.75 in|1 in scale",
    "5 in|1 in scale",
    "7.25 in|1.5 in scale",
    "7.5 in|1.5 in scale",
    "10.25 in|2 in scale",
    "12 in|3 in scale",
    "15 in|3.5 in scale",
]
NMRA_WEIGHT_CHECK_NAME = "NMRA Weight Check"
NMRA_WEIGHT_CHECK_LOADED_NAME = "NMRA Weight Check (Loaded)"
NMRA_WEIGHT_BY_SCALE = {
    "O": (5.0, 1.0),
    "On3": (1.5, 0.75),
    "S": (2.0, 0.5),
    "Sn3": (1.0, 0.5),
    "HO": (1.0, 0.5),
    "HOn3": (0.75, 0.375),
    "TT": (0.75, 0.375),
    "N": (0.5, 0.15),
}
WEIGHT_UNIT_TO_OZ = {
    "oz": 1.0,
    "lb": 16.0,
    "g": 0.03527396195,
    "kg": 35.27396195,
}
WEIGHT_UNIT_TO_KG = {
    "kg": 1.0,
    "g": 0.001,
    "lb": 0.45359237,
    "oz": 0.028349523125,
}
LENGTH_UNIT_TO_IN = {
    "in": 1.0,
    "ft": 12.0,
    "mm": 0.03937007874,
    "cm": 0.3937007874,
    "m": 39.37007874,
}
LENGTH_UNIT_TO_M = {
    "m": 1.0,
    "cm": 0.01,
    "mm": 0.001,
    "ft": 0.3048,
    "in": 0.0254,
}
DEFAULT_LENGTH_UNIT = "mm"
DEFAULT_WEIGHT_UNIT = "g"
WEIGHT_UNITS = ["g", "kg", "lb", "oz"]
LENGTH_UNITS = ["mm", "cm", "m", "in", "ft"]


def ensure_db_backup() -> None:
    ensure_periodic_backup(db.store.db)


def get_app_settings() -> AppSettings:
    settings = AppSettings.query.get(1)
    if settings:
        return settings
    settings = AppSettings(
        id=1,
        page_size=DEFAULT_PAGE_SIZE,
        default_length_unit=DEFAULT_LENGTH_UNIT,
        default_weight_unit=DEFAULT_WEIGHT_UNIT,
    )
    db.session.add(settings)
    db.session.commit()
    ensure_db_backup()
    return settings


def get_page_size() -> str:
    settings = get_app_settings()
    size = settings.page_size or DEFAULT_PAGE_SIZE
    if size not in PAGINATION_OPTIONS:
        return DEFAULT_PAGE_SIZE
    return size


def get_default_length_unit() -> str:
    settings = get_app_settings()
    unit = (settings.default_length_unit or DEFAULT_LENGTH_UNIT).lower()
    if unit not in LENGTH_UNIT_TO_IN:
        return DEFAULT_LENGTH_UNIT
    return unit


def get_default_weight_unit() -> str:
    settings = get_app_settings()
    unit = (settings.default_weight_unit or DEFAULT_WEIGHT_UNIT).lower()
    if unit not in WEIGHT_UNIT_TO_OZ:
        return DEFAULT_WEIGHT_UNIT
    return unit


def get_page_number() -> int:
    page_value = request.args.get("page", "").strip()
    if page_value.isdigit():
        return max(1, int(page_value))
    return 1


def normalize_page_size(value: str) -> str:
    if not value:
        return ""
    if value in PAGINATION_OPTIONS:
        return value
    return ""


def parse_scale_line(line: str) -> tuple[str | None, str | None]:
    raw = line.strip()
    if "|" in raw:
        name, ratio = [part.strip() for part in raw.split("|", 1)]
        return name or None, ratio or None
    if "=" in raw:
        name, ratio = [part.strip() for part in raw.split("=", 1)]
        return name or None, ratio or None
    if raw.endswith(")") and "(" in raw:
        prefix, suffix = raw.rsplit("(", 1)
        ratio = suffix[:-1].strip()
        name = prefix.strip()
        if ratio.startswith("1:") and name:
            return name, ratio
    return None, raw or None


def parse_gauge_line(line: str) -> tuple[str | None, str | None]:
    raw = line.strip()
    if "|" in raw:
        value, scales = [part.strip() for part in raw.split("|", 1)]
        return value or None, scales or None
    if "=" in raw:
        value, scales = [part.strip() for part in raw.split("=", 1)]
        return value or None, scales or None
    if raw.endswith(")") and "(" in raw:
        prefix, suffix = raw.rsplit("(", 1)
        value = prefix.strip()
        scales = suffix[:-1].strip()
        return value or None, scales or None
    return raw or None, None


def build_scale_options(text: str | None) -> list[dict[str, str | None]]:
    if not text:
        return []
    options = []
    seen_values: set[str] = set()
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        name, ratio = parse_scale_line(cleaned)
        if name and ratio:
            value = ratio
            label = f"{name} ({ratio})"
            raw = f"{name}|{ratio}"
        else:
            value = cleaned
            label = cleaned
            raw = cleaned
            name = None
        if value in seen_values:
            continue
        seen_values.add(value)
        options.append({"name": name, "value": value, "label": label, "raw": raw})
    return options


def build_gauge_options(text: str | None) -> list[dict[str, str | None]]:
    if not text:
        return []
    options = []
    seen_values: set[str] = set()
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        value, scales = parse_gauge_line(cleaned)
        if not value:
            continue
        label = f"{value} ({scales})" if scales else value
        raw = f"{value}|{scales}" if scales else value
        if value in seen_values:
            continue
        seen_values.add(value)
        options.append({"value": value, "label": label, "scales": scales, "raw": raw})
    return options


def get_scale_options_text() -> str:
    settings = get_app_settings()
    if settings.scale_options is None:
        return "\n".join(DEFAULT_SCALE_OPTIONS)
    return settings.scale_options or ""


def get_gauge_options_text() -> str:
    settings = get_app_settings()
    if settings.gauge_options is None:
        return "\n".join(DEFAULT_GAUGE_OPTIONS)
    return settings.gauge_options or ""


def get_scale_options() -> list[dict[str, str | None]]:
    return build_scale_options(get_scale_options_text())


def get_gauge_options() -> list[dict[str, str | None]]:
    return build_gauge_options(get_gauge_options_text())


def normalize_scale_input(value: str | None) -> str:
    if not value:
        return ""
    cleaned = value.strip()
    for option in get_scale_options():
        if cleaned == option["value"] or (option["name"] and cleaned == option["name"]):
            return option["value"] or cleaned
    return cleaned


def normalize_gauge_input(value: str | None) -> str:
    if not value:
        return ""
    cleaned = value.strip()
    for option in get_gauge_options():
        if cleaned == option["value"] or cleaned == option["label"]:
            return option["value"] or cleaned
    if cleaned.endswith(")") and "(" in cleaned:
        cleaned = cleaned.rsplit("(", 1)[0].strip()
    return cleaned


def format_scale_label(value: str | None) -> str | None:
    if not value:
        return None
    for option in get_scale_options():
        if option["value"] == value and option.get("name"):
            return option["label"]
    return value


def format_gauge_label(value: str | None) -> str | None:
    if not value:
        return None
    for option in get_gauge_options():
        if option["value"] == value:
            return option["label"]
    return value


def parse_actual_weight(value: str | None) -> tuple[str, str]:
    if not value:
        return "", ""
    cleaned = value.strip()
    match = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]+)$", cleaned)
    if match:
        amount = match.group(1)
        unit = match.group(2).lower()
        if unit in {"g", "kg", "lb", "oz"}:
            return amount, unit
    parts = cleaned.split()
    if len(parts) >= 2:
        amount = parts[0]
        unit = parts[1].lower()
        if unit in {"g", "kg", "lb", "oz"}:
            return amount, unit
    return cleaned, ""


def parse_actual_length(value: str | None) -> tuple[str, str]:
    if not value:
        return "", ""
    cleaned = value.strip()
    match = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]+)$", cleaned)
    if match:
        amount = match.group(1)
        unit = match.group(2).lower()
        if unit in {"in", "ft", "mm", "cm", "m"}:
            return amount, unit
    parts = cleaned.split()
    if len(parts) >= 2:
        amount = parts[0]
        unit = parts[1].lower()
        if unit in {"in", "ft", "mm", "cm", "m"}:
            return amount, unit
    return cleaned, ""


def get_scale_name(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    if cleaned in NMRA_WEIGHT_BY_SCALE:
        return cleaned
    for option in get_scale_options():
        if option["value"] == cleaned and option.get("name"):
            return option["name"]
    return None


def weight_to_ounces(amount: str, unit: str) -> float | None:
    try:
        numeric = float(amount)
    except ValueError:
        return None
    factor = WEIGHT_UNIT_TO_OZ.get(unit)
    if factor is None:
        return None
    return numeric * factor


def weight_to_kg(amount: str, unit: str) -> float | None:
    try:
        numeric = float(amount)
    except ValueError:
        return None
    factor = WEIGHT_UNIT_TO_KG.get(unit)
    if factor is None:
        return None
    return numeric * factor


def length_to_inches(amount: str, unit: str) -> float | None:
    try:
        numeric = float(amount)
    except ValueError:
        return None
    factor = LENGTH_UNIT_TO_IN.get(unit)
    if factor is None:
        return None
    return numeric * factor


def length_to_meters(amount: str, unit: str) -> float | None:
    try:
        numeric = float(amount)
    except ValueError:
        return None
    factor = LENGTH_UNIT_TO_M.get(unit)
    if factor is None:
        return None
    return numeric * factor


def format_ounces(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def format_linear_density(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def maybe_run_nmra_weight_check(
    car: Car,
    previous_weight: str | None,
    previous_length: str | None,
    previous_scale: str | None,
) -> bool:
    if not car.id or not car.actual_weight or not car.actual_length:
        return False
    if (
        previous_weight == car.actual_weight
        and previous_length == car.actual_length
        and previous_scale == car.scale
    ):
        return False
    scale_name = get_scale_name(car.scale)
    if not scale_name or scale_name not in NMRA_WEIGHT_BY_SCALE:
        return False
    weight_amount, weight_unit = parse_actual_weight(car.actual_weight)
    length_amount, length_unit = parse_actual_length(car.actual_length)
    if not weight_amount or not weight_unit or not length_amount or not length_unit:
        return False
    actual_oz = weight_to_ounces(weight_amount, weight_unit)
    length_in = length_to_inches(length_amount, length_unit)
    if actual_oz is None or length_in is None:
        return False
    initial, additional = NMRA_WEIGHT_BY_SCALE[scale_name]
    minimum_oz = initial + (additional * length_in)
    passed = actual_oz >= minimum_oz
    details = (
        f"Min {format_ounces(minimum_oz)} oz (scale {scale_name}, "
        f"length {format_ounces(length_in)} in). "
        f"Actual {format_ounces(actual_oz)} oz."
    )
    inspection_type = InspectionType.query.filter_by(name=NMRA_WEIGHT_CHECK_NAME).first()
    if not inspection_type:
        inspection_type = InspectionType(name=NMRA_WEIGHT_CHECK_NAME)
        db.session.add(inspection_type)
        db.session.commit()
        ensure_db_backup()
    today = datetime.now().date().isoformat()
    existing = (
        CarInspection.query.filter_by(car_id=car.id, inspection_type_id=inspection_type.id)
        .order_by("inspection_date", reverse=True)
        .first()
    )
    if existing and existing.inspection_date == today:
        if existing.inspection_type_id is None:
            existing.inspection_type_id = inspection_type.id
        existing.passed = passed
        existing.details = details
    else:
        db.session.add(
            CarInspection(
                car_id=car.id,
                inspection_type_id=inspection_type.id,
                inspection_date=today,
                details=details,
                passed=passed,
            )
        )
    car.last_inspection_date = today
    db.session.commit()
    ensure_db_backup()
    return True


def maybe_run_nmra_loaded_weight_check(
    car: Car,
    previous_weight: str | None,
    previous_length: str | None,
    previous_scale: str | None,
    force: bool = False,
) -> bool:
    if not car.id or not car.actual_weight or not car.actual_length:
        return False
    if not force and (
        previous_weight == car.actual_weight
        and previous_length == car.actual_length
        and previous_scale == car.scale
    ):
        return False
    scale_name = get_scale_name(car.scale)
    if not scale_name or scale_name not in NMRA_WEIGHT_BY_SCALE:
        return False
    weight_amount, weight_unit = parse_actual_weight(car.actual_weight)
    length_amount, length_unit = parse_actual_length(car.actual_length)
    if not weight_amount or not weight_unit or not length_amount or not length_unit:
        return False
    car_oz = weight_to_ounces(weight_amount, weight_unit)
    length_in = length_to_inches(length_amount, length_unit)
    if car_oz is None or length_in is None:
        return False
    load_oz_total = 0.0
    placements = LoadPlacement.query.filter_by(car_id=car.id).all()
    for placement in placements:
        if not placement.load or not placement.load.weight:
            continue
        load_amount, load_unit = parse_actual_weight(placement.load.weight)
        if not load_amount or not load_unit:
            continue
        load_oz = weight_to_ounces(load_amount, load_unit)
        if load_oz is None:
            continue
        load_oz_total += load_oz * max(placement.quantity, 1)
    initial, additional = NMRA_WEIGHT_BY_SCALE[scale_name]
    minimum_oz = initial + (additional * length_in)
    loaded_oz = car_oz + load_oz_total
    passed = loaded_oz >= minimum_oz
    details = (
        f"Min {format_ounces(minimum_oz)} oz (scale {scale_name}, "
        f"length {format_ounces(length_in)} in). "
        f"Loaded {format_ounces(loaded_oz)} oz "
        f"(car {format_ounces(car_oz)} oz + loads {format_ounces(load_oz_total)} oz)."
    )
    inspection_type = InspectionType.query.filter_by(name=NMRA_WEIGHT_CHECK_LOADED_NAME).first()
    if not inspection_type:
        inspection_type = InspectionType(name=NMRA_WEIGHT_CHECK_LOADED_NAME)
        db.session.add(inspection_type)
        db.session.commit()
        ensure_db_backup()
    today = datetime.now().date().isoformat()
    existing = (
        CarInspection.query.filter_by(car_id=car.id, inspection_type_id=inspection_type.id)
        .order_by("inspection_date", reverse=True)
        .first()
    )
    if existing and existing.inspection_date == today:
        if existing.inspection_type_id is None:
            existing.inspection_type_id = inspection_type.id
        existing.passed = passed
        existing.details = details
    else:
        db.session.add(
            CarInspection(
                car_id=car.id,
                inspection_type_id=inspection_type.id,
                inspection_date=today,
                details=details,
                passed=passed,
            )
        )
    car.last_inspection_date = today
    db.session.commit()
    ensure_db_backup()
    return True


def calculate_linear_density(car: Car) -> str | None:
    if not car.actual_weight or not car.actual_length:
        return None
    weight_amount, weight_unit = parse_actual_weight(car.actual_weight)
    length_amount, length_unit = parse_actual_length(car.actual_length)
    if not weight_amount or not weight_unit or not length_amount or not length_unit:
        return None
    weight_kg = weight_to_kg(weight_amount, weight_unit)
    length_m = length_to_meters(length_amount, length_unit)
    if weight_kg is None or length_m is None or length_m <= 0 or weight_kg <= 0:
        return None
    density = weight_kg / length_m
    return f"{format_linear_density(density)} kg/m"


def paginate_list(items: list, page: int, page_size: str, route: str, route_params: dict) -> tuple[list, dict]:
    total = len(items)
    if page_size == "all":
        start = 1 if total else 0
        return (
            items,
            {
                "page": 1,
                "pages": 1,
                "total": total,
                "start": start,
                "end": total,
                "page_size": page_size,
                "prev_url": None,
                "next_url": None,
            },
        )
    per_page = int(page_size)
    pages = max(1, math.ceil(total / per_page))
    page = min(max(1, page), pages)
    start_index = (page - 1) * per_page
    end_index = min(start_index + per_page, total)
    prev_url = url_for(route, **route_params, page=page - 1) if page > 1 else None
    next_url = url_for(route, **route_params, page=page + 1) if page < pages else None
    return (
        items[start_index:end_index],
        {
            "page": page,
            "pages": pages,
            "total": total,
            "start": start_index + 1 if total else 0,
            "end": end_index,
            "page_size": page_size,
            "prev_url": prev_url,
            "next_url": next_url,
        },
    )


def paginate_query(query, page: int, page_size: str, route: str, route_params: dict) -> tuple[list, dict]:
    if page_size == "all":
        items = query.all()
        total = len(items)
        start = 1 if total else 0
        return (
            items,
            {
                "page": 1,
                "pages": 1,
                "total": total,
                "start": start,
                "end": total,
                "page_size": page_size,
                "prev_url": None,
                "next_url": None,
            },
        )
    per_page = int(page_size)
    total = query.total()
    pages = max(1, math.ceil(total / per_page)) if total else 1
    page = min(max(1, page), pages)
    items = query.page(page, per_page)
    start_index = (page - 1) * per_page
    end_index = min(start_index + per_page, total)
    prev_url = url_for(route, **route_params, page=page - 1) if page > 1 else None
    next_url = url_for(route, **route_params, page=page + 1) if page < pages else None
    return (
        items,
        {
            "page": page,
            "pages": pages,
            "total": total,
            "start": start_index + 1 if total else 0,
            "end": end_index,
            "page_size": page_size,
            "prev_url": prev_url,
            "next_url": next_url,
        },
    )


def prefetch_car_relations(cars: list[Car]) -> None:
    railroad_ids = {car.railroad_id for car in cars if car.railroad_id}
    class_ids = {car.car_class_id for car in cars if car.car_class_id}
    location_ids = {car.location_id for car in cars if car.location_id}
    railroads = {rid: Railroad.query.get(rid) for rid in railroad_ids}
    classes = {cid: CarClass.query.get(cid) for cid in class_ids}
    locations = {lid: Location.query.get(lid) for lid in location_ids}
    logo_ids = {
        railroad.representative_logo_id
        for railroad in railroads.values()
        if railroad and railroad.representative_logo_id
    }
    logos = {lid: RailroadLogo.query.get(lid) for lid in logo_ids}
    for car in cars:
        if car.railroad_id:
            railroad = railroads.get(car.railroad_id)
            car._railroad_ref = railroad
            if railroad and railroad.representative_logo_id:
                railroad._representative_logo_ref = logos.get(railroad.representative_logo_id)
        if car.car_class_id:
            car._car_class_ref = classes.get(car.car_class_id)
        if car.location_id:
            car._location_ref = locations.get(car.location_id)

def get_or_create_location(name: str) -> Optional[Location]:
    if not name:
        return None
    loc = Location.query.filter_by(name=name).first()
    if loc:
        return loc
    location_type = "bag"
    if "-F" in name:
        location_type = "flat"
    elif "staging" in name.lower() or " st" in name.lower():
        location_type = "staging_track"
    elif "yard" in name.lower() or " yd" in name.lower():
        location_type = "yard_track"
    elif "Carrier" in name or "carrier" in name:
        location_type = "carrier"
    loc = Location(name=name, location_type=location_type)
    db.session.add(loc)
    return loc


def normalize_color_list(value: str) -> str:
    if not value:
        return ""
    return ",".join([part.strip() for part in value.split(",") if part.strip()])


def allowed_logo_extension(filename: str) -> bool:
    _, ext = os.path.splitext(filename.lower())
    return ext in {".png", ".jpg", ".jpeg", ".svg"}


def save_logo_file(file_storage, railroad_id: int) -> str | None:
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_logo_extension(file_storage.filename):
        return None
    filename = secure_filename(file_storage.filename)
    _, ext = os.path.splitext(filename)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_name = f"railroad-{railroad_id}-logo-{timestamp}{ext.lower()}"
    upload_dir = current_app.config.get("LOGO_UPLOAD_FOLDER")
    if not upload_dir:
        return None
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, safe_name)
    file_storage.save(file_path)
    return f"uploads/railroad-logos/{safe_name}"


@main_bp.route("/")
def index():
    return redirect(url_for("main.inventory"))


@main_bp.route("/inventory")
def inventory():
    car_query = Car.query.order_by("id", reverse=True)
    page_size = get_page_size()
    page = get_page_number()
    paged_cars, pagination = paginate_query(car_query, page, page_size, "main.inventory", {})
    prefetch_car_relations(paged_cars)
    return render_template("inventory.html", cars=paged_cars, pagination=pagination)


@main_bp.route("/inventory2")
def inventory2():
    return render_template("inventory2.html")


@main_bp.route("/reports")
def reports():
    return render_template("reports.html")


def inspection_type_tree(types: list[InspectionType], excluded_id: int | None = None) -> list[dict]:
    by_parent: dict[int | None, list[InspectionType]] = {}
    for inspection_type in types:
        if excluded_id and inspection_type.id == excluded_id:
            continue
        by_parent.setdefault(inspection_type.parent_id, []).append(inspection_type)
    for group in by_parent.values():
        group.sort(key=lambda item: (item.name or "").lower())

    results: list[dict] = []

    def walk(parent_id: int | None, depth: int) -> None:
        for inspection_type in by_parent.get(parent_id, []):
            label = f"{'-- ' * depth}{inspection_type.name or ''}"
            results.append({"type": inspection_type, "label": label, "depth": depth})
            walk(inspection_type.id, depth + 1)

    walk(None, 0)
    return results


@main_bp.route("/reports/inspections")
def inspections_report():
    inspection_types = InspectionType.query.all()
    type_rows = inspection_type_tree(inspection_types)
    selected_type_id = request.args.get("inspection_type_id", "").strip()
    selected_result = request.args.get("result", "").strip()
    inspections: list[CarInspection] = []
    if selected_type_id.isdigit() and selected_result in {"passed", "failed"}:
        type_id = int(selected_type_id)
        all_inspections = CarInspection.query.filter_by(inspection_type_id=type_id).all()
        all_inspections.sort(
            key=lambda inspection: (inspection.inspection_date is None, inspection.inspection_date or ""),
            reverse=True,
        )
        latest_by_car: dict[int, CarInspection] = {}
        for inspection in all_inspections:
            if inspection.car_id is None:
                continue
            if inspection.car_id in latest_by_car:
                continue
            latest_by_car[inspection.car_id] = inspection
        passed_flag = selected_result == "passed"
        inspections = [
            inspection
            for inspection in latest_by_car.values()
            if inspection.passed is not None and inspection.passed == passed_flag
        ]
        inspections.sort(
            key=lambda inspection: (inspection.inspection_date is None, inspection.inspection_date or ""),
            reverse=True,
        )
    return render_template(
        "inspection_report.html",
        inspection_types=type_rows,
        selected_type_id=selected_type_id,
        selected_result=selected_result,
        inspections=inspections,
    )


@main_bp.route("/inventory/export")
def inventory_export():
    cars = Car.query.order_by("id").all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Reporting Mark",
            "Railroad",
            "Car Class",
            "Car Type",
            "Wheel Arrangement",
            "Tender Axles",
            "Capacity (Lettering)",
            "Weight (Lettering)",
            "Load Limit",
            "AAR Plate",
            "Location",
            "Brand",
            "UPC",
            "Car #",
            "DCC ID",
            "Notes",
            "Traction Drivers",
            "Built (Lettering)",
            "Alt Date",
            "Reweight date",
            "Repack Bearings Date",
            "Last Inspection Date",
            "Other Lettering",
            "MSRP",
            "Price",
            "Load",
            "Repairs Req’d",
        ]
    )
    for car in cars:
        class_type = car.car_class.car_type if car.car_class else ""
        class_wheel = car.car_class.wheel_arrangement if car.car_class else ""
        class_tender = car.car_class.tender_axles if car.car_class else ""
        class_capacity = car.car_class.capacity if car.car_class else ""
        class_weight = car.car_class.weight if car.car_class else ""
        class_load_limit = car.car_class.load_limit if car.car_class else ""
        class_aar_plate = car.car_class.aar_plate if car.car_class else ""
        writer.writerow(
            [
                car.railroad.reporting_mark if car.railroad else (car.reporting_mark_override or ""),
                car.railroad.name if car.railroad else "",
                car.car_class.code if car.car_class else "",
                car.car_type_override or class_type or "",
                car.wheel_arrangement_override or class_wheel or "",
                car.tender_axles_override or class_tender or "",
                car.capacity_override or class_capacity or "",
                car.weight_override or class_weight or "",
                car.load_limit_override or class_load_limit or "",
                car.aar_plate_override or class_aar_plate or "",
                car.location.name if car.location else "",
                car.brand or "",
                car.upc or "",
                car.car_number or "",
                car.dcc_id or "",
                car.notes or "",
                "Yes" if car.traction_drivers else "",
                car.built or "",
                car.alt_date or "",
                car.reweight_date or "",
                car.repack_bearings_date or "",
                car.last_inspection_date or "",
                car.other_lettering or "",
                car.msrp or "",
                car.price or "",
                car.load or "",
                car.repairs_required or "",
            ]
        )
    response = Response(output.getvalue(), mimetype="text/csv")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    response.headers["Content-Disposition"] = f"attachment; filename=inventory-export-{timestamp}.csv"
    return response


@main_bp.route("/reports/locomotive-dcc-export")
def locomotive_dcc_export():
    cars = Car.query.order_by("id").all()
    locomotive_cars = []
    for car in cars:
        class_is_locomotive = car.car_class.is_locomotive if car.car_class else None
        is_locomotive = (
            car.is_locomotive_override if car.is_locomotive_override is not None else class_is_locomotive
        )
        if is_locomotive and car.dcc_id:
            locomotive_cars.append(car)
    locomotive_cars.sort(
        key=lambda car: (
            car.dcc_id or "",
            car.railroad.reporting_mark if car.railroad else (car.reporting_mark_override or ""),
            car.car_number or "",
        )
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "DCC ID",
            "Reporting Mark",
            "Car #",
            "Car Class",
            "Class Era",
        ]
    )
    for car in locomotive_cars:
        writer.writerow(
            [
                car.dcc_id or "",
                car.railroad.reporting_mark if car.railroad else (car.reporting_mark_override or ""),
                car.car_number or "",
                car.car_class.code if car.car_class else "",
                car.car_class.era if car.car_class and car.car_class.era else "",
            ]
        )
    response = Response(output.getvalue(), mimetype="text/csv")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    response.headers["Content-Disposition"] = (
        f"attachment; filename=locomotive-dcc-export-{timestamp}.csv"
    )
    return response


@main_bp.route("/reports/era-histogram")
def era_histogram():
    mode = request.args.get("mode", "car").strip().lower()
    if mode not in {"car", "class"}:
        mode = "car"

    current_year = datetime.now().year
    cars = Car.query.order_by("id").all()
    decade_counts: dict[int, int] = {}
    class_seen: dict[int, set[int]] = {}
    for car in cars:
        if not car.car_class or not car.car_class.era:
            continue
        era_text = car.car_class.era.lower()
        years = [int(year) for year in re.findall(r"(\d{4})", era_text)]
        if not years and "present" in era_text:
            years = [current_year]
        if not years:
            continue
        start_year = min(years)
        end_year = max(years)
        if "present" in era_text or "current" in era_text or "today" in era_text:
            end_year = current_year
        if end_year < start_year:
            end_year = start_year
        start_decade = (start_year // 10) * 10
        end_decade = (end_year // 10) * 10
        if mode == "class":
            if car.car_class_id is None:
                continue
            for decade in range(start_decade, end_decade + 1, 10):
                class_seen.setdefault(decade, set()).add(car.car_class_id)
        else:
            for decade in range(start_decade, end_decade + 1, 10):
                decade_counts[decade] = decade_counts.get(decade, 0) + 1

    if mode == "class":
        decade_counts = {decade: len(class_ids) for decade, class_ids in class_seen.items()}

    if decade_counts:
        min_decade = min(decade_counts.keys())
        max_decade = max(max(decade_counts.keys()), (current_year // 10) * 10)
        for decade in range(min_decade, max_decade + 1, 10):
            decade_counts.setdefault(decade, 0)

    sorted_counts = sorted(decade_counts.items(), key=lambda item: item[0])
    max_count = max((count for _, count in sorted_counts), default=0)
    return render_template(
        "era_histogram.html",
        era_counts=sorted_counts,
        max_count=max_count,
        total=sum(decade_counts.values()),
        mode=mode,
    )


@main_bp.route("/reports/introduction-years")
def introduction_years():
    current_year = datetime.now().year
    classes = CarClass.query.order_by("code").all()
    cars = Car.query.order_by("id").all()

    class_by_year: dict[int, list[CarClass]] = {}
    for car_class in classes:
        if not car_class.era:
            continue
        match = re.search(r"(\d{4})", car_class.era)
        if not match:
            continue
        year = int(match.group(1))
        class_by_year.setdefault(year, []).append(car_class)

    built_by_year: dict[int, list[Car]] = {}
    for car in cars:
        if not car.built:
            continue
        match = re.search(r"(\d{4})", car.built)
        if not match:
            continue
        year = int(match.group(1))
        built_by_year.setdefault(year, []).append(car)

    all_years = set(class_by_year.keys()) | set(built_by_year.keys())
    if not all_years:
        return render_template("introduction_years.html", year_entries=[], current_year=current_year)

    start_year = min(all_years)
    year_entries = []
    for year in range(start_year, current_year + 1):
        class_entries = class_by_year.get(year, [])
        built_entries = built_by_year.get(year, [])
        if not class_entries and not built_entries:
            continue
        class_entries = sorted(class_entries, key=lambda item: item.code)
        built_entries = sorted(built_entries, key=lambda item: item.id)
        year_entries.append(
            {
                "year": year,
                "classes": class_entries,
                "cars": built_entries,
            }
        )

    return render_template(
        "introduction_years.html",
        year_entries=year_entries,
        current_year=current_year,
    )


@main_bp.route("/reports/repairs")
def repairs_report():
    cars = Car.query.order_by("id").all()
    repairs = []
    for car in cars:
        if car.repairs_required and car.repairs_required.strip():
            repairs.append(car)
    return render_template("repairs_report.html", cars=repairs, total=len(repairs))


@main_bp.route("/reports/conflicts")
def conflict_report():
    cars = Car.query.order_by("id").all()
    car_key_map = {}
    dcc_map = {}
    for car in cars:
        reporting_mark = (
            car.railroad.reporting_mark if car.railroad else (car.reporting_mark_override or "")
        )
        car_number = car.car_number or ""
        if car_number:
            key = f"{reporting_mark} {car_number}".strip()
            car_key_map.setdefault(key, []).append(car)
        if car.dcc_id:
            dcc_map.setdefault(car.dcc_id, []).append(car)

    car_conflicts = {key: items for key, items in car_key_map.items() if len(items) > 1}
    dcc_conflicts = {key: items for key, items in dcc_map.items() if len(items) > 1}

    return render_template(
        "conflicts_report.html",
        car_conflicts=car_conflicts,
        dcc_conflicts=dcc_conflicts,
    )


@main_bp.route("/railroads")
def railroads():
    railroads = Railroad.query.order_by("reporting_mark").all()
    page_size = get_page_size()
    page = get_page_number()
    paged_railroads, pagination = paginate_list(railroads, page, page_size, "main.railroads", {})
    return render_template("railroads.html", railroads=paged_railroads, pagination=pagination)


@main_bp.route("/locations")
def locations():
    locations = Location.query.order_by("name").all()
    page_size = get_page_size()
    page = get_page_number()
    paged_locations, pagination = paginate_list(locations, page, page_size, "main.locations", {})
    return render_template("locations.html", locations=paged_locations, pagination=pagination)


@main_bp.route("/locations/new", methods=["GET", "POST"])
def location_new():
    if request.method == "POST":
        location = Location(name=request.form.get("name", "").strip())
        location.location_type = request.form.get("location_type", "").strip()
        parent_id = request.form.get("parent_id", "").strip()
        if parent_id and parent_id.isdigit():
            location.parent = Location.query.get(int(parent_id))
        db.session.add(location)
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.location_detail", location_id=location.id))
    locations = Location.query.order_by("name").all()
    location_types = current_app.config.get("LOCATION_TYPES", [])
    return render_template(
        "location_form.html",
        location=None,
        locations=locations,
        descendant_ids=set(),
        location_types=location_types,
    )


@main_bp.route("/railroads/<int:railroad_id>")
def railroad_detail(railroad_id: int):
    railroad = Railroad.query.get_or_404(railroad_id)
    cars = Car.query.filter_by(railroad_id=railroad.id).order_by("id", reverse=True).all()
    page_size = get_page_size()
    page = get_page_number()
    paged_cars, cars_pagination = paginate_list(
        cars,
        page,
        page_size,
        "main.railroad_detail",
        {"railroad_id": railroad.id},
    )
    return render_template(
        "railroad_detail.html",
        railroad=railroad,
        cars=paged_cars,
        cars_pagination=cars_pagination,
    )


@main_bp.route("/railroads/<int:railroad_id>/delete", methods=["POST"])
def railroad_delete(railroad_id: int):
    railroad = Railroad.query.get_or_404(railroad_id)
    if Car.query.filter_by(railroad_id=railroad.id).count() > 0:
        return "Cannot delete railroad with cars assigned.", 400
    db.session.delete(railroad)
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.railroads"))


@main_bp.route("/railroads/<int:railroad_id>/edit", methods=["GET", "POST"])
def railroad_edit(railroad_id: int):
    railroad = Railroad.query.get_or_404(railroad_id)
    if request.method == "POST":
        railroad.reporting_mark = request.form.get("reporting_mark", "").strip()
        railroad.name = request.form.get("name", "").strip()
        railroad.start_date = request.form.get("start_date", "").strip()
        railroad.end_date = request.form.get("end_date", "").strip()
        railroad.merged_into = request.form.get("merged_into", "").strip()
        railroad.merged_from = request.form.get("merged_from", "").strip()
        railroad.notes = request.form.get("notes", "").strip()
        scheme_ids = request.form.getlist("color_scheme_id")
        scheme_descriptions = request.form.getlist("color_scheme_description")
        scheme_starts = request.form.getlist("color_scheme_start")
        scheme_ends = request.form.getlist("color_scheme_end")
        scheme_colors = request.form.getlist("color_scheme_colors")
        existing_schemes = {str(scheme.id): scheme for scheme in railroad.color_schemes}
        kept_scheme_ids = set()
        new_schemes = []
        for index, description in enumerate(scheme_descriptions):
            description = description.strip()
            scheme_id = scheme_ids[index] if index < len(scheme_ids) else ""
            if not description:
                continue
            start_date = scheme_starts[index].strip() if index < len(scheme_starts) else ""
            end_date = scheme_ends[index].strip() if index < len(scheme_ends) else ""
            colors = normalize_color_list(scheme_colors[index]) if index < len(scheme_colors) else ""
            if scheme_id and scheme_id in existing_schemes:
                scheme = existing_schemes[scheme_id]
                scheme.description = description
                scheme.start_date = start_date or None
                scheme.end_date = end_date or None
                scheme.colors = colors or None
                kept_scheme_ids.add(scheme_id)
            else:
                new_schemes.append(
                    RailroadColorScheme(
                        description=description,
                        start_date=start_date or None,
                        end_date=end_date or None,
                        colors=colors or None,
                    )
                )
        for scheme_id, scheme in existing_schemes.items():
            if scheme_id not in kept_scheme_ids:
                db.session.delete(scheme)
        for scheme in new_schemes:
            scheme.railroad_id = railroad.id
            db.session.add(scheme)

        logo_ids = request.form.getlist("logo_id")
        logo_descriptions = request.form.getlist("logo_description")
        logo_starts = request.form.getlist("logo_start")
        logo_ends = request.form.getlist("logo_end")
        logo_existing_paths = request.form.getlist("logo_existing_path")
        representative_index = request.form.get("representative_logo_index", "").strip()
        existing_logos = {str(logo.id): logo for logo in railroad.logos}
        kept_logo_ids = set()
        row_logos: list[RailroadLogo | None] = []
        for index, description in enumerate(logo_descriptions):
            logo_id = logo_ids[index] if index < len(logo_ids) else ""
            start_date = logo_starts[index].strip() if index < len(logo_starts) else ""
            end_date = logo_ends[index].strip() if index < len(logo_ends) else ""
            existing_path = logo_existing_paths[index].strip() if index < len(logo_existing_paths) else ""
            file_storage = request.files.get(f"logo_image_{index}")
            description = description.strip()
            row_has_data = bool(
                description
                or start_date
                or end_date
                or existing_path
                or (file_storage and file_storage.filename)
            )
            if not row_has_data:
                row_logos.append(None)
                continue
            new_path = save_logo_file(file_storage, railroad.id)
            logo = existing_logos.get(logo_id) if logo_id else None
            if not logo:
                logo = RailroadLogo(railroad_id=railroad.id)
                db.session.add(logo)
            logo.description = description
            logo.start_date = start_date or None
            logo.end_date = end_date or None
            if new_path:
                if logo.image_path and logo.image_path.startswith("uploads/railroad-logos/"):
                    old_path = os.path.join(current_app.static_folder or "", logo.image_path)
                    if os.path.exists(old_path):
                        os.remove(old_path)
                logo.image_path = new_path
            else:
                logo.image_path = existing_path or None
            if logo_id:
                kept_logo_ids.add(logo_id)
            row_logos.append(logo)
        for logo_id, logo in existing_logos.items():
            if logo_id not in kept_logo_ids:
                db.session.delete(logo)

        slogan_ids = request.form.getlist("slogan_id")
        slogan_descriptions = request.form.getlist("slogan_description")
        slogan_texts = request.form.getlist("slogan_text")
        slogan_starts = request.form.getlist("slogan_start")
        slogan_ends = request.form.getlist("slogan_end")
        existing_slogans = {str(slogan.id): slogan for slogan in railroad.slogans}
        kept_slogan_ids = set()
        new_slogans = []
        for index, description in enumerate(slogan_descriptions):
            description = description.strip()
            slogan_id = slogan_ids[index] if index < len(slogan_ids) else ""
            slogan_text = slogan_texts[index].strip() if index < len(slogan_texts) else ""
            start_date = slogan_starts[index].strip() if index < len(slogan_starts) else ""
            end_date = slogan_ends[index].strip() if index < len(slogan_ends) else ""
            if not (description or slogan_text or start_date or end_date):
                continue
            if slogan_id and slogan_id in existing_slogans:
                slogan = existing_slogans[slogan_id]
                slogan.description = description
                slogan.slogan_text = slogan_text or None
                slogan.start_date = start_date or None
                slogan.end_date = end_date or None
                kept_slogan_ids.add(slogan_id)
            else:
                new_slogans.append(
                    RailroadSlogan(
                        description=description,
                        slogan_text=slogan_text or None,
                        start_date=start_date or None,
                        end_date=end_date or None,
                    )
                )
        for slogan_id, slogan in existing_slogans.items():
            if slogan_id not in kept_slogan_ids:
                db.session.delete(slogan)
        for slogan in new_slogans:
            slogan.railroad_id = railroad.id
            db.session.add(slogan)

        db.session.flush()
        if representative_index.isdigit():
            rep_index = int(representative_index)
            representative_logo = row_logos[rep_index] if rep_index < len(row_logos) else None
            railroad.representative_logo_id = representative_logo.id if representative_logo else None
        else:
            railroad.representative_logo_id = None
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.railroad_detail", railroad_id=railroad.id))
    return render_template("railroad_form.html", railroad=railroad)


@main_bp.route("/tools")
def tools():
    return render_template("tools.html")


@main_bp.route("/tools/aar-plate-viewer")
def aar_plate_viewer():
    return render_template("aar_plate_viewer.html")


def draw_wrapped_text(draw, text, x, y, max_width, font, line_height) -> None:
    if not text:
        return
    words = text.split()
    line = []
    for word in words:
        test = " ".join(line + [word]).strip()
        if not test:
            continue
        width = draw.textlength(test, font=font)
        if width <= max_width or not line:
            line.append(word)
        else:
            draw.text((x, y), " ".join(line), fill="#111111", font=font)
            y += line_height
            line = [word]
    if line:
        draw.text((x, y), " ".join(line), fill="#111111", font=font)


def draw_centered_text(draw, text, x, y, width, font) -> None:
    if not text:
        return
    text_width = draw.textlength(text, font=font)
    draw.text((x + max((width - text_width) / 2, 0), y), text, fill="#111111", font=font)


@main_bp.route("/tools/prr-home-shop-repair", methods=["GET", "POST"])
def prr_home_shop_repair():
    if request.method == "POST":
        via = request.form.get("via", "").strip()
        from_value = request.form.get("from_value", "").strip()
        date_value = request.form.get("date_value", "").strip()
        main_defects = request.form.get("main_defects", "").strip()
        car_initials = request.form.get("car_initials", "").strip()
        car_number = request.form.get("car_number", "").strip()
        inspector = request.form.get("inspector", "").strip()
        responsibility = request.form.get("responsibility", "").strip()

        template_path = os.path.join(current_app.root_path, "static", "tools", "PRR-home-shop-defect.png")
        image = Image.open(template_path).convert("RGB")
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", size=48)
        except OSError:
            font = ImageFont.load_default()

        draw_centered_text(draw, via, 360, 1040, 2500, font)
        draw_centered_text(draw, from_value, 460, 1225, 1600, font)
        draw_centered_text(draw, date_value, 2300, 1225, 600, font)
        draw_centered_text(draw, main_defects, 700, 1395, 2100, font)
        draw_centered_text(draw, car_initials, 520, 1575, 1000, font)
        draw_centered_text(draw, car_number, 1400, 1575, 600, font)
        draw_centered_text(draw, inspector, 2200, 1575, 700, font)
        draw_centered_text(draw, responsibility, 1250, 1795, 1200, font)

        output = io.BytesIO()
        image.save(output, format="PNG")
        output.seek(0)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"prr-home-shop-{timestamp}.png"
        generated_dir = os.path.join(current_app.root_path, "static", "tools", "generated")
        os.makedirs(generated_dir, exist_ok=True)
        output_path = os.path.join(generated_dir, filename)
        with open(output_path, "wb") as output_file:
            output_file.write(output.getvalue())

        return render_template(
            "prr_home_shop_form.html",
            image_filename=filename,
            form_data=request.form,
        )

    return render_template("prr_home_shop_form.html", image_filename=None, form_data={})


@main_bp.route("/tools/prr-home-shop-repair/render")
def prr_home_shop_render():
    filename = request.args.get("file", "").strip()
    if not filename:
        return redirect(url_for("main.prr_home_shop_repair"))
    image_path = os.path.join(current_app.root_path, "static", "tools", "generated", filename)
    if not os.path.exists(image_path):
        return redirect(url_for("main.prr_home_shop_repair"))
    return render_template("prr_home_shop_render.html", image_filename=filename)


@main_bp.route("/car-classes")
def car_classes():
    classes = CarClass.query.order_by("code").all()
    car_classes = [c for c in classes if not c.is_locomotive]
    locomotive_classes = [c for c in classes if c.is_locomotive]
    page_size = get_page_size()
    page = get_page_number()
    paged_classes, pagination = paginate_list(car_classes, page, page_size, "main.car_classes", {})
    return render_template("car_classes.html", car_classes=paged_classes, pagination=pagination)


@main_bp.route("/locomotive-classes")
def locomotive_classes():
    classes = CarClass.query.order_by("code").all()
    locomotive_classes = [c for c in classes if c.is_locomotive]
    page_size = get_page_size()
    page = get_page_number()
    paged_classes, pagination = paginate_list(
        locomotive_classes,
        page,
        page_size,
        "main.locomotive_classes",
        {},
    )
    return render_template("locomotive_classes.html", locomotive_classes=paged_classes, pagination=pagination)


@main_bp.route("/loads")
def loads():
    loads = LoadType.query.order_by("name").all()
    page_size = get_page_size()
    page = get_page_number()
    paged_loads, pagination = paginate_list(loads, page, page_size, "main.loads", {})
    return render_template("loads.html", loads=paged_loads, pagination=pagination)


@main_bp.route("/loads/new", methods=["GET", "POST"])
def load_new():
    classes = CarClass.query.order_by("code").all()
    railroads = Railroad.query.order_by("name").all()
    if request.method == "POST":
        load = LoadType(name=request.form.get("name", "").strip())
        apply_load_form(load, request.form)
        db.session.add(load)
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.load_detail", load_id=load.id))
    default_length_unit = get_default_length_unit()
    default_weight_unit = get_default_weight_unit()
    return render_template(
        "load_form.html",
        load=None,
        classes=classes,
        railroads=railroads,
        length_value="",
        length_unit=default_length_unit,
        width_value="",
        width_unit=default_length_unit,
        height_value="",
        height_unit=default_length_unit,
        weight_value="",
        weight_unit=default_weight_unit,
    )


@main_bp.route("/loads/<int:load_id>")
def load_detail(load_id: int):
    load = LoadType.query.get_or_404(load_id)
    placements = LoadPlacement.query.filter_by(load_id=load.id).all()
    page_size = get_page_size()
    page = get_page_number()
    paged_placements, placements_pagination = paginate_list(
        placements,
        page,
        page_size,
        "main.load_detail",
        {"load_id": load.id},
    )
    return render_template(
        "load_detail.html",
        load=load,
        placements=paged_placements,
        placements_pagination=placements_pagination,
    )


@main_bp.route("/loads/<int:load_id>/edit", methods=["GET", "POST"])
def load_edit(load_id: int):
    load = LoadType.query.get_or_404(load_id)
    classes = CarClass.query.order_by("code").all()
    railroads = Railroad.query.order_by("name").all()
    if request.method == "POST":
        load.name = request.form.get("name", "").strip()
        apply_load_form(load, request.form)
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.load_detail", load_id=load.id))
    length_value, length_unit = parse_actual_length(load.length)
    width_value, width_unit = parse_actual_length(load.width)
    height_value, height_unit = parse_actual_length(load.height)
    weight_value, weight_unit = parse_actual_weight(load.weight)
    if not length_unit:
        length_unit = get_default_length_unit()
    if not width_unit:
        width_unit = get_default_length_unit()
    if not height_unit:
        height_unit = get_default_length_unit()
    if not weight_unit:
        weight_unit = get_default_weight_unit()
    return render_template(
        "load_form.html",
        load=load,
        classes=classes,
        railroads=railroads,
        length_value=length_value,
        length_unit=length_unit,
        width_value=width_value,
        width_unit=width_unit,
        height_value=height_value,
        height_unit=height_unit,
        weight_value=weight_value,
        weight_unit=weight_unit,
    )


@main_bp.route("/loads/<int:load_id>/delete", methods=["POST"])
def load_delete(load_id: int):
    load = LoadType.query.get_or_404(load_id)
    db.session.delete(load)
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.loads"))


@main_bp.route("/loads/<int:load_id>/placements/new", methods=["GET", "POST"])
def load_placement_new(load_id: int):
    load = LoadType.query.get_or_404(load_id)
    cars = Car.query.order_by("id", reverse=True).all()
    locations = Location.query.order_by("name").all()
    if request.method == "POST":
        placement = LoadPlacement(load_id=load.id)
        if not apply_load_placement_form(placement, request.form):
            return "Select a car or location for this load placement.", 400
        db.session.add(placement)
        db.session.commit()
        ensure_db_backup()
        if placement.car_id:
            car = Car.query.get(placement.car_id)
            if car:
                maybe_run_nmra_loaded_weight_check(car, car.actual_weight, car.actual_length, car.scale, True)
        return redirect(url_for("main.load_detail", load_id=load.id))
    return render_template(
        "load_placement_form.html",
        load=load,
        placement=None,
        cars=cars,
        locations=locations,
        preset_car_id=request.args.get("car_id", "").strip(),
        preset_location_id=request.args.get("location_id", "").strip(),
    )


@main_bp.route("/load-placements/new", methods=["GET", "POST"])
def load_placement_new_generic():
    loads = LoadType.query.order_by("name").all()
    cars = Car.query.order_by("id", reverse=True).all()
    locations = Location.query.order_by("name").all()
    if request.method == "POST":
        load_id = request.form.get("load_id", "").strip()
        if not load_id.isdigit():
            return "Select a load type for this placement.", 400
        load = LoadType.query.get_or_404(int(load_id))
        placement = LoadPlacement(load_id=load.id)
        if not apply_load_placement_form(placement, request.form):
            return "Select a car or location for this load placement.", 400
        db.session.add(placement)
        db.session.commit()
        ensure_db_backup()
        if placement.car_id:
            car = Car.query.get(placement.car_id)
            if car:
                maybe_run_nmra_loaded_weight_check(car, car.actual_weight, car.actual_length, car.scale, True)
        return redirect(url_for("main.load_detail", load_id=load.id))
    return render_template(
        "load_placement_form.html",
        load=None,
        placement=None,
        loads=loads,
        cars=cars,
        locations=locations,
        preset_car_id=request.args.get("car_id", "").strip(),
        preset_location_id=request.args.get("location_id", "").strip(),
    )


@main_bp.route("/load-placements/<int:placement_id>/edit", methods=["GET", "POST"])
def load_placement_edit(placement_id: int):
    placement = LoadPlacement.query.get_or_404(placement_id)
    cars = Car.query.order_by("id", reverse=True).all()
    locations = Location.query.order_by("name").all()
    if request.method == "POST":
        previous_car_id = placement.car_id
        if not apply_load_placement_form(placement, request.form):
            return "Select a car or location for this load placement.", 400
        db.session.commit()
        ensure_db_backup()
        if previous_car_id and previous_car_id != placement.car_id:
            previous_car = Car.query.get(previous_car_id)
            if previous_car:
                maybe_run_nmra_loaded_weight_check(
                    previous_car,
                    previous_car.actual_weight,
                    previous_car.actual_length,
                    previous_car.scale,
                    True,
                )
        if placement.car_id:
            car = Car.query.get(placement.car_id)
            if car:
                maybe_run_nmra_loaded_weight_check(car, car.actual_weight, car.actual_length, car.scale, True)
        return redirect(url_for("main.load_detail", load_id=placement.load_id))
    return render_template(
        "load_placement_form.html",
        load=placement.load,
        placement=placement,
        cars=cars,
        locations=locations,
        preset_car_id=str(placement.car_id or ""),
        preset_location_id=str(placement.location_id or ""),
    )


@main_bp.route("/load-placements/<int:placement_id>/delete", methods=["POST"])
def load_placement_delete(placement_id: int):
    placement = LoadPlacement.query.get_or_404(placement_id)
    load_id = placement.load_id
    car_id = placement.car_id
    db.session.delete(placement)
    db.session.commit()
    ensure_db_backup()
    if car_id:
        car = Car.query.get(car_id)
        if car:
            maybe_run_nmra_loaded_weight_check(car, car.actual_weight, car.actual_length, car.scale, True)
    return redirect(url_for("main.load_detail", load_id=load_id))


@main_bp.route("/car-classes/<int:class_id>")
def car_class_detail(class_id: int):
    car_class = CarClass.query.get_or_404(class_id)
    cars = Car.query.filter_by(car_class_id=car_class.id).order_by("id", reverse=True).all()
    page_size = get_page_size()
    page = get_page_number()
    paged_cars, cars_pagination = paginate_list(
        cars,
        page,
        page_size,
        "main.car_class_detail",
        {"class_id": car_class.id},
    )
    return render_template(
        "car_class_detail.html",
        car_class=car_class,
        cars=paged_cars,
        cars_pagination=cars_pagination,
    )


@main_bp.route("/car-classes/<int:class_id>/delete", methods=["POST"])
def car_class_delete(class_id: int):
    car_class = CarClass.query.get_or_404(class_id)
    if Car.query.filter_by(car_class_id=car_class.id).count() > 0:
        return "Cannot delete class with cars assigned.", 400
    db.session.delete(car_class)
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.car_classes"))


@main_bp.route("/car-classes/<int:class_id>/edit", methods=["GET", "POST"])
def car_class_edit(class_id: int):
    car_class = CarClass.query.get_or_404(class_id)
    if request.method == "POST":
        car_class.code = request.form.get("code", "").strip()
        car_class.car_type = request.form.get("car_type", "").strip()
        car_class.era = request.form.get("era", "").strip()
        car_class.wheel_arrangement = request.form.get("wheel_arrangement", "").strip()
        car_class.tender_axles = request.form.get("tender_axles", "").strip()
        car_class.is_locomotive = request.form.get("is_locomotive") == "on"
        car_class.capacity = request.form.get("capacity", "").strip()
        car_class.weight = request.form.get("weight", "").strip()
        car_class.load_limit = request.form.get("load_limit", "").strip()
        car_class.aar_plate = request.form.get("aar_plate", "").strip()
        car_class.internal_length = request.form.get("internal_length", "").strip()
        car_class.internal_width = request.form.get("internal_width", "").strip()
        car_class.internal_height = request.form.get("internal_height", "").strip()
        car_class.notes = request.form.get("notes", "").strip()
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.car_class_detail", class_id=car_class.id))
    return render_template("car_class_form.html", car_class=car_class)


@main_bp.route("/locations/<int:location_id>")
def location_detail(location_id: int):
    location = Location.query.get_or_404(location_id)
    cars = Car.query.filter_by(location_id=location.id).order_by("id", reverse=True).all()
    page_size = get_page_size()
    page = get_page_number()
    paged_cars, cars_pagination = paginate_list(
        cars,
        page,
        page_size,
        "main.location_detail",
        {"location_id": location.id},
    )
    return render_template(
        "location_detail.html",
        location=location,
        cars=paged_cars,
        cars_pagination=cars_pagination,
    )


def get_location_descendant_ids(location: Location) -> set[int]:
    descendants = set()
    queue = list(location.children)
    while queue:
        current = queue.pop(0)
        if current.id in descendants:
            continue
        descendants.add(current.id)
        queue.extend(current.children)
    return descendants


@main_bp.route("/locations/<int:location_id>/edit", methods=["GET", "POST"])
def location_edit(location_id: int):
    location = Location.query.get_or_404(location_id)
    descendant_ids = get_location_descendant_ids(location)
    if request.method == "POST":
        location.name = request.form.get("name", "").strip()
        location.location_type = request.form.get("location_type", "").strip()
        parent_id = request.form.get("parent_id", "").strip()
        if parent_id and parent_id.isdigit():
            parent_id_value = int(parent_id)
            if parent_id_value == location.id or parent_id_value in descendant_ids:
                return "Invalid parent location selection.", 400
            location.parent = Location.query.get(parent_id_value)
        else:
            location.parent = None
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.location_detail", location_id=location.id))
    locations = Location.query.order_by("name").all()
    location_types = current_app.config.get("LOCATION_TYPES", [])
    return render_template(
        "location_form.html",
        location=location,
        locations=locations,
        descendant_ids=descendant_ids,
        location_types=location_types,
    )


@main_bp.route("/locations/<int:location_id>/delete", methods=["POST"])
def location_delete(location_id: int):
    location = Location.query.get_or_404(location_id)
    if Car.query.filter_by(location_id=location.id).count() > 0:
        return "Cannot delete location with cars assigned.", 400
    if Location.query.filter_by(parent_id=location.id).count() > 0:
        return "Cannot delete location with child locations assigned.", 400
    db.session.delete(location)
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.locations"))


@main_bp.route("/cars/<int:car_id>")
def car_detail(car_id: int):
    car = Car.query.get_or_404(car_id)
    compare_cars = []
    for other in Car.query.order_by("id").all():
        if other.id == car.id:
            continue
        reporting_mark = other.railroad.reporting_mark if other.railroad else other.reporting_mark_override
        compare_cars.append(
            {
                "id": other.id,
                "reporting_mark": reporting_mark,
                "car_number": other.car_number,
            }
        )
    return render_template(
        "car_detail.html",
        car=car,
        compare_cars=compare_cars,
        car_payload=serialize_car(car),
        scale_label=format_scale_label(car.scale),
        gauge_label=format_gauge_label(car.gauge),
        linear_density=calculate_linear_density(car),
    )


@main_bp.route("/cars/<int:car_id>/inspect", methods=["GET", "POST"])
def car_inspect(car_id: int):
    car = Car.query.get_or_404(car_id)
    inspection_types = InspectionType.query.all()
    type_rows = inspection_type_tree(inspection_types)
    if request.method == "POST":
        inspection_date = request.form.get("inspection_date", "").strip()
        inspection_details = request.form.get("inspection_details", "").strip()
        inspection_type_id = request.form.get("inspection_type_id", "").strip()
        inspection_passed = request.form.get("inspection_passed", "").strip()
        if not inspection_date:
            return "Inspection date is required.", 400
        if not inspection_type_id.isdigit():
            return "Inspection type is required.", 400
        if inspection_passed not in {"passed", "failed"}:
            return "Inspection result is required.", 400
        inspection = CarInspection(
            car_id=car.id,
            inspection_date=inspection_date,
            details=inspection_details or None,
            inspection_type_id=int(inspection_type_id),
            passed=inspection_passed == "passed",
        )
        db.session.add(inspection)
        car.last_inspection_date = inspection_date
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.car_detail", car_id=car.id))
    return render_template("car_inspection_form.html", car=car, inspection_types=type_rows)


@main_bp.route("/cars/<int:car_id>/delete", methods=["POST"])
def car_delete(car_id: int):
    car = Car.query.get_or_404(car_id)
    db.session.delete(car)
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.inventory"))


@main_bp.route("/cars/by-number")
def car_by_number():
    number = request.args.get("number", "").strip()
    if not number:
        return redirect(url_for("main.inventory"))
    cars = Car.query.filter_by(car_number=number).order_by("id", reverse=True).all()
    if len(cars) == 1:
        return redirect(url_for("main.car_detail", car_id=cars[0].id))
    return render_template("car_number_list.html", number=number, cars=cars)


@main_bp.route("/cars/<int:car_id>/edit", methods=["GET", "POST"])
def car_edit(car_id: int):
    car = Car.query.get_or_404(car_id)
    if request.method == "POST":
        previous_weight = car.actual_weight
        previous_length = car.actual_length
        previous_scale = car.scale
        apply_car_form(car, request.form)
        db.session.commit()
        ensure_db_backup()
        maybe_run_nmra_weight_check(car, previous_weight, previous_length, previous_scale)
        maybe_run_nmra_loaded_weight_check(car, previous_weight, previous_length, previous_scale)
        return redirect(url_for("main.car_detail", car_id=car.id))
    railroads = Railroad.query.order_by("reporting_mark").all()
    classes = CarClass.query.order_by("code").all()
    locations = Location.query.order_by("name").all()
    scale_value = normalize_scale_input(car.scale)
    gauge_value = normalize_gauge_input(car.gauge)
    actual_weight_value, actual_weight_unit = parse_actual_weight(car.actual_weight)
    actual_length_value, actual_length_unit = parse_actual_length(car.actual_length)
    if not actual_weight_unit:
        actual_weight_unit = get_default_weight_unit()
    if not actual_length_unit:
        actual_length_unit = get_default_length_unit()
    return render_template(
        "car_form.html",
        car=car,
        railroads=railroads,
        classes=classes,
        locations=locations,
        prefill={},
        scale_options=get_scale_options(),
        gauge_options=get_gauge_options(),
        scale_value=scale_value,
        gauge_value=gauge_value,
        actual_weight_value=actual_weight_value,
        actual_weight_unit=actual_weight_unit,
        actual_length_value=actual_length_value,
        actual_length_unit=actual_length_unit,
        form_action=url_for("main.car_edit", car_id=car.id),
    )


@main_bp.route("/cars/new", methods=["GET", "POST"])
def car_new():
    if request.method == "POST":
        car = Car()
        apply_car_form(car, request.form)
        db.session.add(car)
        db.session.commit()
        ensure_db_backup()
        maybe_run_nmra_weight_check(car, None, None, None)
        maybe_run_nmra_loaded_weight_check(car, None, None, None)
        return redirect(url_for("main.car_detail", car_id=car.id))
    prefill = {
        "reporting_mark": request.args.get("reporting_mark", "").strip(),
        "railroad_name": request.args.get("railroad_name", "").strip(),
        "car_class": request.args.get("car_class", "").strip(),
        "car_type": request.args.get("car_type", "").strip(),
        "aar_plate": request.args.get("aar_plate", "").strip(),
        "capacity": request.args.get("capacity", "").strip(),
        "weight": request.args.get("weight", "").strip(),
        "load_limit": request.args.get("load_limit", "").strip(),
        "actual_weight": request.args.get("actual_weight", "").strip(),
        "actual_length": request.args.get("actual_length", "").strip(),
        "scale": request.args.get("scale", "").strip(),
        "gauge": request.args.get("gauge", "").strip(),
        "built": request.args.get("built", "").strip(),
        "brand": request.args.get("brand", "").strip(),
        "price": request.args.get("price", "").strip(),
        "msrp": request.args.get("msrp", "").strip(),
    }
    railroads = Railroad.query.order_by("reporting_mark").all()
    classes = CarClass.query.order_by("code").all()
    locations = Location.query.order_by("name").all()
    scale_value = normalize_scale_input(prefill.get("scale", ""))
    gauge_value = normalize_gauge_input(prefill.get("gauge", ""))
    actual_weight_value, actual_weight_unit = parse_actual_weight(prefill.get("actual_weight", ""))
    actual_length_value, actual_length_unit = parse_actual_length(prefill.get("actual_length", ""))
    if not actual_weight_unit:
        actual_weight_unit = get_default_weight_unit()
    if not actual_length_unit:
        actual_length_unit = get_default_length_unit()
    return render_template(
        "car_form.html",
        car=None,
        railroads=railroads,
        classes=classes,
        locations=locations,
        prefill=prefill,
        scale_options=get_scale_options(),
        gauge_options=get_gauge_options(),
        scale_value=scale_value,
        gauge_value=gauge_value,
        actual_weight_value=actual_weight_value,
        actual_weight_unit=actual_weight_unit,
        actual_length_value=actual_length_value,
        actual_length_unit=actual_length_unit,
        form_action=url_for("main.car_new"),
    )


@main_bp.route("/search")
def search():
    query = request.args.get("q", "").strip()
    cars = search_cars(query)
    return render_template("search.html", cars=cars, query=query)


@main_bp.route("/api/cars")
def api_cars():
    page_value = request.args.get("page", "").strip()
    page_size_value = request.args.get("page_size", "").strip()
    page_size = normalize_page_size(page_size_value) if page_size_value else ""
    page = int(page_value) if page_value.isdigit() else 1
    car_query = Car.query.order_by("id", reverse=True)
    if not page_size and not page_value:
        cars = car_query.all()
        prefetch_car_relations(cars)
        return jsonify([serialize_car(car) for car in cars])
    if not page_size:
        page_size = get_page_size()
    if page_size not in PAGINATION_OPTIONS:
        return jsonify({"error": "Invalid page size."}), 400
    paged_cars, pagination = paginate_query(car_query, page, page_size, "main.api_cars", {})
    prefetch_car_relations(paged_cars)
    return jsonify({"items": [serialize_car(car) for car in paged_cars], "pagination": pagination})


@main_bp.route("/api/cars/<int:car_id>")
def api_car_detail(car_id: int):
    car = Car.query.get_or_404(car_id)
    return jsonify(serialize_car(car))


@main_bp.route("/api/railroads")
def api_railroads():
    railroads = Railroad.query.all()
    return jsonify([
        {
            "id": r.id,
            "reporting_mark": r.reporting_mark,
            "name": r.name,
            "start_date": r.start_date,
            "end_date": r.end_date,
            "merged_into": r.merged_into,
            "merged_from": r.merged_from,
            "notes": r.notes,
        }
        for r in railroads
    ])


@main_bp.route("/api/car-classes")
def api_car_classes():
    classes = CarClass.query.all()
    return jsonify([
        {
            "id": c.id,
            "code": c.code,
            "car_type": c.car_type,
            "is_locomotive": c.is_locomotive,
            "era": c.era,
            "wheel_arrangement": c.wheel_arrangement,
            "tender_axles": c.tender_axles,
            "capacity": c.capacity,
            "weight": c.weight,
            "load_limit": c.load_limit,
            "aar_plate": c.aar_plate,
            "internal_length": c.internal_length,
            "internal_width": c.internal_width,
            "internal_height": c.internal_height,
            "notes": c.notes,
        }
        for c in classes
    ])


@main_bp.route("/api/search")
def api_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    cars = search_cars(query)
    return jsonify([serialize_car(car) for car in cars])


def search_cars(query: str) -> list[Car]:
    if not query:
        return []
    needle = query.lower()

    def matches(value: str | None) -> bool:
        return bool(value) and needle in value.lower()

    results = []
    for car in Car.query.all():
        values = [
            car.car_number,
            car.reporting_mark_override,
            car.car_type_override,
            car.load,
            car.notes,
            car.actual_weight,
            car.actual_length,
            car.scale,
            car.gauge,
        ]
        if car.car_class:
            values.extend([car.car_class.code, car.car_class.car_type])
        if car.railroad:
            values.extend([car.railroad.reporting_mark, car.railroad.name])
        if car.location:
            values.append(car.location.name)
        if any(matches(value) for value in values):
            results.append(car)
    return results


def apply_load_form(load: LoadType, form) -> None:
    load.name = form.get("name", "").strip()
    load.era = form.get("era", "").strip()
    load.brand = form.get("brand", "").strip()
    load.lettering = form.get("lettering", "").strip()
    load.msrp = form.get("msrp", "").strip()
    load.price = form.get("price", "").strip()
    load.upc = form.get("upc", "").strip()
    def read_measurement(value_key: str, unit_key: str, fallback_key: str) -> str | None:
        value = form.get(value_key, "").strip()
        unit = form.get(unit_key, "").strip()
        if value and unit:
            return f"{value} {unit}"
        if value:
            return value
        fallback = form.get(fallback_key, "").strip()
        return fallback or None

    load.length = read_measurement("length_value", "length_unit", "length")
    load.width = read_measurement("width_value", "width_unit", "width")
    load.height = read_measurement("height_value", "height_unit", "height")
    load.weight = read_measurement("weight_value", "weight_unit", "weight")
    load.repairs_required = form.get("repairs_required", "").strip()
    load.notes = form.get("notes", "").strip()
    class_id = form.get("car_class_id", "").strip()
    if class_id and class_id.isdigit():
        load.car_class_id = int(class_id)
    else:
        load.car_class_id = None
    railroad_id = form.get("railroad_id", "").strip()
    if railroad_id and railroad_id.isdigit():
        load.railroad_id = int(railroad_id)
    else:
        load.railroad_id = None


def apply_load_placement_form(placement: LoadPlacement, form) -> bool:
    quantity = form.get("quantity", "").strip()
    placement.quantity = int(quantity) if quantity.isdigit() and int(quantity) > 0 else 1
    car_id = form.get("car_id", "").strip()
    location_id = form.get("location_id", "").strip()
    placement.car_id = int(car_id) if car_id.isdigit() else None
    placement.location_id = int(location_id) if location_id.isdigit() else None
    if placement.car_id and placement.location_id:
        placement.location_id = None
    return bool(placement.car_id or placement.location_id)


def apply_car_form(car: Car, form) -> None:
    has_reporting_mark = "reporting_mark" in form
    has_railroad_name = "railroad_name" in form
    reporting_mark = (
        form.get("reporting_mark", car.railroad.reporting_mark if car.railroad else "").strip()
        if has_reporting_mark
        else None
    )
    if reporting_mark and reporting_mark.lower() in {"none", "null"}:
        reporting_mark = ""
    clear_railroad = form.get("clear_railroad") == "1"
    railroad_name = (
        form.get("railroad_name", car.railroad.name if car.railroad else "").strip()
        if has_railroad_name
        else None
    )
    railroad = car.railroad
    if clear_railroad:
        railroad = None
    elif has_reporting_mark or has_railroad_name:
        if reporting_mark:
            railroad = Railroad.query.filter_by(reporting_mark=reporting_mark).first()
            if not railroad:
                railroad = Railroad(reporting_mark=reporting_mark, name=railroad_name or reporting_mark)
                db.session.add(railroad)
        elif railroad_name:
            railroad = Railroad.query.filter_by(name=railroad_name).first()
            if not railroad:
                railroad = Railroad(reporting_mark=None, name=railroad_name)
                db.session.add(railroad)
    car.railroad = railroad
    if clear_railroad or has_reporting_mark or has_railroad_name:
        if railroad is None:
            car.reporting_mark_override = reporting_mark or None
        else:
            car.reporting_mark_override = None

    car_type_value = form.get("car_type", "").strip()
    car.car_number = form.get("car_number", "").strip()
    car.brand = form.get("brand", "").strip()
    car.upc = form.get("upc", "").strip()
    car.dcc_id = form.get("dcc_id", "").strip()
    car.traction_drivers = form.get("traction_drivers") == "on"
    capacity_value = form.get("capacity", "").strip()
    weight_value = form.get("weight", "").strip()
    load_limit_value = form.get("load_limit", "").strip()
    aar_plate_value = form.get("aar_plate", "").strip()
    actual_weight_value = form.get("actual_weight_value", "").strip()
    actual_weight_unit = form.get("actual_weight_unit", "").strip()
    if actual_weight_value and actual_weight_unit:
        car.actual_weight = f"{actual_weight_value} {actual_weight_unit}"
    else:
        car.actual_weight = actual_weight_value or None
    actual_length_value = form.get("actual_length_value", "").strip()
    actual_length_unit = form.get("actual_length_unit", "").strip()
    if actual_length_value and actual_length_unit:
        car.actual_length = f"{actual_length_value} {actual_length_unit}"
    else:
        car.actual_length = actual_length_value or None
    scale_value = normalize_scale_input(form.get("scale", ""))
    gauge_value = normalize_gauge_input(form.get("gauge", ""))
    car.scale = scale_value or None
    car.gauge = gauge_value or None
    car.built = form.get("built", "").strip()
    car.alt_date = form.get("alt_date", "").strip()
    car.reweight_date = form.get("reweight_date", "").strip()
    car.repack_bearings_date = form.get("repack_bearings_date", "").strip()
    car.other_lettering = form.get("other_lettering", "").strip()
    car.msrp = form.get("msrp", "").strip()
    car.price = form.get("price", "").strip()
    car.load = form.get("load", "").strip()
    car.repairs_required = form.get("repairs_required", "").strip()
    car.notes = form.get("notes", "").strip()

    class_code = form.get("car_class", "").strip()
    if class_code:
        car_class = CarClass.query.filter_by(code=class_code).first()
        created_class = False
        if not car_class:
            car_class = CarClass(code=class_code)
            db.session.add(car_class)
            created_class = True
        class_wheel = form.get("class_wheel_arrangement", "").strip()
        class_tender = form.get("class_tender_axles", "").strip()
        class_is_locomotive = form.get("is_locomotive") == "on"
        if class_wheel and not car_class.wheel_arrangement:
            car_class.wheel_arrangement = class_wheel
        if class_tender and not car_class.tender_axles:
            car_class.tender_axles = class_tender
        if car_type_value and not car_class.car_type:
            car_class.car_type = car_type_value
        if car_class.is_locomotive is None:
            car_class.is_locomotive = class_is_locomotive

        if capacity_value and (created_class or not car_class.capacity):
            car_class.capacity = capacity_value
        if weight_value and (created_class or not car_class.weight):
            car_class.weight = weight_value
        if load_limit_value and (created_class or not car_class.load_limit):
            car_class.load_limit = load_limit_value
        if aar_plate_value and not car_class.aar_plate:
            car_class.aar_plate = aar_plate_value
        class_internal_length = form.get("internal_length", "").strip()
        class_internal_width = form.get("internal_width", "").strip()
        class_internal_height = form.get("internal_height", "").strip()
        if class_internal_length and not car_class.internal_length:
            car_class.internal_length = class_internal_length
        if class_internal_width and not car_class.internal_width:
            car_class.internal_width = class_internal_width
        if class_internal_height and not car_class.internal_height:
            car_class.internal_height = class_internal_height

        if created_class:
            car.capacity_override = None
            car.weight_override = None
            car.load_limit_override = None
            car.aar_plate_override = None
            car.car_type_override = None
            car.wheel_arrangement_override = None
            car.tender_axles_override = None
            car.is_locomotive_override = None
            car.internal_length_override = None
            car.internal_width_override = None
            car.internal_height_override = None
        else:
            car.capacity_override = (
                capacity_value if capacity_value and car_class.capacity and capacity_value != car_class.capacity else None
            )
            car.weight_override = (
                weight_value if weight_value and car_class.weight and weight_value != car_class.weight else None
            )
            car.load_limit_override = (
                load_limit_value if load_limit_value and car_class.load_limit and load_limit_value != car_class.load_limit else None
            )
            car.aar_plate_override = (
                aar_plate_value if aar_plate_value and car_class.aar_plate and aar_plate_value != car_class.aar_plate else None
            )
            car.internal_length_override = (
                class_internal_length
                if class_internal_length and car_class.internal_length and class_internal_length != car_class.internal_length
                else None
            )
            car.internal_width_override = (
                class_internal_width
                if class_internal_width and car_class.internal_width and class_internal_width != car_class.internal_width
                else None
            )
            car.internal_height_override = (
                class_internal_height
                if class_internal_height and car_class.internal_height and class_internal_height != car_class.internal_height
                else None
            )
            car.car_type_override = (
                car_type_value if car_type_value and car_class.car_type and car_type_value != car_class.car_type else None
            )
            car.wheel_arrangement_override = (
                class_wheel
                if class_wheel and car_class.wheel_arrangement and class_wheel != car_class.wheel_arrangement
                else None
            )
            car.tender_axles_override = (
                class_tender
                if class_tender and car_class.tender_axles and class_tender != car_class.tender_axles
                else None
            )
            if car_class.is_locomotive is not None:
                car.is_locomotive_override = (
                    class_is_locomotive if class_is_locomotive != car_class.is_locomotive else None
                )
        car.car_class = car_class
    else:
        car.car_class = None
        car.capacity_override = capacity_value or None
        car.weight_override = weight_value or None
        car.load_limit_override = load_limit_value or None
        car.aar_plate_override = aar_plate_value or None
        car.car_type_override = car_type_value or None
        car.wheel_arrangement_override = form.get("class_wheel_arrangement", "").strip() or None
        car.tender_axles_override = form.get("class_tender_axles", "").strip() or None
        car.is_locomotive_override = True if form.get("is_locomotive") == "on" else None
        car.internal_length_override = form.get("internal_length", "").strip() or None
        car.internal_width_override = form.get("internal_width", "").strip() or None
        car.internal_height_override = form.get("internal_height", "").strip() or None

    location_name = form.get("location", "").strip()
    if location_name:
        car.location = get_or_create_location(location_name)
    else:
        car.location = None


@main_bp.route("/settings")
def settings():
    inspection_types = InspectionType.query.all()
    type_rows = inspection_type_tree(inspection_types)
    page_size = get_page_size()
    scale_options_text = get_scale_options_text()
    gauge_options_text = get_gauge_options_text()
    default_length_unit = get_default_length_unit()
    default_weight_unit = get_default_weight_unit()
    options = [
        {"value": value, "label": "All" if value == "all" else value} for value in PAGINATION_OPTIONS
    ]
    return render_template(
        "settings.html",
        inspection_types=type_rows,
        page_size=page_size,
        page_size_options=options,
        scale_options_text=scale_options_text,
        gauge_options_text=gauge_options_text,
        default_length_unit=default_length_unit,
        default_weight_unit=default_weight_unit,
        length_units=LENGTH_UNITS,
        weight_units=WEIGHT_UNITS,
    )


@main_bp.route("/settings/pagination", methods=["POST"])
def settings_pagination():
    page_size = request.form.get("page_size", "").strip()
    if page_size not in PAGINATION_OPTIONS:
        return "Invalid pagination size.", 400
    settings = get_app_settings()
    settings.page_size = page_size
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.settings"))


@main_bp.route("/settings/scale-gauge", methods=["POST"])
def settings_scale_gauge():
    scale_text = request.form.get("scale_options", "").strip()
    gauge_text = request.form.get("gauge_options", "").strip()
    scale_options = build_scale_options(scale_text)
    gauge_options = build_gauge_options(gauge_text)
    settings = get_app_settings()
    settings.scale_options = "\n".join(
        option["raw"] for option in scale_options if option.get("raw")
    )
    settings.gauge_options = "\n".join(
        option["raw"] for option in gauge_options if option.get("raw")
    )
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.settings"))


@main_bp.route("/settings/units", methods=["POST"])
def settings_units():
    length_unit = request.form.get("default_length_unit", "").strip().lower()
    weight_unit = request.form.get("default_weight_unit", "").strip().lower()
    if length_unit not in LENGTH_UNIT_TO_IN:
        return "Invalid length unit.", 400
    if weight_unit not in WEIGHT_UNIT_TO_OZ:
        return "Invalid weight unit.", 400
    settings = get_app_settings()
    settings.default_length_unit = length_unit
    settings.default_weight_unit = weight_unit
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.settings"))


@main_bp.route("/settings/inspection-types/new", methods=["GET", "POST"])
def inspection_type_new():
    inspection_types = InspectionType.query.all()
    type_rows = inspection_type_tree(inspection_types)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        parent_id = request.form.get("parent_id", "").strip()
        if not name:
            return "Inspection type name is required.", 400
        inspection_type = InspectionType(name=name)
        if parent_id.isdigit():
            inspection_type.parent_id = int(parent_id)
        db.session.add(inspection_type)
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.settings"))
    return render_template("inspection_type_form.html", inspection_type=None, inspection_types=type_rows)


@main_bp.route("/settings/inspection-types/<int:type_id>/edit", methods=["GET", "POST"])
def inspection_type_edit(type_id: int):
    inspection_type = InspectionType.query.get_or_404(type_id)
    inspection_types = InspectionType.query.all()
    type_rows = inspection_type_tree(inspection_types, excluded_id=inspection_type.id)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        parent_id = request.form.get("parent_id", "").strip()
        if not name:
            return "Inspection type name is required.", 400
        if parent_id.isdigit() and int(parent_id) == inspection_type.id:
            return "Inspection type cannot parent itself.", 400
        inspection_type.name = name
        inspection_type.parent_id = int(parent_id) if parent_id.isdigit() else None
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.settings"))
    return render_template(
        "inspection_type_form.html",
        inspection_type=inspection_type,
        inspection_types=type_rows,
    )


@main_bp.route("/settings/inspection-types/<int:type_id>/delete", methods=["POST"])
def inspection_type_delete(type_id: int):
    inspection_type = InspectionType.query.get_or_404(type_id)
    if InspectionType.query.filter_by(parent_id=inspection_type.id).count() > 0:
        return "Cannot delete an inspection type with children.", 400
    if CarInspection.query.filter_by(inspection_type_id=inspection_type.id).count() > 0:
        return "Cannot delete an inspection type used in inspections.", 400
    db.session.delete(inspection_type)
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.settings"))


def serialize_car(car: Car) -> dict:
    class_capacity = car.car_class.capacity if car.car_class else None
    class_weight = car.car_class.weight if car.car_class else None
    class_load_limit = car.car_class.load_limit if car.car_class else None
    class_aar_plate = car.car_class.aar_plate if car.car_class else None
    class_internal_length = car.car_class.internal_length if car.car_class else None
    class_internal_width = car.car_class.internal_width if car.car_class else None
    class_internal_height = car.car_class.internal_height if car.car_class else None
    class_is_locomotive = car.car_class.is_locomotive if car.car_class else None
    is_locomotive = (
        car.is_locomotive_override if car.is_locomotive_override is not None else class_is_locomotive
    )
    return {
        "id": car.id,
        "car_type": car.car_type_override or (car.car_class.car_type if car.car_class else None),
        "car_number": car.car_number,
        "reporting_mark": car.railroad.reporting_mark if car.railroad else car.reporting_mark_override,
        "railroad": car.railroad.name if car.railroad else None,
        "car_class": car.car_class.code if car.car_class else None,
        "location": car.location.name if car.location else None,
        "brand": car.brand,
        "upc": car.upc,
        "dcc_id": car.dcc_id,
        "wheel_arrangement": car.wheel_arrangement_override
        or (car.car_class.wheel_arrangement if car.car_class else None),
        "tender_axles": car.tender_axles_override or (car.car_class.tender_axles if car.car_class else None),
        "traction_drivers": car.traction_drivers,
        "capacity": car.capacity_override or class_capacity,
        "weight": car.weight_override or class_weight,
        "load_limit": car.load_limit_override or class_load_limit,
        "actual_weight": car.actual_weight,
        "actual_length": car.actual_length,
        "scale": car.scale,
        "gauge": car.gauge,
        "aar_plate": car.aar_plate_override or class_aar_plate,
        "internal_length": car.internal_length_override or class_internal_length,
        "internal_width": car.internal_width_override or class_internal_width,
        "internal_height": car.internal_height_override or class_internal_height,
        "built": car.built,
        "alt_date": car.alt_date,
        "reweight_date": car.reweight_date,
        "repack_bearings_date": car.repack_bearings_date,
        "last_inspection_date": car.last_inspection_date,
        "other_lettering": car.other_lettering,
        "msrp": car.msrp,
        "price": car.price,
        "load": car.load,
        "repairs_required": car.repairs_required,
        "notes": car.notes,
        "capacity_override": car.capacity_override,
        "weight_override": car.weight_override,
        "load_limit_override": car.load_limit_override,
        "aar_plate_override": car.aar_plate_override,
        "internal_length_override": car.internal_length_override,
        "internal_width_override": car.internal_width_override,
        "internal_height_override": car.internal_height_override,
        "car_type_override": car.car_type_override,
        "wheel_arrangement_override": car.wheel_arrangement_override,
        "tender_axles_override": car.tender_axles_override,
        "is_locomotive_override": car.is_locomotive_override,
        "is_locomotive": is_locomotive,
    }
