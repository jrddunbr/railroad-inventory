from __future__ import annotations

from dataclasses import dataclass, field, fields
import time
from typing import Any, Iterable, Type, TypeVar

import couchdb
from couchdb import http
from flask import abort, g, has_request_context

from app.backup import ensure_schema_backup

T = TypeVar("T", bound="BaseModel")


class CouchStore:
    def __init__(self) -> None:
        self.server: couchdb.Server | None = None
        self.db: couchdb.Database | None = None
        self.cache: dict[tuple[type, int], BaseModel] = {}

    def init_app(self, app) -> None:
        url = app.config["COUCHDB_URL"]
        db_name = app.config["COUCHDB_DATABASE"]
        self.server = couchdb.Server(url)
        if db_name in self.server:
            self.db = self.server[db_name]
        else:
            self.db = self.server.create(db_name)
        self.cache = {}
        self.ensure_views()
        self.ensure_counters(app.config["COUCHDB_COUNTERS"])
        self.ensure_totals(app.config.get("COUCHDB_TOTALS", []))
        self.ensure_schema_version(app.config["SCHEMA_VERSION"])

    def ensure_schema_version(self, version: str) -> None:
        if not self.db:
            return
        doc_id = "schema_version"
        try:
            doc = self.db[doc_id]
        except http.ResourceNotFound:
            self.db[doc_id] = {"_id": doc_id, "type": "schema_version", "version": version}
            return
        if doc.get("version") != version:
            doc["version"] = version
            self.db.save(doc)
            ensure_schema_backup(self.db, version)

    def ensure_counters(self, counter_keys: Iterable[str]) -> None:
        if not self.db:
            return
        doc_id = "counters"
        try:
            doc = self.db[doc_id]
        except http.ResourceNotFound:
            doc = {"_id": doc_id, "type": "counters"}
            for key in counter_keys:
                doc[key] = 0
            self.db[doc_id] = doc
            return
        updated = False
        for key in counter_keys:
            if key not in doc:
                doc[key] = 0
                updated = True
        if updated:
            self.db.save(doc)

    def ensure_views(self) -> None:
        if not self.db:
            return
        design_id = "_design/indexes"
        map_source = (
            "function(doc) {"
            " if (doc.type && doc.id !== undefined && doc.id !== null) {"
            " emit([doc.type, doc.id], null);"
            " }"
            "}"
        )
        view_doc = {
            "_id": design_id,
            "views": {
                "by_type_id": {
                    "map": map_source,
                }
            },
        }
        try:
            existing = self.db[design_id]
        except http.ResourceNotFound:
            self.db[design_id] = view_doc
            return
        if existing.get("views", {}).get("by_type_id", {}).get("map") != map_source:
            existing["views"] = view_doc["views"]
            self.db.save(existing)

    def ensure_totals(self, totals: Iterable[dict[str, str]]) -> None:
        if not self.db:
            return
        doc_id = "counters"
        try:
            doc = self.db[doc_id]
        except http.ResourceNotFound:
            doc = {"_id": doc_id, "type": "counters"}
        updated = False
        for entry in totals:
            doc_type = entry.get("doc_type")
            counter_key = entry.get("counter_key")
            if not doc_type or not counter_key:
                continue
            total_key = f"{counter_key}_total"
            if total_key in doc:
                continue
            doc[total_key] = self._count_docs(doc_type)
            updated = True
        if updated:
            self.db.save(doc)

    def next_id(self, counter_key: str) -> int:
        if not self.db:
            raise RuntimeError("CouchDB is not initialized.")
        while True:
            doc = self.db["counters"]
            next_value = int(doc.get(counter_key, 0)) + 1
            doc[counter_key] = next_value
            try:
                self.db.save(doc)
                return next_value
            except http.ResourceConflict:
                continue

    def ensure_counter_at_least(self, counter_key: str, value: int) -> None:
        if not self.db:
            return
        while True:
            doc = self.db["counters"]
            current_value = int(doc.get(counter_key, 0))
            if current_value >= value:
                return
            doc[counter_key] = value
            try:
                self.db.save(doc)
                return
            except http.ResourceConflict:
                continue

    def total_count(self, counter_key: str) -> int | None:
        if not self.db:
            return None
        try:
            doc = self.db["counters"]
        except http.ResourceNotFound:
            return None
        total_key = f"{counter_key}_total"
        if total_key not in doc:
            return None
        try:
            return int(doc.get(total_key, 0))
        except (TypeError, ValueError):
            return None

    def get(self, model_cls: Type[T], item_id: int | None) -> T | None:
        if not self.db or item_id is None:
            return None
        cache_key = (model_cls, item_id)
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        doc_id = f"{model_cls.doc_type}:{item_id}"
        start = time.perf_counter()
        doc = self.db.get(doc_id)
        self._track_db_time(start)
        if not doc:
            return None
        obj = model_cls.from_doc(doc, self)
        self.cache[cache_key] = obj
        return obj

    def all(self, model_cls: Type[T]) -> list[T]:
        if not self.db:
            return []
        prefix = f"{model_cls.doc_type}:"
        start = time.perf_counter()
        rows = self.db.view(
            "_all_docs",
            include_docs=True,
            startkey=prefix,
            endkey=f"{prefix}\ufff0",
        )
        self._track_db_time(start)
        results: list[T] = []
        for row in rows:
            doc = row.doc
            if not doc:
                continue
            obj_id = doc.get("id")
            cache_key = (model_cls, int(obj_id)) if obj_id is not None else None
            if cache_key and cache_key in self.cache:
                results.append(self.cache[cache_key])  # type: ignore[arg-type]
                continue
            obj = model_cls.from_doc(doc, self)
            if cache_key:
                self.cache[cache_key] = obj
            results.append(obj)
        return results

    def page(self, model_cls: Type[T], page: int, per_page: int, reverse: bool = False) -> list[T]:
        if not self.db:
            return []
        if per_page <= 0:
            return []
        page = max(1, page)
        doc_type = model_cls.doc_type
        startkey = [doc_type, {}] if reverse else [doc_type, 0]
        endkey = [doc_type, 0] if reverse else [doc_type, {}]
        rows = self.db.view(
            "_design/indexes/_view/by_type_id",
            include_docs=True,
            startkey=startkey,
            endkey=endkey,
            descending=reverse,
            limit=per_page,
            skip=(page - 1) * per_page,
        )
        results: list[T] = []
        for row in rows:
            doc = row.doc
            if not doc:
                continue
            obj_id = doc.get("id")
            cache_key = (model_cls, int(obj_id)) if obj_id is not None else None
            if cache_key and cache_key in self.cache:
                results.append(self.cache[cache_key])  # type: ignore[arg-type]
                continue
            obj = model_cls.from_doc(doc, self)
            if cache_key:
                self.cache[cache_key] = obj
            results.append(obj)
        return results

    def filter_by(self, model_cls: Type[T], **filters: Any) -> list[T]:
        results = []
        for item in self.all(model_cls):
            match = True
            for key, value in filters.items():
                if getattr(item, key) != value:
                    match = False
                    break
            if match:
                results.append(item)
        return results

    def save(self, obj: T) -> None:
        if not self.db:
            raise RuntimeError("CouchDB is not initialized.")
        if hasattr(obj, "prepare_save"):
            obj.prepare_save()
        is_new = obj._rev is None
        if obj.id is None:
            obj.id = self.next_id(obj.counter_key)
        else:
            self.ensure_counter_at_least(obj.counter_key, obj.id)
        doc = obj.to_doc()
        start = time.perf_counter()
        doc_id, rev = self.db.save(doc)
        self._track_db_time(start)
        obj.id = int(doc_id.split(":")[-1])
        obj._rev = rev
        obj._dirty = False
        obj._store = self
        self.cache[(obj.__class__, obj.id)] = obj
        if is_new:
            self._update_total(obj.counter_key, 1)

    def delete(self, obj: T) -> None:
        if not self.db or obj.id is None:
            return
        doc_id = f"{obj.doc_type}:{obj.id}"
        start = time.perf_counter()
        doc = self.db.get(doc_id)
        self._track_db_time(start)
        if doc:
            start = time.perf_counter()
            self.db.delete(doc)
            self._track_db_time(start)
            self._update_total(obj.counter_key, -1)
        self.cache.pop((obj.__class__, obj.id), None)

    def _count_docs(self, doc_type: str) -> int:
        if not self.db:
            return 0
        prefix = f"{doc_type}:"
        rows = self.db.view(
            "_all_docs",
            include_docs=False,
            startkey=prefix,
            endkey=f"{prefix}\ufff0",
        )
        return sum(1 for _ in rows)

    def _update_total(self, counter_key: str, delta: int) -> None:
        if not self.db:
            return
        total_key = f"{counter_key}_total"
        while True:
            doc = self.db["counters"]
            if total_key not in doc:
                return
            current_value = int(doc.get(total_key, 0))
            next_value = max(0, current_value + delta)
            doc[total_key] = next_value
            try:
                self.db.save(doc)
                return
            except http.ResourceConflict:
                continue

    def _track_db_time(self, start: float) -> None:
        if not has_request_context():
            return
        elapsed = time.perf_counter() - start
        current = getattr(g, "db_time", 0.0)
        setattr(g, "db_time", current + elapsed)

    def dirty_objects(self) -> list[T]:
        return [obj for obj in self.cache.values() if obj._dirty]


