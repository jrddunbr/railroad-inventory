# Model Inventory

Model Inventory is a Python + Flask + SQLite app for tracking HO scale train inventory over a local network.
It stores railroads, car classes, and individual cars with detailed metadata and location tracking.

## Features
- Inventory list, railroad list, car class list, and search views.
- Individual car detail pages with edit and delete actions.
- Class-aware defaults for capacity, weight, and load limit, with per-car overrides.
- Location tracking for bags, carriers, flats, staging tracks, and yard tracks.
- CSV import support for seeding data.

## Quick Start
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/import_inventory.py "Railroad Inventory.csv"
flask --app app run --debug
```

Then open `http://127.0.0.1:5000/inventory`.

## Notes
- The database lives at `data/inventory.db`.
- If you change the schema, delete the database and re-import the CSV.

## Documentation
- Schema details are in `docs/SCHEMA.md`.
