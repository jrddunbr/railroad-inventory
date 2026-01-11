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

## Car Classes (`car_classes`)
- `id` (integer, primary key)
- `code` (string)
- `car_type` (string, optional)
- `wheel_arrangement` (string, optional)
- `tender_axles` (string, optional)
- `is_locomotive` (boolean, optional)
- `capacity` (string, optional)
- `weight` (string, optional)
- `load_limit` (string, optional)
- `notes` (text, optional)

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
- `car_type` (string)
- `car_number` (string, optional)
- `reporting_mark` (string, optional)
- `brand` (string, optional)
- `upc` (string, optional)
- `dcc_id` (string, optional)
- `traction_drivers` (boolean, optional)
- `capacity_override` (string, optional)
- `weight_override` (string, optional)
- `load_limit_override` (string, optional)
- `built` (string, optional)
- `alt_date` (string, optional)
- `reweight_date` (string, optional)
- `other_lettering` (string, optional)
- `msrp` (string, optional)
- `price` (string, optional)
- `load` (string, optional)
- `repairs_required` (string, optional)
- `notes` (text, optional)
- `is_locomotive` (boolean, optional)

## Alternate Names and Legacy Labels
- `weight`: also known as "Light Weight" (empty car weight).
- `load_limit`: also known as "Total Weight" (max loaded weight).
- `capacity`: from CSV column "Capacity (Lettering)".
- `weight` (class/override): from CSV column "Weight (Lettering)".
- `built`: from CSV column "Built (Lettering)".
- `other_lettering`: matches "Other Lettering".
- `reporting_mark`: may be missing for some railroads (e.g., Amtrak).
- `car_type`: stored on both car and class; class fills the car when known.
- `is_locomotive`: stored on both car and class; class determines car when known.
