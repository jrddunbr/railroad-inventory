from __future__ import annotations

import base64
import json
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Any

from couchdb import Database

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
COUCHDB_DIR = DATA_DIR / "couchdb"
BACKUP_DIR = DATA_DIR / "backups"
PERIODIC_DIR = BACKUP_DIR / "periodic"
SCHEMA_DIR = BACKUP_DIR / "schema"
STATE_FILE = BACKUP_DIR / "backup_state.json"
BACKUP_FILE_FORMAT = "railroad-inventory-backup"
BACKUP_FILE_VERSION = 1


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _get_update_seq(db: Database | None) -> str | None:
    if not db:
        return None
    info = db.info()
    seq = info.get("update_seq")
    return str(seq) if seq is not None else None


def _create_backup(destination: Path, label: str) -> Path | None:
    if not COUCHDB_DIR.exists():
        return None
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_path = destination / f"couchdb-{label}-{timestamp}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(COUCHDB_DIR, arcname="couchdb")
    return archive_path


def _total_size(files: list[Path]) -> int:
    return sum(path.stat().st_size for path in files if path.exists())


def _prune_periodic(max_bytes: int) -> None:
    if not PERIODIC_DIR.exists():
        return
    backups = sorted(PERIODIC_DIR.glob("couchdb-periodic-*.tar.gz"), key=lambda path: path.stat().st_mtime)
    total_bytes = _total_size(backups)
    while total_bytes > max_bytes and backups:
        oldest = backups.pop(0)
        total_bytes -= oldest.stat().st_size
        oldest.unlink(missing_ok=True)


def ensure_periodic_backup(
    db: Database | None,
    interval_seconds: int = 15 * 60,
    max_total_bytes: int = 100 * 1024 * 1024,
) -> None:
    update_seq = _get_update_seq(db)
    if update_seq is None:
        return
    state = _load_state()
    last_seq = str(state.get("last_seq")) if state.get("last_seq") is not None else None
    last_time = float(state.get("last_time", 0))
    now = datetime.now().timestamp()
    if update_seq == last_seq:
        return
    if now - last_time < interval_seconds:
        return
    created = _create_backup(PERIODIC_DIR, "periodic")
    if not created:
        return
    _prune_periodic(max_total_bytes)
    state["last_seq"] = update_seq
    state["last_time"] = now
    _save_state(state)


def ensure_schema_backup(db: Database | None, version: str) -> None:
    update_seq = _get_update_seq(db)
    if update_seq is None:
        return
    created = _create_backup(SCHEMA_DIR, f"schema-{version}")
    if not created:
        return
    state = _load_state()
    state["last_seq"] = update_seq
    state["last_time"] = datetime.now().timestamp()
    _save_state(state)


def _serialize_docs(db: Database) -> list[dict[str, Any]]:
    rows = db.view("_all_docs", include_docs=True)
    docs: list[dict[str, Any]] = []
    for row in rows:
        doc = row.doc
        if not doc:
            continue
        doc_id = str(doc.get("_id", ""))
        if doc_id.startswith("_design/") or doc_id.startswith("_local/"):
            continue
        docs.append({key: value for key, value in doc.items() if key != "_rev"})
    docs.sort(key=lambda item: str(item.get("_id", "")))
    return docs


