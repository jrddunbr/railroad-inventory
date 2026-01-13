from __future__ import annotations

from app import db


class Railroad(db.Model):
    __tablename__ = "railroads"

    id = db.Column(db.Integer, primary_key=True)
    reporting_mark = db.Column(db.String(16), unique=True)
    name = db.Column(db.String(128), nullable=False)
    start_date = db.Column(db.String(32))
    end_date = db.Column(db.String(32))
    merged_into = db.Column(db.String(128))
    merged_from = db.Column(db.String(128))
    notes = db.Column(db.Text)
    representative_logo_id = db.Column(db.Integer, db.ForeignKey("railroad_logos.id"))

    cars = db.relationship("Car", back_populates="railroad")
    color_schemes = db.relationship(
        "RailroadColorScheme",
        back_populates="railroad",
        cascade="all, delete-orphan",
    )
    logos = db.relationship(
        "RailroadLogo",
        back_populates="railroad",
        cascade="all, delete-orphan",
        foreign_keys="RailroadLogo.railroad_id",
    )
    slogans = db.relationship(
        "RailroadSlogan",
        back_populates="railroad",
        cascade="all, delete-orphan",
    )
    representative_logo = db.relationship(
        "RailroadLogo",
        foreign_keys=[representative_logo_id],
        uselist=False,
    )

    def __repr__(self) -> str:
        return f"<Railroad {self.reporting_mark}>"


class CarClass(db.Model):
    __tablename__ = "car_classes"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False)
    car_type = db.Column(db.String(64))
    wheel_arrangement = db.Column(db.String(32))
    tender_axles = db.Column(db.String(32))
    is_locomotive = db.Column(db.Boolean)
    era = db.Column(db.String(64))
    load_limit = db.Column(db.String(32))
    capacity = db.Column(db.String(64))
    weight = db.Column(db.String(64))
    notes = db.Column(db.Text)

    cars = db.relationship("Car", back_populates="car_class")

    def __repr__(self) -> str:
        return f"<CarClass {self.code}>"


class Location(db.Model):
    __tablename__ = "locations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    location_type = db.Column(db.String(16), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("locations.id"))

    parent = db.relationship("Location", remote_side=[id], backref="children")
    cars = db.relationship("Car", back_populates="location")

    def __repr__(self) -> str:
        return f"<Location {self.name} ({self.location_type})>"


class Car(db.Model):
    __tablename__ = "cars"

    id = db.Column(db.Integer, primary_key=True)
    railroad_id = db.Column(db.Integer, db.ForeignKey("railroads.id"))
    car_class_id = db.Column(db.Integer, db.ForeignKey("car_classes.id"))
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"))

    car_number = db.Column(db.String(32))
    reporting_mark_override = db.Column(db.String(16))
    brand = db.Column(db.String(128))
    upc = db.Column(db.String(32))
    dcc_id = db.Column(db.String(32))
    traction_drivers = db.Column(db.Boolean)
    car_type_override = db.Column(db.String(64))
    wheel_arrangement_override = db.Column(db.String(32))
    tender_axles_override = db.Column(db.String(32))
    is_locomotive_override = db.Column(db.Boolean)
    capacity_override = db.Column(db.String(64))
    weight_override = db.Column(db.String(64))
    load_limit_override = db.Column(db.String(64))
    built = db.Column(db.String(64))
    alt_date = db.Column(db.String(64))
    reweight_date = db.Column(db.String(64))
    repack_bearings_date = db.Column(db.String(64))
    other_lettering = db.Column(db.String(128))
    msrp = db.Column(db.String(32))
    price = db.Column(db.String(32))
    load = db.Column(db.String(64))
    repairs_required = db.Column(db.String(64))
    notes = db.Column(db.Text)

    railroad = db.relationship("Railroad", back_populates="cars")
    car_class = db.relationship("CarClass", back_populates="cars")
    location = db.relationship("Location", back_populates="cars")

    def __repr__(self) -> str:
        return f"<Car {self.car_number}>"


class SchemaVersion(db.Model):
    __tablename__ = "schema_version"

    id = db.Column(db.Integer, primary_key=True)
    version = db.Column(db.String(32), nullable=False)


class RailroadColorScheme(db.Model):
    __tablename__ = "railroad_color_schemes"

    id = db.Column(db.Integer, primary_key=True)
    railroad_id = db.Column(db.Integer, db.ForeignKey("railroads.id"), nullable=False)
    description = db.Column(db.String(128), nullable=False)
    start_date = db.Column(db.String(32))
    end_date = db.Column(db.String(32))
    colors = db.Column(db.String(256))

    railroad = db.relationship("Railroad", back_populates="color_schemes")

    def __repr__(self) -> str:
        return f"<RailroadColorScheme {self.description}>"


class RailroadLogo(db.Model):
    __tablename__ = "railroad_logos"

    id = db.Column(db.Integer, primary_key=True)
    railroad_id = db.Column(db.Integer, db.ForeignKey("railroads.id"), nullable=False)
    description = db.Column(db.String(128), nullable=False)
    start_date = db.Column(db.String(32))
    end_date = db.Column(db.String(32))
    image_path = db.Column(db.String(256))

    railroad = db.relationship("Railroad", back_populates="logos", foreign_keys=[railroad_id])

    def __repr__(self) -> str:
        return f"<RailroadLogo {self.description}>"


class RailroadSlogan(db.Model):
    __tablename__ = "railroad_slogans"

    id = db.Column(db.Integer, primary_key=True)
    railroad_id = db.Column(db.Integer, db.ForeignKey("railroads.id"), nullable=False)
    description = db.Column(db.String(128), nullable=False)
    slogan_text = db.Column(db.String(256))
    start_date = db.Column(db.String(32))
    end_date = db.Column(db.String(32))

    railroad = db.relationship("Railroad", back_populates="slogans")

    def __repr__(self) -> str:
        return f"<RailroadSlogan {self.description}>"
