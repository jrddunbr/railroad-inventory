# Database Schema

This document describes the current database schema and common alternate names used in the project or source data.

## Schema Version
- Table: `schema_version`
- Fields:
  - `id` (integer, primary key)
  - `version` (string)

## Railroads (`railroads`)
- `id` (integer, primary key)
- `reporting_mark` (string, nullable)
- `name` (string)
- `start_date` (string, optional)
- `end_date` (string, optional)
- `merged_into` (string, optional)
- `merged_from` (string, optional)
- `notes` (text, optional)
- `representative_logo_id` (integer, FK -> `railroad_logos.id`, optional)

## Car Classes (`car_classes`)
- `id` (integer, primary key)
- `code` (string)
- `car_type` (string, optional)
- `era` (string, optional)
- `wheel_arrangement` (string, optional)
- `tender_axles` (string, optional)
- `is_locomotive` (boolean, optional)
- `capacity` (string, optional)
- `weight` (string, optional)
- `load_limit` (string, optional)
- `notes` (text, optional)
- `internal_length` (string, optional)
- `internal_width` (string, optional)
- `internal_height` (string, optional)

## Locations (`locations`)
- `id` (integer, primary key)
- `name` (string)
- `location_type` (string) values: `bag`, `carrier`, `flat`, `staging_track`, `yard_track`
- `parent_id` (integer, self-reference, optional)

## Cars (`cars`)
- `id` (integer, primary key)
- `railroad_id` (integer, FK -> `railroads.id`)
- `car_class_id` (integer, FK -> `car_classes.id`)
- `location_id` (integer, FK -> `locations.id`)
- `car_number` (string, optional)
- `reporting_mark_override` (string, optional)
- `brand` (string, optional)
- `upc` (string, optional)
- `dcc_id` (string, optional)
- `traction_drivers` (boolean, optional)
- `car_type_override` (string, optional)
- `wheel_arrangement_override` (string, optional)
- `tender_axles_override` (string, optional)
- `is_locomotive_override` (boolean, optional)
- `capacity_override` (string, optional)
- `weight_override` (string, optional)
- `load_limit_override` (string, optional)
- `built` (string, optional)
- `alt_date` (string, optional)
- `reweight_date` (string, optional)
- `repack_bearings_date` (string, optional)
- `other_lettering` (string, optional)
- `msrp` (string, optional)
- `price` (string, optional)
- `load` (string, optional)
- `repairs_required` (string, optional)
- `notes` (text, optional)
- `internal_length_override` (string, optional)
- `internal_width_override` (string, optional)
- `internal_height_override` (string, optional)

## Loads (`loads`)
- `id` (integer, primary key)
- `name` (string)
- `car_class_id` (integer, FK -> `car_classes.id`, optional)
- `railroad_id` (integer, FK -> `railroads.id`, optional)
- `era` (string, optional)
- `brand` (string, optional)
- `lettering` (string, optional)
- `msrp` (string, optional)
- `price` (string, optional)
- `upc` (string, optional)
- `length` (string, optional)
- `width` (string, optional)
- `height` (string, optional)
- `repairs_required` (string, optional)
- `notes` (text, optional)

## Load Placements (`load_placements`)
- `id` (integer, primary key)
- `load_id` (integer, FK -> `loads.id`)
- `car_id` (integer, FK -> `cars.id`, optional)
- `location_id` (integer, FK -> `locations.id`, optional)
- `quantity` (integer)

## Railroad Color Schemes (`railroad_color_schemes`)
- `id` (integer, primary key)
- `railroad_id` (integer, FK -> `railroads.id`)
- `description` (string)
- `start_date` (string, optional)
- `end_date` (string, optional)
- `colors` (string, optional, comma-separated hex codes)

## Railroad Logos (`railroad_logos`)
- `id` (integer, primary key)
- `railroad_id` (integer, FK -> `railroads.id`)
- `description` (string)
- `start_date` (string, optional)
- `end_date` (string, optional)
- `image_path` (string, optional, relative to `app/static/`)

## Railroad Slogans (`railroad_slogans`)
- `id` (integer, primary key)
- `railroad_id` (integer, FK -> `railroads.id`)
- `description` (string)
- `slogan_text` (string, optional)
- `start_date` (string, optional)
- `end_date` (string, optional)

## Alternate Names and Legacy Labels
- `weight`: also known as "Light Weight" (empty car weight).
- `load_limit`: also known as "Total Weight" (max loaded weight).
- `capacity`: from CSV column "Capacity (Lettering)".
- `weight` (class/override): from CSV column "Weight (Lettering)".
- `built`: from CSV column "Built (Lettering)".
- `other_lettering`: matches "Other Lettering".
- `reporting_mark`: may be missing for some railroads (e.g., Amtrak).
- `car_type`: stored on car class; cars read it via class.
- `is_locomotive`: stored on car class; cars read it via class.
- `*_override` fields: used when no class is assigned or when a specific car must override class defaults.