class Session:
    def __init__(self, store: CouchStore) -> None:
        self.store = store
        self._pending: list[BaseModel] = []

    def add(self, obj: BaseModel) -> None:
        obj._store = self.store
        obj._dirty = True
        self._pending.append(obj)

    def delete(self, obj: BaseModel) -> None:
        self.store.delete(obj)

    def commit(self) -> None:
        seen = set()
        for obj in self._pending:
            self.store.save(obj)
            seen.add((obj.__class__, obj.id))
        self._pending.clear()
        for obj in self.store.dirty_objects():
            key = (obj.__class__, obj.id)
            if key in seen:
                continue
            self.store.save(obj)

    def flush(self) -> None:
        self.commit()


class Query:
    def __init__(self, model_cls: Type[T], store: CouchStore) -> None:
        self.model_cls = model_cls
        self.store = store
        self._filters: dict[str, Any] = {}
        self._sort_field: str | None = None
        self._sort_reverse = False

    def filter_by(self, **filters: Any) -> "Query":
        self._filters.update(filters)
        return self

    def order_by(self, field: str, reverse: bool = False) -> "Query":
        self._sort_field = field
        self._sort_reverse = reverse
        return self

    def all(self) -> list[T]:
        if self._filters:
            items = self.store.filter_by(self.model_cls, **self._filters)
        else:
            items = self.store.all(self.model_cls)
        if self._sort_field:
            items.sort(
                key=lambda item: (
                    getattr(item, self._sort_field) is None,
                    getattr(item, self._sort_field),
                ),
                reverse=self._sort_reverse,
            )
        return items

    def first(self) -> T | None:
        items = self.all()
        return items[0] if items else None

    def count(self) -> int:
        return len(self.all())

    def total(self) -> int:
        if self._filters or (self._sort_field and self._sort_field != "id"):
            return len(self.all())
        total = self.store.total_count(self.model_cls.counter_key)
        if total is None:
            return len(self.all())
        return total

    def page(self, page: int, per_page: int) -> list[T]:
        if self._filters or (self._sort_field and self._sort_field != "id"):
            items = self.all()
            start_index = max(0, (page - 1) * per_page)
            end_index = start_index + per_page
            return items[start_index:end_index]
        return self.store.page(self.model_cls, page, per_page, reverse=self._sort_reverse)

    def get(self, item_id: int) -> T | None:
        return self.store.get(self.model_cls, item_id)

    def get_or_404(self, item_id: int) -> T:
        item = self.get(item_id)
        if item is None:
            abort(404)
        return item


