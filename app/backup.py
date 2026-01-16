from __future__ import annotations

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
