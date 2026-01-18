from __future__ import annotations

from dataclasses import dataclass, field

from app.storage import BaseModel, QueryDescriptor


@dataclass
class Railroad(BaseModel):
    doc_type = "railroad"
    counter_key = "railroads"
    query = QueryDescriptor()

    reporting_mark: str | None = None
    name: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    merged_into: str | None = None
    merged_from: str | None = None
    notes: str | None = None
    representative_logo_id: int | None = None
    _representative_logo_ref: RailroadLogo | None = field(default=None, repr=False, compare=False)

    @property
    def cars(self) -> list[Car]:
        if not self._store:
            return []
        return self._store.filter_by(Car, railroad_id=self.id)

    @property
    def color_schemes(self) -> list[RailroadColorScheme]:
        if not self._store:
            return []
        return self._store.filter_by(RailroadColorScheme, railroad_id=self.id)

    @property
    def logos(self) -> list[RailroadLogo]:
        if not self._store:
            return []
        return self._store.filter_by(RailroadLogo, railroad_id=self.id)

    @property
    def slogans(self) -> list[RailroadSlogan]:
        if not self._store:
            return []
        return self._store.filter_by(RailroadSlogan, railroad_id=self.id)

    @property
    def representative_logo(self) -> RailroadLogo | None:
        if self._representative_logo_ref is not None:
            return self._representative_logo_ref
        if not self._store or not self.representative_logo_id:
            return None
        return self._store.get(RailroadLogo, self.representative_logo_id)

    @representative_logo.setter
    def representative_logo(self, value: RailroadLogo | None) -> None:
        self._representative_logo_ref = value
        self.representative_logo_id = value.id if value else None


@dataclass
class CarClass(BaseModel):
    doc_type = "car_class"
    counter_key = "car_classes"
    query = QueryDescriptor()

    code: str | None = None
    car_type: str | None = None
    wheel_arrangement: str | None = None
    tender_axles: str | None = None
    is_locomotive: bool | None = None
    era: str | None = None
    load_limit: str | None = None
    aar_plate: str | None = None
    capacity: str | None = None
    weight: str | None = None
    notes: str | None = None
    internal_length: str | None = None
    internal_width: str | None = None
    internal_height: str | None = None

    @property
    def cars(self) -> list[Car]:
        if not self._store:
            return []
        return self._store.filter_by(Car, car_class_id=self.id)

    @property
    def loads(self) -> list[LoadType]:
        if not self._store:
            return []
        return self._store.filter_by(LoadType, car_class_id=self.id)


@dataclass
class Location(BaseModel):
    doc_type = "location"
    counter_key = "locations"
    query = QueryDescriptor()

    name: str | None = None
    location_type: str | None = None
    parent_id: int | None = None
    _parent_ref: Location | None = field(default=None, repr=False, compare=False)

    @property
    def parent(self) -> Location | None:
        if self._parent_ref is not None:
            return self._parent_ref
        if not self._store or not self.parent_id:
            return None
        return self._store.get(Location, self.parent_id)

    @parent.setter
    def parent(self, value: Location | None) -> None:
        self._parent_ref = value
        self.parent_id = value.id if value else None

    @property
    def children(self) -> list[Location]:
        if not self._store:
            return []
        return self._store.filter_by(Location, parent_id=self.id)

    @property
    def cars(self) -> list[Car]:
        if not self._store:
            return []
        return self._store.filter_by(Car, location_id=self.id)

    @property
    def load_placements(self) -> list[LoadPlacement]:
        if not self._store:
            return []
        return self._store.filter_by(LoadPlacement, location_id=self.id)


@dataclass
class Car(BaseModel):
    doc_type = "car"
    counter_key = "cars"
    query = QueryDescriptor()

    railroad_id: int | None = None
    car_class_id: int | None = None
    location_id: int | None = None

    car_number: str | None = None
    reporting_mark_override: str | None = None
    brand: str | None = None
    upc: str | None = None
    dcc_id: str | None = None
    traction_drivers: bool | None = None
    car_type_override: str | None = None
    wheel_arrangement_override: str | None = None
    tender_axles_override: str | None = None
    is_locomotive_override: bool | None = None
    capacity_override: str | None = None
    weight_override: str | None = None
    load_limit_override: str | None = None
    actual_weight: str | None = None
    scale: str | None = None
    gauge: str | None = None
    aar_plate_override: str | None = None
    built: str | None = None
    alt_date: str | None = None
    reweight_date: str | None = None
    repack_bearings_date: str | None = None
    last_inspection_date: str | None = None
    other_lettering: str | None = None
    msrp: str | None = None
    price: str | None = None
    load: str | None = None
    repairs_required: str | None = None
    notes: str | None = None
    internal_length_override: str | None = None
    internal_width_override: str | None = None
    internal_height_override: str | None = None

    _railroad_ref: Railroad | None = field(default=None, repr=False, compare=False)
    _car_class_ref: CarClass | None = field(default=None, repr=False, compare=False)
    _location_ref: Location | None = field(default=None, repr=False, compare=False)

    @property
    def railroad(self) -> Railroad | None:
        if self._railroad_ref is not None:
            return self._railroad_ref
        if not self._store or not self.railroad_id:
            return None
        return self._store.get(Railroad, self.railroad_id)

    @railroad.setter
    def railroad(self, value: Railroad | None) -> None:
        self._railroad_ref = value
        self.railroad_id = value.id if value else None

    @property
    def car_class(self) -> CarClass | None:
        if self._car_class_ref is not None:
            return self._car_class_ref
        if not self._store or not self.car_class_id:
            return None
        return self._store.get(CarClass, self.car_class_id)

    @car_class.setter
    def car_class(self, value: CarClass | None) -> None:
        self._car_class_ref = value
        self.car_class_id = value.id if value else None

    @property
    def location(self) -> Location | None:
        if self._location_ref is not None:
            return self._location_ref
        if not self._store or not self.location_id:
            return None
        return self._store.get(Location, self.location_id)

    @location.setter
    def location(self, value: Location | None) -> None:
        self._location_ref = value
        self.location_id = value.id if value else None

    @property
    def load_placements(self) -> list[LoadPlacement]:
        if not self._store:
            return []
        return self._store.filter_by(LoadPlacement, car_id=self.id)

    @property
    def inspections(self) -> list[CarInspection]:
        if not self._store:
            return []
        return CarInspection.query.filter_by(car_id=self.id).order_by("inspection_date", reverse=True).all()

    def prepare_save(self) -> None:
        if self._railroad_ref and not self.railroad_id and self._railroad_ref.id:
            self.railroad_id = self._railroad_ref.id
        if self._car_class_ref and not self.car_class_id and self._car_class_ref.id:
            self.car_class_id = self._car_class_ref.id
        if self._location_ref and not self.location_id and self._location_ref.id:
            self.location_id = self._location_ref.id


