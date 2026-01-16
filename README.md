# Railroad Inventory

Railroad Inventory is a Python + Flask + CouchDB app for tracking HO scale train inventory over a local network.
It stores railroads, car classes, and individual cars with detailed metadata and location tracking.

Repository: https://github.com/jrddunbr/railroad-inventory

## Features
- Inventory list, railroad list, car class list, and search views.
- Individual car detail pages with edit and delete actions.
- Class-aware defaults for capacity, weight, and load limit, with per-car overrides.
- Location tracking for bags, carriers, flats, staging tracks, and yard tracks.
- CSV import support for seeding data.

## Quick Start
```bash
./run.sh
```

Rootless Podman not available?
- Rootful Podman: `./run-rootful.sh`
- Docker: `./run-docker.sh`

Configuration
- `./run.sh` generates `.env` with a random CouchDB password on first run.
- Copy `.env.example` to `.env` to customize credentials before starting.

Then open `http://127.0.0.1:5000/inventory`.

## Notes
- CouchDB runs in a Podman container named `modelinventory-couchdb`.
- The CouchDB data volume lives at `data/couchdb`.
- To migrate an existing SQLite database, run `python scripts/convert_sqlite_to_couchdb.py data/inventory.db`.

## Documentation
- Schema details are in `docs/SCHEMA.md`.
