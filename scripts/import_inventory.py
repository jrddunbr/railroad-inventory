from __future__ import annotations

import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(BASE_DIR))

from app import create_app, db  # noqa: E402
from app.models import Car, CarClass, Location, Railroad  # noqa: E402


def get_or_create_railroad(reporting_mark: str | None, name: str) -> Railroad:
    if reporting_mark:
        railroad = Railroad.query.filter_by(reporting_mark=reporting_mark).first()
        if railroad:
            return railroad
    railroad = Railroad.query.filter_by(name=name).first()
    if railroad:
        return railroad
    railroad = Railroad(reporting_mark=reporting_mark or None, name=name or reporting_mark or "Unknown")
    db.session.add(railroad)
    return railroad


def get_or_create_class(code: str) -> CarClass:
    car_class = CarClass.query.filter_by(code=code).first()
    if car_class:
        return car_class
    car_class = CarClass(code=code)
    db.session.add(car_class)
    return car_class


def get_or_create_location(name: str) -> Location:
    location = Location.query.filter_by(name=name).first()
    if location:
        return location
    location_type = "bag"
    if "-F" in name:
        location_type = "flat"
    elif "staging" in name.lower() or " st" in name.lower():
        location_type = "staging_track"
    elif "yard" in name.lower() or " yd" in name.lower():
        location_type = "yard_track"
    elif "Carrier" in name or "carrier" in name:
        location_type = "carrier"
    location = Location(name=name, location_type=location_type)
    db.session.add(location)
    return location


def main(path: Path) -> None:
    app = create_app()
    with app.app_context():
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                reporting_mark = (row.get("Reporting Mark") or "").strip()
                railroad_name = (row.get("Railroad") or "").strip()
                car_class_code = (row.get("Car Class") or "").strip()
                car_type = (row.get("Car Type") or "").strip()
                class_wheel = (row.get("Wheel Arrangement") or "").strip()
                class_tender = (row.get("Tender Axles") or "").strip()
                class_capacity = (row.get("Capacity (Lettering)") or "").strip()
                class_weight = (row.get("Weight (Lettering)") or "").strip()
                class_load_limit = (row.get("Load Limit") or "").strip()
                class_aar_plate = (row.get("AAR Plate") or "").strip()
                location_name = (row.get("Location") or "").strip()

                railroad = None
                if reporting_mark or railroad_name:
                    railroad = get_or_create_railroad(reporting_mark or None, railroad_name)
                car_class = get_or_create_class(car_class_code) if car_class_code else None
                if car_class:
                    if car_type and not car_class.car_type:
                        car_class.car_type = car_type
                    if car_class.is_locomotive is None and car_type.lower().find("locomotive") != -1:
                        car_class.is_locomotive = True
                    if class_wheel and not car_class.wheel_arrangement:
                        car_class.wheel_arrangement = class_wheel
                    if class_tender and not car_class.tender_axles:
                        car_class.tender_axles = class_tender
                    if class_capacity and not car_class.capacity:
                        car_class.capacity = class_capacity
                    if class_weight and not car_class.weight:
                        car_class.weight = class_weight
                    if class_load_limit and not car_class.load_limit:
                        car_class.load_limit = class_load_limit
                    if class_aar_plate and not car_class.aar_plate:
                        car_class.aar_plate = class_aar_plate
                location = get_or_create_location(location_name) if location_name else None

                capacity_override = None
                weight_override = None
                load_limit_override = None
                aar_plate_override = None
                car_type_override = None
                wheel_override = None
                tender_override = None
                is_locomotive_override = None
                if car_class:
                    if class_capacity and car_class.capacity and class_capacity != car_class.capacity:
                        capacity_override = class_capacity
                    if class_weight and car_class.weight and class_weight != car_class.weight:
                        weight_override = class_weight
                    if class_load_limit and car_class.load_limit and class_load_limit != car_class.load_limit:
                        load_limit_override = class_load_limit
                    if class_aar_plate and car_class.aar_plate and class_aar_plate != car_class.aar_plate:
                        aar_plate_override = class_aar_plate
                    if car_type and car_class.car_type and car_type != car_class.car_type:
                        car_type_override = car_type
                    if class_wheel and car_class.wheel_arrangement and class_wheel != car_class.wheel_arrangement:
                        wheel_override = class_wheel
                    if class_tender and car_class.tender_axles and class_tender != car_class.tender_axles:
                        tender_override = class_tender
                else:
                    car_type_override = car_type or None
                    wheel_override = class_wheel or None
                    tender_override = class_tender or None
                    capacity_override = class_capacity or None
                    weight_override = class_weight or None
                    load_limit_override = class_load_limit or None
                    aar_plate_override = class_aar_plate or None
                    if car_type and car_type.lower().find("locomotive") != -1:
                        is_locomotive_override = True

                car = Car(
                    reporting_mark_override=reporting_mark if not railroad else None,
                    car_type_override=car_type_override,
                    wheel_arrangement_override=wheel_override,
                    tender_axles_override=tender_override,
                    is_locomotive_override=is_locomotive_override,
                    brand=(row.get("Brand") or "").strip(),
                    upc=(row.get("UPC") or "").strip(),
                    car_number=(row.get("Car #") or "").strip(),
                    dcc_id=(row.get("DCC ID") or "").strip(),
                    notes=(row.get("Notes") or "").strip(),
                    traction_drivers=(row.get("Traction Drivers") or "").strip().lower() == "yes",
                    capacity_override=capacity_override,
                    weight_override=weight_override,
                    load_limit_override=load_limit_override,
                    aar_plate_override=aar_plate_override,
                    built=(row.get("Built (Lettering)") or "").strip(),
                    alt_date=(row.get("Alt Date") or "").strip(),
                    reweight_date=(row.get("Reweight date") or "").strip(),
                    other_lettering=(row.get("Other Lettering") or "").strip(),
                    msrp=(row.get("MSRP") or "").strip(),
                    price=(row.get("Price") or "").strip(),
                    load=(row.get("Load") or "").strip(),
                    repairs_required=(row.get("Repairs Req’d") or "").strip(),
                )
                car.railroad = railroad
                car.car_class = car_class
                car.location = location
                db.session.add(car)

        db.session.commit()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/import_inventory.py <path-to-csv>")
        sys.exit(1)
    main(Path(sys.argv[1]))