@dataclass
class CarInspection(BaseModel):
    doc_type = "car_inspection"
    counter_key = "car_inspections"
    query = QueryDescriptor()

    car_id: int | None = None
    inspection_type_id: int | None = None
    inspection_date: str | None = None
    details: str | None = None


@dataclass
class AppSettings(BaseModel):
    doc_type = "app_settings"
    counter_key = "app_settings"
    query = QueryDescriptor()

    page_size: str | None = None
    scale_options: str | None = None
    gauge_options: str | None = None
    passed: bool | None = None

    @property
    def car(self) -> Car | None:
        if not self._store or not self.car_id:
            return None
        return self._store.get(Car, self.car_id)

    @property
    def inspection_type(self) -> InspectionType | None:
        if not self._store or not self.inspection_type_id:
            return None
        return self._store.get(InspectionType, self.inspection_type_id)


@dataclass
class InspectionType(BaseModel):
    doc_type = "inspection_type"
    counter_key = "inspection_types"
    query = QueryDescriptor()

    name: str | None = None
    parent_id: int | None = None

    @property
    def parent(self) -> InspectionType | None:
        if not self._store or not self.parent_id:
            return None
        return self._store.get(InspectionType, self.parent_id)

    @property
    def children(self) -> list[InspectionType]:
        if not self._store:
            return []
        return InspectionType.query.filter_by(parent_id=self.id).order_by("name").all()


@dataclass
class RailroadColorScheme(BaseModel):
    doc_type = "railroad_color_scheme"
    counter_key = "railroad_color_schemes"
    query = QueryDescriptor()

    railroad_id: int | None = None
    description: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    colors: str | None = None

    @property
    def railroad(self) -> Railroad | None:
        if not self._store or not self.railroad_id:
            return None
        return self._store.get(Railroad, self.railroad_id)


@dataclass
class RailroadLogo(BaseModel):
    doc_type = "railroad_logo"
    counter_key = "railroad_logos"
    query = QueryDescriptor()

    railroad_id: int | None = None
    description: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    image_path: str | None = None

    @property
    def railroad(self) -> Railroad | None:
        if not self._store or not self.railroad_id:
            return None
        return self._store.get(Railroad, self.railroad_id)


@dataclass
class RailroadSlogan(BaseModel):
    doc_type = "railroad_slogan"
    counter_key = "railroad_slogans"
    query = QueryDescriptor()

    railroad_id: int | None = None
    description: str | None = None
    slogan_text: str | None = None
    start_date: str | None = None
    end_date: str | None = None

    @property
    def railroad(self) -> Railroad | None:
        if not self._store or not self.railroad_id:
            return None
        return self._store.get(Railroad, self.railroad_id)


@dataclass
class LoadType(BaseModel):
    doc_type = "load"
    counter_key = "loads"
    query = QueryDescriptor()

    name: str | None = None
    car_class_id: int | None = None
    railroad_id: int | None = None
    era: str | None = None
    brand: str | None = None
    lettering: str | None = None
    msrp: str | None = None
    price: str | None = None
    upc: str | None = None
    length: str | None = None
    width: str | None = None
    height: str | None = None
    repairs_required: str | None = None
    notes: str | None = None

    @property
    def car_class(self) -> CarClass | None:
        if not self._store or not self.car_class_id:
            return None
        return self._store.get(CarClass, self.car_class_id)

    @property
    def railroad(self) -> Railroad | None:
        if not self._store or not self.railroad_id:
            return None
        return self._store.get(Railroad, self.railroad_id)

    @property
    def placements(self) -> list[LoadPlacement]:
        if not self._store:
            return []
        return self._store.filter_by(LoadPlacement, load_id=self.id)


@dataclass
class LoadPlacement(BaseModel):
    doc_type = "load_placement"
    counter_key = "load_placements"
    query = QueryDescriptor()

    load_id: int | None = None
    car_id: int | None = None
    location_id: int | None = None
    quantity: int = 1

    @property
    def load(self) -> LoadType | None:
        if not self._store or not self.load_id:
            return None
        return self._store.get(LoadType, self.load_id)

    @property
    def car(self) -> Car | None:
        if not self._store or not self.car_id:
            return None
        return self._store.get(Car, self.car_id)

    @property
    def location(self) -> Location | None:
        if not self._store or not self.location_id:
            return None
        return self._store.get(Location, self.location_id)


__all__ = [
    "Car",
    "CarInspection",
    "CarClass",
    "InspectionType",
    "LoadPlacement",
    "LoadType",
    "Location",
    "Railroad",
    "RailroadColorScheme",
    "RailroadLogo",
    "RailroadSlogan",
]
