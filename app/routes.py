from __future__ import annotations

import csv
import io
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



def ensure_db_backup() -> None:
    ensure_periodic_backup(db.store.db)


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
    cars = Car.query.order_by("id", reverse=True).all()
    return render_template("inventory.html", cars=cars)


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
    return render_template("railroads.html", railroads=railroads)


@main_bp.route("/locations")
def locations():
    locations = Location.query.order_by("name").all()
    return render_template("locations.html", locations=locations)


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
    return render_template("railroad_detail.html", railroad=railroad, cars=cars)


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
    return render_template("car_classes.html", car_classes=car_classes)


@main_bp.route("/locomotive-classes")
def locomotive_classes():
    classes = CarClass.query.order_by("code").all()
    locomotive_classes = [c for c in classes if c.is_locomotive]
    return render_template("locomotive_classes.html", locomotive_classes=locomotive_classes)


@main_bp.route("/loads")
def loads():
    loads = LoadType.query.order_by("name").all()
    return render_template("loads.html", loads=loads)


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
    return render_template("load_form.html", load=None, classes=classes, railroads=railroads)


@main_bp.route("/loads/<int:load_id>")
def load_detail(load_id: int):
    load = LoadType.query.get_or_404(load_id)
    placements = LoadPlacement.query.filter_by(load_id=load.id).all()
    return render_template("load_detail.html", load=load, placements=placements)


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
    return render_template("load_form.html", load=load, classes=classes, railroads=railroads)


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
        placement = LoadPlacement(load=load)
        if not apply_load_placement_form(placement, request.form):
            return "Select a car or location for this load placement.", 400
        db.session.add(placement)
        db.session.commit()
        ensure_db_backup()
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
        placement = LoadPlacement(load=load)
        if not apply_load_placement_form(placement, request.form):
            return "Select a car or location for this load placement.", 400
        db.session.add(placement)
        db.session.commit()
        ensure_db_backup()
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
        if not apply_load_placement_form(placement, request.form):
            return "Select a car or location for this load placement.", 400
        db.session.commit()
        ensure_db_backup()
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
    db.session.delete(placement)
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.load_detail", load_id=load_id))


@main_bp.route("/car-classes/<int:class_id>")
def car_class_detail(class_id: int):
    car_class = CarClass.query.get_or_404(class_id)
    cars = Car.query.filter_by(car_class_id=car_class.id).order_by("id", reverse=True).all()
    return render_template("car_class_detail.html", car_class=car_class, cars=cars)


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
    return render_template("location_detail.html", location=location, cars=cars)


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
    return render_template("car_detail.html", car=car)


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
        apply_car_form(car, request.form)
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.car_detail", car_id=car.id))
    railroads = Railroad.query.order_by("reporting_mark").all()
    classes = CarClass.query.order_by("code").all()
    locations = Location.query.order_by("name").all()
    return render_template(
        "car_form.html",
        car=car,
        railroads=railroads,
        classes=classes,
        locations=locations,
        prefill={},
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
        return redirect(url_for("main.car_detail", car_id=car.id))
    prefill = {
        "reporting_mark": request.args.get("reporting_mark", "").strip(),
        "railroad_name": request.args.get("railroad_name", "").strip(),
        "car_class": request.args.get("car_class", "").strip(),
        "car_type": request.args.get("car_type", "").strip(),
        "capacity": request.args.get("capacity", "").strip(),
        "weight": request.args.get("weight", "").strip(),
        "load_limit": request.args.get("load_limit", "").strip(),
        "built": request.args.get("built", "").strip(),
        "brand": request.args.get("brand", "").strip(),
        "price": request.args.get("price", "").strip(),
        "msrp": request.args.get("msrp", "").strip(),
    }
    railroads = Railroad.query.order_by("reporting_mark").all()
    classes = CarClass.query.order_by("code").all()
    locations = Location.query.order_by("name").all()
    return render_template(
        "car_form.html",
        car=None,
        railroads=railroads,
        classes=classes,
        locations=locations,
        prefill=prefill,
        form_action=url_for("main.car_new"),
    )


@main_bp.route("/search")
def search():
    query = request.args.get("q", "").strip()
    cars = search_cars(query)
    return render_template("search.html", cars=cars, query=query)


@main_bp.route("/api/cars")
def api_cars():
    cars = Car.query.all()
    return jsonify([serialize_car(car) for car in cars])


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
    load.length = form.get("length", "").strip()
    load.width = form.get("width", "").strip()
    load.height = form.get("height", "").strip()
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
    return render_template("settings.html", inspection_types=type_rows)


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
        "internal_length_override": car.internal_length_override,
        "internal_width_override": car.internal_width_override,
        "internal_height_override": car.internal_height_override,
        "car_type_override": car.car_type_override,
        "wheel_arrangement_override": car.wheel_arrangement_override,
        "tender_axles_override": car.tender_axles_override,
        "is_locomotive_override": car.is_locomotive_override,
        "is_locomotive": is_locomotive,
    }
