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

## Containers
```bash
docker compose up --build
```

Podman users can run:
```bash
podman compose up --build
```

## Kubernetes
Build and load the image into your cluster (example uses the local tag):
```bash
docker build -t modelinventory:latest .
```

Apply the manifests:
```bash
kubectl apply -k k8s
```

Update secrets in `k8s/secrets.yaml`, then re-apply when you change them:
```bash
kubectl apply -f k8s/secrets.yaml
```

Port-forward the app service:
```bash
kubectl port-forward service/modelinventory 5000:5000
```

## Notes
- CouchDB runs in a Podman container named `modelinventory-couchdb`.
- The CouchDB data volume lives at `data/couchdb`.

## Documentation
- Schema details are in `docs/SCHEMA.md`.