class QueryDescriptor:
    def __get__(self, obj: Any, owner: Type[T]) -> Query:
        return Query(owner, db.store)


class CouchDB:
    def __init__(self) -> None:
        self.store = CouchStore()
        self.session = Session(self.store)

    def init_app(self, app) -> None:
        self.store.init_app(app)


@dataclass
class BaseModel:
    id: int | None = None
    _rev: str | None = field(default=None, repr=False, compare=False)
    _store: CouchStore | None = field(default=None, repr=False, compare=False)
    _dirty: bool = field(default=False, repr=False, compare=False)
    _tracking: bool = field(default=False, repr=False, compare=False)
    doc_type = ""
    counter_key = ""

    def __setattr__(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)
        if name.startswith("_"):
            return
        if getattr(self, "_tracking", False):
            object.__setattr__(self, "_dirty", True)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_tracking", True)
        object.__setattr__(self, "_dirty", False)

    @property
    def doc_id(self) -> str:
        return f"{self.doc_type}:{self.id}"

    def to_doc(self) -> dict[str, Any]:
        doc = {"_id": self.doc_id, "type": self.doc_type}
        for field_def in fields(self):
            if field_def.name.startswith("_"):
                continue
            doc[field_def.name] = getattr(self, field_def.name)
        if self._rev:
            doc["_rev"] = self._rev
        return doc

    @classmethod
    def from_doc(cls: Type[T], doc: dict[str, Any], store: CouchStore) -> T:
        data = {}
        for field_def in fields(cls):
            if field_def.name.startswith("_"):
                continue
            data[field_def.name] = doc.get(field_def.name)
        obj = cls(**data)
        object.__setattr__(obj, "_rev", doc.get("_rev"))
        object.__setattr__(obj, "_store", store)
        object.__setattr__(obj, "_dirty", False)
        object.__setattr__(obj, "_tracking", True)
        return obj

    def prepare_save(self) -> None:
        return


db = CouchDB()
