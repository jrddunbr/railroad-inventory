# Repository Guidelines

## Project Structure & Module Organization
- `app/`: Flask application package (routes, templates, static assets).
- `app/templates/`: HTML/Jinja templates for Bulma UI pages.
- `app/static/`: CSS, JS, and Bulma assets.
- `data/`: CouchDB data volume and seed CSVs (e.g., `Railroad Inventory.csv`).
- `migrations/`: Database migration scripts (if using Alembic).
- `tests/`: Unit/integration tests.
- `scripts/`: One-off importers or maintenance tasks.
- `docs/`: Project documentation (schema, architecture notes).

Adjust paths as modules are added; keep domain logic (models, services) in `app/` and avoid mixing with view code.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate`: create/activate a local virtual environment.
- `pip install -r requirements.txt`: install dependencies.
- `flask --app app run --debug`: run the dev server locally.
- `pytest`: run the test suite.
- `python scripts/import_inventory.py "Railroad Inventory.csv"`: import seed inventory data (example).
- `rm -rf data/couchdb`: delete the CouchDB data volume before reseeding after schema changes.

## Coding Style & Naming Conventions
- Python: 4-space indentation, PEP 8 naming (`snake_case` for functions/vars, `PascalCase` for classes).
- Templates: keep UI logic minimal; prefer view helpers in `app/`.
- CouchDB document types use singular prefixes (`railroad`, `car`, `car_class`, `location`).
- Format/lint: use `black` and `ruff` if configured.

## Testing Guidelines
- Framework: `pytest` with fixtures under `tests/`.
- Name tests `test_*.py` and keep datasets in `tests/fixtures/`.
- Prefer fast unit tests; add integration tests for API endpoints and key pages.

## Commit & Pull Request Guidelines
- Commit messages: no history yet; follow `type: short summary` (e.g., `feat: add car class model`).
- Pull requests: include a summary, linked issue (if any), and screenshots for UI changes.

## Domain Notes (Inventory)
- Store reporting marks and railroads in a separate table from individual cars.
- Car classes store type, locomotive flag, wheel/tender details, and class defaults for capacity/weight/load limit.
- Car overrides only apply to capacity/weight/load limit (three weight-related fields total).
- Locations include Bags, Carriers, Flats (e.g., `JD-F1`), plus `staging_track` and `yard_track`.
- Locomotive tenders are part of the locomotive record, not standalone inventory.
- Reporting marks can be blank for some railroads (e.g., Amtrak).

## UI & API Expectations
- Use Bulma Design System for templates and components.
- Maintain API endpoints that back the UI pages (inventory, railroads, car classes, locomotive classes, search, car detail/edit, new entry).
- Keep forms keyboard-friendly; add/maintain auto-fill for reporting mark → railroad name and class → car type/locomotive details.
- Schema version lives in `schema_version` and is seeded on app init (see `docs/SCHEMA.md`).