def _get_couchdb_size_bytes(info: dict[str, Any] | None) -> int:
    if not info:
        return 0
    sizes = info.get("sizes")
    if isinstance(sizes, dict):
        for key in ("file", "external", "active"):
            try:
                numeric = int(sizes.get(key, 0) or 0)
            except (TypeError, ValueError):
                numeric = 0
            if numeric > 0:
                return numeric
    try:
        return int(info.get("disk_size", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _resolve_asset_path(path: str | Path) -> Path:
    return path if isinstance(path, Path) else Path(path)


def serialize_asset_files(asset_roots: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not asset_roots:
        return []
    assets: list[dict[str, Any]] = []
    for root in asset_roots:
        root_name = str(root.get("name", "")).strip()
        root_path_value = root.get("source")
        if not root_name or not root_path_value:
            continue
        root_path = _resolve_asset_path(root_path_value)
        if not root_path.exists():
            continue
        for file_path in sorted(path for path in root_path.rglob("*") if path.is_file()):
            relative_path = file_path.relative_to(root_path).as_posix()
            assets.append(
                {
                    "root": root_name,
                    "path": relative_path,
                    "size": file_path.stat().st_size,
                    "content_base64": base64.b64encode(file_path.read_bytes()).decode("ascii"),
                }
            )
    return assets


def restore_asset_files(asset_roots: list[dict[str, Any]] | None, assets: list[dict[str, Any]] | None) -> None:
    if not asset_roots:
        return
    targets_by_name: dict[str, list[Path]] = {}
    for root in asset_roots:
        root_name = str(root.get("name", "")).strip()
        destinations = root.get("destinations") or []
        if not root_name:
            continue
        target_paths = [_resolve_asset_path(path) for path in destinations if path]
        if not target_paths:
            continue
        for target_path in target_paths:
            if target_path.exists():
                for child in sorted(target_path.rglob("*"), reverse=True):
                    if child.is_file() or child.is_symlink():
                        child.unlink(missing_ok=True)
                    elif child.is_dir():
                        child.rmdir()
            target_path.mkdir(parents=True, exist_ok=True)
        targets_by_name[root_name] = target_paths

    for asset in assets or []:
        if not isinstance(asset, dict):
            raise ValueError("Backup asset entry is invalid.")
        root_name = str(asset.get("root", "")).strip()
        relative_path = str(asset.get("path", "")).strip()
        encoded = asset.get("content_base64")
        if not root_name or not relative_path or not isinstance(encoded, str):
            raise ValueError("Backup asset entry is incomplete.")
        if root_name not in targets_by_name:
            continue
        relative_file = Path(relative_path)
        if relative_file.is_absolute() or ".." in relative_file.parts:
            raise ValueError(f"Backup asset path {relative_path} is invalid.")
        try:
            content = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Backup asset {root_name}/{relative_path} is not valid base64.") from exc
        for target_root in targets_by_name[root_name]:
            output_path = target_root / relative_file
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(content)


def create_backup_payload(
    db: Database | None,
    *,
    app_metadata: dict[str, Any] | None = None,
    asset_roots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not db:
        raise RuntimeError("CouchDB is not initialized.")
    info = db.info()
    disk_size_bytes = _get_couchdb_size_bytes(info)
    docs = _serialize_docs(db)
    assets = serialize_asset_files(asset_roots)
    return {
        "format": BACKUP_FILE_FORMAT,
        "format_version": BACKUP_FILE_VERSION,
        "created_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "application": app_metadata or {},
        "database": {
            "update_seq": str(info.get("update_seq")) if info.get("update_seq") is not None else None,
            "doc_count": int(info.get("doc_count", 0) or 0),
            "disk_size": disk_size_bytes,
            "data_doc_count": len(docs),
        },
        "docs": docs,
        "assets": assets,
    }


def dump_backup_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


def load_backup_payload(raw_bytes: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Backup file is not valid UTF-8 JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Backup file must contain a JSON object.")
    if payload.get("format") != BACKUP_FILE_FORMAT:
        raise ValueError("Backup file format is not supported.")
    if payload.get("format_version") != BACKUP_FILE_VERSION:
        raise ValueError("Backup file version is not supported.")
    docs = payload.get("docs")
    if not isinstance(docs, list):
        raise ValueError("Backup file is missing document data.")
    for index, doc in enumerate(docs, start=1):
        if not isinstance(doc, dict):
            raise ValueError(f"Backup document #{index} is invalid.")
        doc_id = doc.get("_id")
        if not isinstance(doc_id, str) or not doc_id:
            raise ValueError(f"Backup document #{index} is missing an _id.")
    assets = payload.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("Backup asset data is invalid.")
    for index, asset in enumerate(assets, start=1):
        if not isinstance(asset, dict):
            raise ValueError(f"Backup asset #{index} is invalid.")
        root_name = asset.get("root")
        asset_path = asset.get("path")
        encoded = asset.get("content_base64")
        if not isinstance(root_name, str) or not root_name:
            raise ValueError(f"Backup asset #{index} is missing a root.")
        if not isinstance(asset_path, str) or not asset_path:
            raise ValueError(f"Backup asset #{index} is missing a path.")
        if not isinstance(encoded, str) or not encoded:
            raise ValueError(f"Backup asset #{index} is missing content.")
    return payload
