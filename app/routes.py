from __future__ import annotations

import csv
import base64
import hashlib
import io
import json
import math
import os
import platform
import re
import resource
import secrets
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Optional
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    g,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from PIL import Image, ImageDraw, ImageFont
from werkzeug.utils import secure_filename
try:
    import qrcode
except ModuleNotFoundError:
    qrcode = None

from app import db
from app.auth import (
    base64url_to_bytes,
    DEFAULT_PERMISSION_TEXT,
    build_totp_uri,
    bytes_to_base64url,
    decrypt_value,
    encrypt_value,
    format_permissions,
    generate_totp_secret,
    get_valid_totp_counter,
    get_csrf_token,
    hash_password,
    is_safe_next_url,
    normalize_scope,
    normalize_username,
    parse_permissions,
    permission_allows,
    verify_totp_code,
    utcnow_iso,
    validate_csrf,
    validate_password_strength,
    verify_password,
)
from app.backup import (
    BACKUP_FILE_FORMAT,
    BACKUP_FILE_VERSION,
    create_backup_payload,
    dump_backup_payload,
    ensure_periodic_backup,
    load_backup_payload,
    restore_asset_files,
)
from app.models import (
    AppSettings,
    Car,
    CarInspection,
    CarClass,
    Consist,
    InspectionType,
    Location,
    LoadPlacement,
    LoadType,
    Railroad,
    RailroadColorScheme,
    RailroadLogo,
    RailroadSlogan,
    ToolItem,
    PartItem,
    User,
)
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)


main_bp = Blueprint("main", __name__)

PASSWORD_AUTH_THROTTLE = defaultdict(deque)
PASSWORD_AUTH_THROTTLE_LOCK = threading.Lock()

PAGINATION_OPTIONS = ["25", "50", "100", "250", "all"]
DEFAULT_PAGE_SIZE = "50"
DEFAULT_SCALE_OPTIONS = [
    "G|1:22.5",
    "F|1:20.3",
    "O|1:48",
    "S|1:64",
    "HO|1:87",
    "TT|1:120",
    "N|1:160",
    "Z|1:220",
    "1:6",
    "1:8",
    "1:12",
    "1:16",
    "1:24",
    "1:29",
    "1:32",
]
DEFAULT_GAUGE_OPTIONS = [
    "16.5 mm|HO, HOn3, HOn30",
    "12 mm|TT",
    "9 mm|N, Nn3",
    "6.5 mm|Z",
    "14.2 mm|OO",
    "18.2 mm|S",
    "21 mm|Sn3",
    "32 mm|O, On30, On3",
    "45 mm|G, 1:20.3, 1:22.5, 1:29",
    "64 mm|1",
    "89 mm|2",
    "127 mm|3",
    "3.5 in|1 in scale",
    "4.75 in|1 in scale",
    "5 in|1 in scale",
    "7.25 in|1.5 in scale",
    "7.5 in|1.5 in scale",
    "10.25 in|2 in scale",
    "12 in|3 in scale",
    "15 in|3.5 in scale",
]
NMRA_WEIGHT_CHECK_NAME = "NMRA Weight Check"
NMRA_WEIGHT_CHECK_LOADED_NAME = "NMRA Weight Check (Loaded)"
NMRA_WEIGHT_BY_SCALE = {
    "O": (5.0, 1.0),
    "On3": (1.5, 0.75),
    "S": (2.0, 0.5),
    "Sn3": (1.0, 0.5),
    "HO": (1.0, 0.5),
    "HOn3": (0.75, 0.375),
    "TT": (0.75, 0.375),
    "N": (0.5, 0.15),
}
WEIGHT_UNIT_TO_OZ = {
    "oz": 1.0,
    "lb": 16.0,
    "g": 0.03527396195,
    "kg": 35.27396195,
}
WEIGHT_UNIT_TO_KG = {
    "kg": 1.0,
    "g": 0.001,
    "lb": 0.45359237,
    "oz": 0.028349523125,
}
LENGTH_UNIT_TO_IN = {
    "in": 1.0,
    "ft": 12.0,
    "mm": 0.03937007874,
    "cm": 0.3937007874,
    "m": 39.37007874,
}
LENGTH_UNIT_TO_M = {
    "m": 1.0,
    "cm": 0.01,
    "mm": 0.001,
    "ft": 0.3048,
    "in": 0.0254,
}
DEFAULT_LENGTH_UNIT = "mm"
DEFAULT_WEIGHT_UNIT = "g"
WEIGHT_UNITS = ["g", "kg", "lb", "oz"]
LENGTH_UNITS = ["mm", "cm", "m", "in", "ft"]
ADMIN_COUNTER_LABELS = {
    "railroads": "Railroads",
    "car_classes": "Car Classes",
    "locations": "Locations",
    "cars": "Cars",
    "consists": "Consists",
    "loads": "Loads",
    "load_placements": "Load Placements",
    "car_inspections": "Car Inspections",
    "inspection_types": "Inspection Types",
    "railroad_color_schemes": "Railroad Color Schemes",
    "railroad_logos": "Railroad Logos",
    "railroad_slogans": "Railroad Slogans",
    "app_settings": "App Settings",
    "tool_items": "Tool Items",
    "part_items": "Part Items",
    "users": "Users",
}
PUBLIC_ENDPOINTS = {
    "main.login",
    "main.setup_users",
    "main.logout",
    "main.login_verify",
    "main.login_passkey_options",
    "main.login_passkey_verify",
    "static",
}
MFA_LOGIN_ENDPOINTS = {
    "main.login_verify",
    "main.login_passkey_verify",
}
SELF_SERVICE_ENDPOINTS = {
    "main.account_security",
    "main.account_password",
    "main.account_totp_setup",
    "main.account_totp_start",
    "main.account_totp_enable",
    "main.account_totp_disable",
    "main.account_passkey_options",
    "main.account_passkey_verify",
    "main.account_passkey_delete",
}
RECENT_AUTH_ENDPOINTS = {
    "main.account_totp_start",
    "main.account_totp_setup",
    "main.account_totp_enable",
    "main.account_totp_disable",
    "main.account_passkey_options",
    "main.account_passkey_verify",
    "main.account_passkey_delete",
}
SCOPE_PREFIXES = [
    ("/administration/users", "admin/users"),
    ("/administration", "admin"),
    ("/settings", "admin/settings"),
    ("/reports", "reports"),
    ("/search", "search"),
    ("/railroads", "railroads"),
    ("/car-classes", "inventory/car-classes"),
    ("/locomotive-classes", "inventory/locomotive-classes"),
    ("/cars", "inventory/cars"),
    ("/consists", "inventory/consists"),
    ("/loads", "inventory/loads"),
    ("/load-placements", "inventory/loads/placements"),
    ("/locations", "inventory/locations"),
    ("/tool-inventory", "inventory/tools"),
    ("/parts-inventory", "inventory/parts"),
    ("/inventory2", "inventory"),
    ("/inventory", "inventory"),
    ("/api/cars", "inventory/cars"),
    ("/api/car-classes", "inventory/car-classes"),
    ("/api/railroads", "railroads"),
    ("/api/search", "search/api"),
    ("/tools/consist-creation", "tools/consist-creation"),
    ("/tools/aar-plate-viewer", "tools/aar-plate-viewer"),
    ("/tools/prr-home-shop-repair", "tools/prr-home-shop-repair"),
    ("/tools", "tools"),
]
WRITE_ONLY_GET_PATTERNS = (
    re.compile(r"^/cars/\d+/(edit|inspect)$"),
    re.compile(r"^/locations/\d+/(edit|inspect)$"),
    re.compile(r"^/consists/\d+/edit$"),
    re.compile(r"^/loads/\d+/edit$"),
    re.compile(r"^/load-placements/\d+/edit$"),
    re.compile(r"^/tool-inventory/\d+/edit$"),
    re.compile(r"^/parts-inventory/\d+/edit$"),
    re.compile(r"^/railroads/\d+/edit$"),
    re.compile(r"^/car-classes/\d+/edit$"),
    re.compile(r"^/settings/inspection-types/\d+/edit$"),
    re.compile(r"^/settings/inspection-types/new$"),
    re.compile(r"^/locations/\d+/flat-pack$"),
    re.compile(r"^/cars/new$"),
    re.compile(r"^/locations/new$"),
    re.compile(r"^/consists/new$"),
    re.compile(r"^/loads/new$"),
    re.compile(r"^/load-placements/new$"),
    re.compile(r"^/tool-inventory/new$"),
    re.compile(r"^/parts-inventory/new$"),
    re.compile(r"^/administration/users$"),
    re.compile(r"^/settings$"),
)


def ensure_db_backup() -> None:
    ensure_periodic_backup(db.store.db)


def utcnow() -> datetime:
    return datetime.now(UTC)


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def clear_session_and_auth_state() -> None:
    session.clear()


def get_timedelta_config(name: str) -> timedelta:
    value = current_app.config.get(name)
    if isinstance(value, timedelta):
        return value
    return timedelta()


def get_failure_limit() -> int:
    try:
        return max(1, int(current_app.config.get("AUTH_FAILURE_LIMIT", 3)))
    except (TypeError, ValueError):
        return 3


def get_password_throttle_limit() -> int:
    try:
        return max(1, int(current_app.config.get("AUTH_PASSWORD_THROTTLE_LIMIT", 5)))
    except (TypeError, ValueError):
        return 5


def get_password_throttle_max_delay_seconds() -> int:
    try:
        return max(1, int(current_app.config.get("AUTH_PASSWORD_THROTTLE_MAX_DELAY_SECONDS", 30)))
    except (TypeError, ValueError):
        return 30


def get_client_address() -> str:
    return (request.remote_addr or "unknown").strip() or "unknown"


def get_password_throttle_key(username: str | None) -> str:
    normalized_username = normalize_username(username)
    return f"{get_client_address()}:{normalized_username}"


def password_auth_is_throttled(username: str | None) -> tuple[bool, int]:
    key = get_password_throttle_key(username)
    window = get_timedelta_config("AUTH_PASSWORD_THROTTLE_WINDOW")
    now_value = utcnow()
    with PASSWORD_AUTH_THROTTLE_LOCK:
        attempts = PASSWORD_AUTH_THROTTLE.get(key)
        if not attempts:
            return False, 0
        while attempts and now_value - attempts[0] > window:
            attempts.popleft()
        if not attempts:
            PASSWORD_AUTH_THROTTLE.pop(key, None)
            return False, 0
        limit = get_password_throttle_limit()
        if len(attempts) < limit:
            return False, 0
        over_limit = len(attempts) - limit
        delay_seconds = min(get_password_throttle_max_delay_seconds(), 2 ** max(over_limit, 0))
        retry_after = max(1, delay_seconds - int((now_value - attempts[-1]).total_seconds()))
        return retry_after > 0, retry_after


def record_failed_password_attempt(username: str | None) -> None:
    key = get_password_throttle_key(username)
    window = get_timedelta_config("AUTH_PASSWORD_THROTTLE_WINDOW")
    now_value = utcnow()
    with PASSWORD_AUTH_THROTTLE_LOCK:
        attempts = PASSWORD_AUTH_THROTTLE[key]
        attempts.append(now_value)
        while attempts and now_value - attempts[0] > window:
            attempts.popleft()


def clear_failed_password_throttle(username: str | None) -> None:
    key = get_password_throttle_key(username)
    with PASSWORD_AUTH_THROTTLE_LOCK:
        PASSWORD_AUTH_THROTTLE.pop(key, None)


def is_recent_auth(max_age: timedelta | None = None) -> bool:
    authenticated_at = parse_iso_datetime(session.get("recent_auth_at"))
    if not authenticated_at:
        return False
    allowed_age = max_age or get_timedelta_config("AUTH_RECENT_AUTH_LIFETIME")
    return utcnow() - authenticated_at <= allowed_age


def mark_recent_auth() -> None:
    session["recent_auth_at"] = utcnow_iso()


def establish_authenticated_session(user: User, next_url: str | None = None, update_last_login: bool = True) -> None:
    session.clear()
    now_value = utcnow_iso()
    session["user_id"] = user.id
    session["session_version"] = int(user.session_version or 1)
    session["_csrf_token"] = secrets.token_urlsafe(32)
    session["authenticated_at"] = now_value
    session["recent_auth_at"] = now_value
    session["last_seen_at"] = now_value
    session.permanent = True
    if isinstance(next_url, str) and is_safe_next_url(next_url):
        session["login_next"] = next_url
    if update_last_login:
        user.last_login_at = now_value
        db.session.commit()


def start_pending_login(user: User) -> None:
    existing_csrf_token = session.get("_csrf_token")
    session.clear()
    session["pending_user_id"] = user.id
    session["pending_user_version"] = int(user.session_version or 1)
    session["pending_started_at"] = utcnow_iso()
    session["login_next"] = get_post_login_redirect()
    session["_csrf_token"] = existing_csrf_token or secrets.token_urlsafe(32)
    session["last_seen_at"] = utcnow_iso()
    session.permanent = True


def complete_login(user: User) -> None:
    next_url = session.get("login_next")
    establish_authenticated_session(user, next_url=next_url, update_last_login=True)


def unlock_user_if_lock_expired(user: User | None) -> None:
    if not user or not user.locked_until:
        return
    locked_until = parse_iso_datetime(user.locked_until)
    if locked_until and locked_until > utcnow():
        return
    user.locked_until = None
    user.failed_password_attempts = 0
    user.failed_mfa_attempts = 0
    db.session.commit()


def is_user_locked(user: User | None) -> bool:
    if not user:
        return False
    unlock_user_if_lock_expired(user)
    locked_until = parse_iso_datetime(user.locked_until)
    return bool(locked_until and locked_until > utcnow())


def register_failed_auth_attempt(user: User | None, attempt_type: str) -> bool:
    if not user:
        return False
    unlock_user_if_lock_expired(user)
    if attempt_type == "password":
        user.failed_password_attempts = int(user.failed_password_attempts or 0) + 1
        current_count = user.failed_password_attempts
    else:
        user.failed_mfa_attempts = int(user.failed_mfa_attempts or 0) + 1
        current_count = user.failed_mfa_attempts
    locked = current_count >= get_failure_limit()
    if locked:
        user.locked_until = (utcnow() + get_timedelta_config("AUTH_LOCKOUT_LIFETIME")).replace(microsecond=0).isoformat()
        user.failed_password_attempts = 0
        user.failed_mfa_attempts = 0
    db.session.commit()
    return locked


def clear_failed_auth_attempts(user: User | None, attempt_type: str | None = None) -> None:
    if not user:
        return
    changed = False
    if attempt_type in {None, "password"} and user.failed_password_attempts:
        user.failed_password_attempts = 0
        changed = True
    if attempt_type in {None, "mfa"} and user.failed_mfa_attempts:
        user.failed_mfa_attempts = 0
        changed = True
    if user.locked_until:
        user.locked_until = None
        changed = True
    if changed:
        db.session.commit()


def bump_session_version(user: User | None) -> None:
    if not user:
        return
    user.session_version = int(user.session_version or 1) + 1


def revoke_user_mfa(user: User | None) -> None:
    if not user:
        return
    user.totp_secret_encrypted = None
    user.totp_enabled = False
    user.last_totp_counter = None
    user.passkeys = []


def pending_login_expired() -> bool:
    started_at = parse_iso_datetime(session.get("pending_started_at"))
    if not started_at:
        return True
    return utcnow() - started_at > get_timedelta_config("AUTH_PENDING_LIFETIME")


def current_session_expired() -> bool:
    authenticated_at = parse_iso_datetime(session.get("authenticated_at"))
    last_seen_at = parse_iso_datetime(session.get("last_seen_at"))
    if not authenticated_at or not last_seen_at:
        return True
    now_value = utcnow()
    if now_value - authenticated_at > get_timedelta_config("AUTH_SESSION_OVERALL_LIFETIME"):
        return True
    if now_value - last_seen_at > get_timedelta_config("PERMANENT_SESSION_LIFETIME"):
        return True
    return False


def user_count() -> int:
    return User.query.count()


def users_exist() -> bool:
    return user_count() > 0


def get_current_user() -> User | None:
    user_id = session.get("user_id")
    if not isinstance(user_id, int):
        return None
    if current_session_expired():
        clear_session_and_auth_state()
        return None
    user = User.query.get(user_id)
    if not user or not user.is_active:
        clear_session_and_auth_state()
        return None
    if int(session.get("session_version") or 0) != int(user.session_version or 1):
        clear_session_and_auth_state()
        return None
    return user


def get_pending_user() -> User | None:
    user_id = session.get("pending_user_id")
    if not isinstance(user_id, int):
        return None
    if pending_login_expired():
        clear_session_and_auth_state()
        return None
    user = User.query.get(user_id)
    if not user or not user.is_active:
        clear_session_and_auth_state()
        return None
    if int(session.get("pending_user_version") or 0) != int(user.session_version or 1):
        clear_session_and_auth_state()
        return None
    return user


def get_user_label(user: User | None) -> str:
    if not user:
        return "-"
    return user.display_name or user.username or f"User {user.id}"


def get_user_first_name(user: User | None) -> str:
    label = get_user_label(user).strip()
    if not label:
        return "-"
    return label.split()[0]


def get_user_totp_secret(user: User | None) -> str | None:
    if not user:
        return None
    return decrypt_value(user.totp_secret_encrypted)


def normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def get_gravatar_url(user: User | None, size: int = 64) -> str | None:
    email = normalize_email(user.email if user else "")
    if not email:
        return None
    digest = hashlib.md5(email.encode("utf-8")).hexdigest()
    return f"https://www.gravatar.com/avatar/{digest}?s={size}&d=identicon"


def user_has_verified_mfa(user: User | None) -> bool:
    return bool(user and (user.totp_enabled or user.has_passkeys))


def derive_scope(path: str) -> str:
    normalized = path.rstrip("/") or "/"
    if normalized == "/":
        return "inventory"
    if normalized.startswith("/api/cars/"):
        return "inventory/cars"
    for prefix, scope in SCOPE_PREFIXES:
        if normalized == prefix or normalized.startswith(f"{prefix}/"):
            return normalize_scope(scope)
    return "inventory"


def required_access_for_request() -> str:
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        return "WRITE"
    for pattern in WRITE_ONLY_GET_PATTERNS:
        if pattern.match(request.path):
            return "WRITE"
    return "READ"


def user_has_access(user: User | None, scope: str, access: str = "READ") -> bool:
    if not user:
        return False
    requested_access = access
    if requested_access == "WRITE" and not user_has_verified_mfa(user):
        return False
    return permission_allows(user.permissions, scope, requested_access)


def current_user_has_access(scope: str, access: str = "READ") -> bool:
    return user_has_access(getattr(g, "current_user", None), scope, access)


def deny_request(message: str, status_code: int):
    if request.path.startswith("/api/"):
        return jsonify({"error": message}), status_code
    if status_code == 401:
        flash(message, "danger")
        return redirect(url_for("main.login", next=request.full_path if request.query_string else request.path))
    return render_template("error.html", title="Access Denied", message=message), status_code


def backfill_ownerless_documents(owner: User) -> int:
    updated = 0
    owned_models = [
        Railroad,
        CarClass,
        Location,
        ToolItem,
        PartItem,
        Car,
        Consist,
        CarInspection,
        RailroadColorScheme,
        RailroadLogo,
        RailroadSlogan,
        LoadType,
        LoadPlacement,
    ]
    for model_cls in owned_models:
        for item in model_cls.query.all():
            if item.owner_user_id:
                continue
            item.owner_user_id = owner.id
            updated += 1
    if updated:
        db.session.commit()
    return updated


def build_user_row(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "label": get_user_label(user),
        "permissions": user.permissions or [],
        "permission_text": format_permissions(user.permissions),
        "is_active": user.is_active,
        "last_login_at": user.last_login_at,
        "totp_enabled": user.totp_enabled,
        "passkey_count": len(user.passkeys or []),
        "has_mfa": user_has_verified_mfa(user),
        "gravatar_url": get_gravatar_url(user, size=40),
    }


def build_user_form_defaults() -> dict[str, str]:
    return {
        "username": "",
        "display_name": "",
        "email": "",
        "permissions": DEFAULT_PERMISSION_TEXT,
    }


def get_permission_examples() -> list[dict[str, str]]:
    return [
        {"permission": "inventory/* READ", "description": "Read all inventory records."},
        {"permission": "inventory/cars WRITE", "description": "Create and edit cars."},
        {"permission": "inventory/cars/c38 WRITE", "description": "Specific leaf scope example."},
        {"permission": "railroads/* WRITE", "description": "Manage railroad records and related details."},
        {"permission": "admin/users WRITE", "description": "Create users, reset passwords, and edit permissions."},
        {"permission": "* WRITE", "description": "Full platform administration."},
    ]


def get_sorted_users() -> list[User]:
    return User.query.order_by("username").all()


def get_admin_user_rows() -> list[dict[str, object]]:
    return [build_user_row(user) for user in get_sorted_users()]


def get_enabled_users() -> list[User]:
    return [user for user in get_sorted_users() if user.is_active]


def resolve_owner_from_form(value: str | None):
    username = normalize_username(value)
    if not username:
        return None
    return User.query.filter_by(username=username, is_active=True).first()


def apply_owner_form(model_obj, form) -> str | None:
    owner = resolve_owner_from_form(form.get("owner_username"))
    if form.get("owner_username", "").strip() and owner is None:
        return "Owner must be an enabled user."
    model_obj.owner_user_id = owner.id if owner else None
    return None


def create_user_from_form(force_superuser: bool = False) -> User:
    username = normalize_username(request.form.get("username"))
    display_name = request.form.get("display_name", "").strip()
    email = normalize_email(request.form.get("email"))
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")
    if not username:
        raise ValueError("Username is required.")
    if User.query.filter_by(username=username).first():
        raise ValueError("That username already exists.")
    if password != confirm_password:
        raise ValueError("Passwords do not match.")
    validate_password_strength(password)
    permissions = [{"scope": "*", "access": "WRITE"}] if force_superuser else parse_permissions(
        request.form.get("permissions")
    )
    user = User(
        username=username,
        display_name=display_name or username,
        email=email or None,
        password_hash=hash_password(password),
        permissions=permissions,
        is_active=True,
    )
    db.session.add(user)
    db.session.commit()
    return user


def get_post_login_redirect() -> str:
    next_url = request.args.get("next", "").strip() or request.form.get("next", "").strip()
    if is_safe_next_url(next_url):
        return next_url
    return url_for("main.inventory")


def get_post_login_redirect_from_json(payload: dict[str, object] | None = None) -> str:
    if isinstance(payload, dict):
        next_url = str(payload.get("next", "") or "").strip()
        if is_safe_next_url(next_url):
            return next_url
    return get_post_login_redirect()


def consume_login_redirect() -> str:
    next_url = session.pop("login_next", None)
    if isinstance(next_url, str) and is_safe_next_url(next_url):
        return next_url
    return url_for("main.inventory")


def get_webauthn_rp_id() -> str:
    configured = (current_app.config.get("WEBAUTHN_RP_ID") or "").strip()
    if configured:
        return configured
    host = request.host.split(":", 1)[0].strip().lower()
    return host or "localhost"


def get_webauthn_origin() -> str:
    configured = (current_app.config.get("WEBAUTHN_ORIGIN") or "").strip()
    if configured:
        return configured.rstrip("/")
    return request.host_url.rstrip("/")


def get_webauthn_rp_name() -> str:
    return str(current_app.config.get("WEBAUTHN_RP_NAME") or "Railroad Inventory")


def get_login_passkey_user() -> User | None:
    pending_user = get_pending_user()
    if pending_user:
        return pending_user
    username = normalize_username(request.form.get("username") or (request.get_json(silent=True) or {}).get("username"))
    if not username:
        return None
    return User.query.filter_by(username=username).first()


def get_passkey_descriptors(user: User) -> list[PublicKeyCredentialDescriptor]:
    descriptors: list[PublicKeyCredentialDescriptor] = []
    for item in user.passkeys or []:
        credential_id = item.get("credential_id")
        if not isinstance(credential_id, str) or not credential_id:
            continue
        descriptors.append(PublicKeyCredentialDescriptor(id=base64url_to_bytes(credential_id)))
    return descriptors


def build_qr_data_uri(value: str) -> str:
    if qrcode is None:
        raise ModuleNotFoundError("No module named 'qrcode'")
    image = qrcode.make(value)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def find_passkey_record(user: User, credential_id: str | None) -> dict[str, object] | None:
    if not credential_id:
        return None
    for item in user.passkeys or []:
        if item.get("credential_id") == credential_id:
            return item
    return None


@main_bp.before_app_request
def load_request_security_context():
    g.current_user = get_current_user()
    g.pending_user = get_pending_user()
    g.current_scope = derive_scope(request.path)
    if request.endpoint == "static":
        return None

    get_csrf_token()
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        validate_csrf()

    if not users_exist():
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        return redirect(url_for("main.setup_users"))

    if request.endpoint in {"main.setup_users"}:
        if g.current_user and current_user_has_access("admin/users", "WRITE"):
            return redirect(url_for("main.administration_users"))
        return redirect(url_for("main.login"))

    if request.endpoint in MFA_LOGIN_ENDPOINTS:
        if g.pending_user:
            return None
        if g.current_user:
            return redirect(url_for("main.inventory"))
        return redirect(url_for("main.login"))

    if request.endpoint in {"main.login"} and g.current_user:
        return redirect(url_for("main.inventory"))

    if request.endpoint in PUBLIC_ENDPOINTS:
        return None

    if not g.current_user:
        return deny_request("Sign in to continue.", 401)

    if request.endpoint in SELF_SERVICE_ENDPOINTS:
        if request.endpoint in RECENT_AUTH_ENDPOINTS and not is_recent_auth():
            clear_session_and_auth_state()
            flash("Sign in again to manage MFA settings.", "danger")
            return redirect(url_for("main.login", next=request.full_path if request.query_string else request.path))
        session["last_seen_at"] = utcnow_iso()
        session.permanent = True
        return None

    if g.current_user or g.pending_user:
        session["last_seen_at"] = utcnow_iso()
    session.permanent = True
    required_access = required_access_for_request()
    if not user_has_access(g.current_user, g.current_scope, required_access):
        return deny_request(
            f"{required_access} access to {g.current_scope} is required for this action.",
            403,
        )
    return None


@main_bp.app_context_processor
def inject_auth_helpers() -> dict[str, object]:
    return {
        "current_user": getattr(g, "current_user", None),
        "pending_user": getattr(g, "pending_user", None),
        "csrf_token": get_csrf_token(),
        "can_access": current_user_has_access,
        "user_label": get_user_label,
        "user_first_name": get_user_first_name,
        "user_has_verified_mfa": user_has_verified_mfa,
        "gravatar_url": get_gravatar_url,
    }


def get_repo_root() -> str:
    return os.path.abspath(os.path.join(current_app.root_path, os.pardir))


def get_git_commit_hash() -> str | None:
    env_commit = (os.environ.get("APP_GIT_COMMIT") or "").strip()
    if env_commit:
        return env_commit
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=get_repo_root(),
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            or None
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def get_couchdb_endpoint_label() -> str:
    raw_url = current_app.config.get("COUCHDB_URL", "")
    parsed = urlsplit(raw_url)
    if not parsed.scheme or not parsed.hostname:
        return raw_url or "-"
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return f"{parsed.scheme}://{host}"


def get_backup_application_metadata() -> dict[str, str | None]:
    return {
        "app_name": "Railroad Inventory",
        "schema_version": current_app.config.get("SCHEMA_VERSION"),
        "database_name": current_app.config.get("COUCHDB_DATABASE"),
        "git_commit": get_git_commit_hash(),
        "backup_scope": "CouchDB documents and managed assets",
    }


def format_bytes(value: int | float | None) -> str:
    if value is None:
        return "-"
    size = float(value)
    units = ["bytes", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "bytes":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{int(value)} bytes"


def format_duration_seconds(value: float | int | None) -> str:
    if value is None:
        return "-"
    total_seconds = max(0, int(value))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def count_files_in_directory(path: str) -> int:
    if not path or not os.path.exists(path):
        return 0
    total = 0
    for _, _, files in os.walk(path):
        total += len(files)
    return total


def get_couchdb_size_bytes(info: dict[str, object] | None) -> int:
    if not info:
        return 0
    sizes = info.get("sizes")
    if isinstance(sizes, dict):
        for key in ("file", "external", "active"):
            value = sizes.get(key)
            try:
                numeric = int(value or 0)
            except (TypeError, ValueError):
                numeric = 0
            if numeric > 0:
                return numeric
    try:
        return int(info.get("disk_size", 0) or 0)
    except (TypeError, ValueError):
        return 0


def get_couchdb_health() -> dict[str, object]:
    started = time.perf_counter()
    try:
        database = db.store.db
        if not database:
            raise RuntimeError("CouchDB database handle is not initialized.")
        database.info()
        elapsed_ms = (time.perf_counter() - started) * 1000
        return {
            "reachable": True,
            "status": "connected",
            "response_time_ms": elapsed_ms,
        }
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return {
            "reachable": False,
            "status": "error",
            "response_time_ms": elapsed_ms,
            "error": str(exc),
        }


def get_flask_health() -> dict[str, object]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    memory_kb = usage.ru_maxrss
    if sys.platform == "darwin":
        memory_bytes = int(memory_kb)
    else:
        memory_bytes = int(memory_kb * 1024)
    started_at = float(current_app.config.get("APP_STARTED_AT", time.time()) or time.time())
    uptime_seconds = max(0.0, time.time() - started_at)
    return {
        "pid": os.getpid(),
        "uptime_seconds": uptime_seconds,
        "uptime_label": format_duration_seconds(uptime_seconds),
        "memory_bytes": memory_bytes,
        "memory_label": format_bytes(memory_bytes),
        "cpu_user_seconds": float(usage.ru_utime),
        "cpu_system_seconds": float(usage.ru_stime),
    }


def get_managed_asset_roots() -> list[dict[str, object]]:
    static_folder = current_app.static_folder or os.path.join(current_app.root_path, "static")
    repo_root = get_repo_root()
    upload_dir = current_app.config.get("LOGO_UPLOAD_FOLDER") or os.path.join(static_folder, "uploads", "railroad-logos")
    local_upload_root = os.path.dirname(upload_dir)
    data_upload_root = os.path.join(repo_root, "data", "uploads")
    generated_root = os.path.join(static_folder, "tools", "generated")

    asset_roots: list[dict[str, object]] = []
    upload_sources = [path for path in [data_upload_root, local_upload_root] if os.path.isdir(path)]
    upload_source = (
        max(upload_sources, key=count_files_in_directory)
        if upload_sources
        else local_upload_root
    )
    asset_roots.append(
        {
            "name": "uploads",
            "label": "Uploaded Assets",
            "source": upload_source,
            "destinations": [local_upload_root, data_upload_root],
        }
    )
    asset_roots.append(
        {
            "name": "generated_tools",
            "label": "Generated Tool Assets",
            "source": generated_root,
            "destinations": [generated_root],
        }
    )
    return asset_roots


def get_admin_database_counts() -> list[dict[str, int | str | None]]:
    counts: list[dict[str, int | str | None]] = []
    for entry in current_app.config.get("COUCHDB_TOTALS", []):
        counter_key = entry.get("counter_key")
        doc_type = entry.get("doc_type")
        if not counter_key or not doc_type:
            continue
        counts.append(
            {
                "label": ADMIN_COUNTER_LABELS.get(counter_key, counter_key.replace("_", " ").title()),
                "doc_type": doc_type,
                "counter_key": counter_key,
                "count": db.store.total_count(counter_key),
            }
        )
    return counts


def get_administration_summary() -> dict[str, object]:
    database = db.store.db
    db_info = database.info() if database else {}
    disk_size_bytes = get_couchdb_size_bytes(db_info)
    git_commit = get_git_commit_hash()
    asset_roots = get_managed_asset_roots()
    couchdb_health = get_couchdb_health()
    flask_health = get_flask_health()
    return {
        "application": {
            "name": "Railroad Inventory",
            "schema_version": current_app.config.get("SCHEMA_VERSION"),
            "backup_format": BACKUP_FILE_FORMAT,
            "backup_format_version": BACKUP_FILE_VERSION,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
        },
        "source_control": {
            "git_commit": git_commit,
            "git_commit_short": git_commit[:12] if git_commit else None,
        },
        "database": {
            "name": current_app.config.get("COUCHDB_DATABASE"),
            "endpoint": get_couchdb_endpoint_label(),
            "doc_count": int(db_info.get("doc_count", 0) or 0),
            "disk_size_bytes": disk_size_bytes,
            "disk_size_label": format_bytes(disk_size_bytes),
        },
        "health": {
            "couchdb": couchdb_health,
            "flask": flask_health,
        },
        "stats": get_admin_database_counts(),
        "user_count": user_count(),
        "assets": [
            {
                "label": str(asset_root["label"]),
                "name": str(asset_root["name"]),
                "source": str(asset_root["source"]),
                "file_count": count_files_in_directory(str(asset_root["source"])),
            }
            for asset_root in asset_roots
        ],
        "restore": {
            "max_upload_bytes": int(current_app.config.get("MAX_CONTENT_LENGTH", 0) or 0),
        },
    }


def get_app_settings() -> AppSettings:
    settings = AppSettings.query.get(1)
    if settings:
        return settings
    settings = AppSettings(
        id=1,
        page_size=DEFAULT_PAGE_SIZE,
        default_length_unit=DEFAULT_LENGTH_UNIT,
        default_weight_unit=DEFAULT_WEIGHT_UNIT,
    )
    db.session.add(settings)
    db.session.commit()
    ensure_db_backup()
    return settings


def get_page_size() -> str:
    settings = get_app_settings()
    size = settings.page_size or DEFAULT_PAGE_SIZE
    if size not in PAGINATION_OPTIONS:
        return DEFAULT_PAGE_SIZE
    return size


def get_default_length_unit() -> str:
    settings = get_app_settings()
    unit = (settings.default_length_unit or DEFAULT_LENGTH_UNIT).lower()
    if unit not in LENGTH_UNIT_TO_IN:
        return DEFAULT_LENGTH_UNIT
    return unit


def get_default_weight_unit() -> str:
    settings = get_app_settings()
    unit = (settings.default_weight_unit or DEFAULT_WEIGHT_UNIT).lower()
    if unit not in WEIGHT_UNIT_TO_OZ:
        return DEFAULT_WEIGHT_UNIT
    return unit


def get_page_number() -> int:
    page_value = request.args.get("page", "").strip()
    if page_value.isdigit():
        return max(1, int(page_value))
    return 1


def normalize_page_size(value: str) -> str:
    if not value:
        return ""
    if value in PAGINATION_OPTIONS:
        return value
    return ""


def parse_scale_line(line: str) -> tuple[str | None, str | None]:
    raw = line.strip()
    if "|" in raw:
        name, ratio = [part.strip() for part in raw.split("|", 1)]
        return name or None, ratio or None
    if "=" in raw:
        name, ratio = [part.strip() for part in raw.split("=", 1)]
        return name or None, ratio or None
    if raw.endswith(")") and "(" in raw:
        prefix, suffix = raw.rsplit("(", 1)
        ratio = suffix[:-1].strip()
        name = prefix.strip()
        if ratio.startswith("1:") and name:
            return name, ratio
    return None, raw or None


def parse_gauge_line(line: str) -> tuple[str | None, str | None]:
    raw = line.strip()
    if "|" in raw:
        value, scales = [part.strip() for part in raw.split("|", 1)]
        return value or None, scales or None
    if "=" in raw:
        value, scales = [part.strip() for part in raw.split("=", 1)]
        return value or None, scales or None
    if raw.endswith(")") and "(" in raw:
        prefix, suffix = raw.rsplit("(", 1)
        value = prefix.strip()
        scales = suffix[:-1].strip()
        return value or None, scales or None
    return raw or None, None


def build_scale_options(text: str | None) -> list[dict[str, str | None]]:
    if not text:
        return []
    options = []
    seen_values: set[str] = set()
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        name, ratio = parse_scale_line(cleaned)
        if name and ratio:
            value = ratio
            label = f"{name} ({ratio})"
            raw = f"{name}|{ratio}"
        else:
            value = cleaned
            label = cleaned
            raw = cleaned
            name = None
        if value in seen_values:
            continue
        seen_values.add(value)
        options.append({"name": name, "value": value, "label": label, "raw": raw})
    return options


def build_gauge_options(text: str | None) -> list[dict[str, str | None]]:
    if not text:
        return []
    options = []
    seen_values: set[str] = set()
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        value, scales = parse_gauge_line(cleaned)
        if not value:
            continue
        label = f"{value} ({scales})" if scales else value
        raw = f"{value}|{scales}" if scales else value
        if value in seen_values:
            continue
        seen_values.add(value)
        options.append({"value": value, "label": label, "scales": scales, "raw": raw})
    return options


def get_scale_options_text() -> str:
    settings = get_app_settings()
    if settings.scale_options is None:
        return "\n".join(DEFAULT_SCALE_OPTIONS)
    return settings.scale_options or ""


def get_gauge_options_text() -> str:
    settings = get_app_settings()
    if settings.gauge_options is None:
        return "\n".join(DEFAULT_GAUGE_OPTIONS)
    return settings.gauge_options or ""


def get_scale_options() -> list[dict[str, str | None]]:
    return build_scale_options(get_scale_options_text())


def get_gauge_options() -> list[dict[str, str | None]]:
    return build_gauge_options(get_gauge_options_text())


def get_foam_blocks_text() -> str:
    settings = get_app_settings()
    return settings.foam_blocks or ""


def parse_foam_blocks(text: str) -> list[dict[str, str]]:
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    blocks: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        length = str(item.get("length", "")).strip()
        if not length:
            continue
        block = {
            "length": length,
            "width": str(item.get("width", "")).strip(),
            "height": str(item.get("height", "")).strip(),
            "weight": str(item.get("weight", "")).strip(),
            "compression": str(item.get("compression", "")).strip(),
        }
        blocks.append(block)
    return blocks


def get_foam_blocks() -> list[dict[str, str]]:
    return parse_foam_blocks(get_foam_blocks_text())


def normalize_scale_input(value: str | None) -> str:
    if not value:
        return ""
    cleaned = value.strip()
    for option in get_scale_options():
        if cleaned == option["value"] or (option["name"] and cleaned == option["name"]):
            return option["value"] or cleaned
    return cleaned


def normalize_gauge_input(value: str | None) -> str:
    if not value:
        return ""
    cleaned = value.strip()
    for option in get_gauge_options():
        if cleaned == option["value"] or cleaned == option["label"]:
            return option["value"] or cleaned
    if cleaned.endswith(")") and "(" in cleaned:
        cleaned = cleaned.rsplit("(", 1)[0].strip()
    return cleaned


def format_scale_label(value: str | None) -> str | None:
    if not value:
        return None
    for option in get_scale_options():
        if option["value"] == value and option.get("name"):
            return option["label"]
    return value


def format_gauge_label(value: str | None) -> str | None:
    if not value:
        return None
    for option in get_gauge_options():
        if option["value"] == value:
            return option["label"]
    return value


def get_common_scale_gauge_pairs(limit: int = 3) -> list[dict[str, str | int]]:
    counts: dict[tuple[str, str], int] = {}
    for car in Car.query.all():
        scale = normalize_scale_input(car.scale)
        gauge = normalize_gauge_input(car.gauge)
        if not scale or not gauge:
            continue
        counts[(scale, gauge)] = counts.get((scale, gauge), 0) + 1
    common = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]
    results: list[dict[str, str | int]] = []
    for (scale, gauge), count in common:
        scale_label = format_scale_label(scale) or scale
        gauge_label = format_gauge_label(gauge) or gauge
        results.append(
            {
                "scale": scale,
                "gauge": gauge,
                "label": f"{scale_label} / {gauge_label}",
                "count": count,
            }
        )
    return results


def parse_actual_weight(value: str | None) -> tuple[str, str]:
    if not value:
        return "", ""
    cleaned = value.strip()
    match = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]+)$", cleaned)
    if match:
        amount = match.group(1)
        unit = match.group(2).lower()
        if unit in {"g", "kg", "lb", "oz"}:
            return amount, unit
    parts = cleaned.split()
    if len(parts) >= 2:
        amount = parts[0]
        unit = parts[1].lower()
        if unit in {"g", "kg", "lb", "oz"}:
            return amount, unit
    return cleaned, ""


def parse_actual_length(value: str | None) -> tuple[str, str]:
    if not value:
        return "", ""
    cleaned = value.strip()
    match = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]+)$", cleaned)
    if match:
        amount = match.group(1)
        unit = match.group(2).lower()
        if unit in {"in", "ft", "mm", "cm", "m"}:
            return amount, unit
    parts = cleaned.split()
    if len(parts) >= 2:
        amount = parts[0]
        unit = parts[1].lower()
        if unit in {"in", "ft", "mm", "cm", "m"}:
            return amount, unit
    return cleaned, ""


def parse_currency(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    cleaned = cleaned.replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def format_currency(value: float | None) -> str:
    if value is None:
        return "-"
    return f"${value:,.2f}"


def format_percent(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.1f}%"


def select_worth_value(msrp_value: float | None, price_value: float | None) -> float | None:
    if price_value is None:
        return msrp_value
    if price_value == 0 and msrp_value is not None:
        return msrp_value
    return price_value


def get_scale_name(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    if cleaned in NMRA_WEIGHT_BY_SCALE:
        return cleaned
    for option in get_scale_options():
        if option["value"] == cleaned and option.get("name"):
            return option["name"]
    return None


def weight_to_ounces(amount: str, unit: str) -> float | None:
    try:
        numeric = float(amount)
    except ValueError:
        return None
    factor = WEIGHT_UNIT_TO_OZ.get(unit)
    if factor is None:
        return None
    return numeric * factor


def weight_to_kg(amount: str, unit: str) -> float | None:
    try:
        numeric = float(amount)
    except ValueError:
        return None
    factor = WEIGHT_UNIT_TO_KG.get(unit)
    if factor is None:
        return None
    return numeric * factor


def length_to_inches(amount: str, unit: str) -> float | None:
    try:
        numeric = float(amount)
    except ValueError:
        return None
    factor = LENGTH_UNIT_TO_IN.get(unit)
    if factor is None:
        return None
    return numeric * factor


def length_to_meters(amount: str, unit: str) -> float | None:
    try:
        numeric = float(amount)
    except ValueError:
        return None
    factor = LENGTH_UNIT_TO_M.get(unit)
    if factor is None:
        return None
    return numeric * factor


def format_ounces(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def format_linear_density(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def format_length_value(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def inches_to_unit(value: float, unit: str) -> float | None:
    factor = LENGTH_UNIT_TO_IN.get(unit)
    if factor is None or factor == 0:
        return None
    return value / factor


def maybe_run_nmra_weight_check(
    car: Car,
    previous_weight: str | None,
    previous_length: str | None,
    previous_scale: str | None,
) -> bool:
    if not car.id or not car.actual_weight or not car.actual_length:
        return False
    if (
        previous_weight == car.actual_weight
        and previous_length == car.actual_length
        and previous_scale == car.scale
    ):
        return False
    scale_name = get_scale_name(car.scale)
    if not scale_name or scale_name not in NMRA_WEIGHT_BY_SCALE:
        return False
    weight_amount, weight_unit = parse_actual_weight(car.actual_weight)
    length_amount, length_unit = parse_actual_length(car.actual_length)
    if not weight_amount or not weight_unit or not length_amount or not length_unit:
        return False
    actual_oz = weight_to_ounces(weight_amount, weight_unit)
    length_in = length_to_inches(length_amount, length_unit)
    if actual_oz is None or length_in is None:
        return False
    initial, additional = NMRA_WEIGHT_BY_SCALE[scale_name]
    minimum_oz = initial + (additional * length_in)
    passed = actual_oz >= minimum_oz
    details = (
        f"Min {format_ounces(minimum_oz)} oz (scale {scale_name}, "
        f"length {format_ounces(length_in)} in). "
        f"Actual {format_ounces(actual_oz)} oz."
    )
    inspection_type = InspectionType.query.filter_by(name=NMRA_WEIGHT_CHECK_NAME).first()
    if not inspection_type:
        inspection_type = InspectionType(name=NMRA_WEIGHT_CHECK_NAME)
        db.session.add(inspection_type)
        db.session.commit()
        ensure_db_backup()
    today = datetime.now().date().isoformat()
    existing = (
        CarInspection.query.filter_by(car_id=car.id, inspection_type_id=inspection_type.id)
        .order_by("inspection_date", reverse=True)
        .first()
    )
    if existing and existing.inspection_date == today:
        if existing.inspection_type_id is None:
            existing.inspection_type_id = inspection_type.id
        existing.passed = passed
        existing.details = details
    else:
        db.session.add(
            CarInspection(
                car_id=car.id,
                inspection_type_id=inspection_type.id,
                inspection_date=today,
                details=details,
                passed=passed,
            )
        )
    car.last_inspection_date = today
    db.session.commit()
    ensure_db_backup()
    return True


def maybe_run_nmra_loaded_weight_check(
    car: Car,
    previous_weight: str | None,
    previous_length: str | None,
    previous_scale: str | None,
    force: bool = False,
) -> bool:
    if not car.id or not car.actual_weight or not car.actual_length:
        return False
    if not force and (
        previous_weight == car.actual_weight
        and previous_length == car.actual_length
        and previous_scale == car.scale
    ):
        return False
    scale_name = get_scale_name(car.scale)
    if not scale_name or scale_name not in NMRA_WEIGHT_BY_SCALE:
        return False
    weight_amount, weight_unit = parse_actual_weight(car.actual_weight)
    length_amount, length_unit = parse_actual_length(car.actual_length)
    if not weight_amount or not weight_unit or not length_amount or not length_unit:
        return False
    car_oz = weight_to_ounces(weight_amount, weight_unit)
    length_in = length_to_inches(length_amount, length_unit)
    if car_oz is None or length_in is None:
        return False
    load_oz_total = 0.0
    placements = LoadPlacement.query.filter_by(car_id=car.id).all()
    for placement in placements:
        if not placement.load or not placement.load.weight:
            continue
        load_amount, load_unit = parse_actual_weight(placement.load.weight)
        if not load_amount or not load_unit:
            continue
        load_oz = weight_to_ounces(load_amount, load_unit)
        if load_oz is None:
            continue
        load_oz_total += load_oz * max(placement.quantity, 1)
    initial, additional = NMRA_WEIGHT_BY_SCALE[scale_name]
    minimum_oz = initial + (additional * length_in)
    loaded_oz = car_oz + load_oz_total
    passed = loaded_oz >= minimum_oz
    details = (
        f"Min {format_ounces(minimum_oz)} oz (scale {scale_name}, "
        f"length {format_ounces(length_in)} in). "
        f"Loaded {format_ounces(loaded_oz)} oz "
        f"(car {format_ounces(car_oz)} oz + loads {format_ounces(load_oz_total)} oz)."
    )
    inspection_type = InspectionType.query.filter_by(name=NMRA_WEIGHT_CHECK_LOADED_NAME).first()
    if not inspection_type:
        inspection_type = InspectionType(name=NMRA_WEIGHT_CHECK_LOADED_NAME)
        db.session.add(inspection_type)
        db.session.commit()
        ensure_db_backup()
    today = datetime.now().date().isoformat()
    existing = (
        CarInspection.query.filter_by(car_id=car.id, inspection_type_id=inspection_type.id)
        .order_by("inspection_date", reverse=True)
        .first()
    )
    if existing and existing.inspection_date == today:
        if existing.inspection_type_id is None:
            existing.inspection_type_id = inspection_type.id
        existing.passed = passed
        existing.details = details
    else:
        db.session.add(
            CarInspection(
                car_id=car.id,
                inspection_type_id=inspection_type.id,
                inspection_date=today,
                details=details,
                passed=passed,
            )
        )
    car.last_inspection_date = today
    db.session.commit()
    ensure_db_backup()
    return True


def calculate_linear_density(car: Car) -> str | None:
    if not car.actual_weight or not car.actual_length:
        return None
    weight_amount, weight_unit = parse_actual_weight(car.actual_weight)
    length_amount, length_unit = parse_actual_length(car.actual_length)
    if not weight_amount or not weight_unit or not length_amount or not length_unit:
        return None
    weight_kg = weight_to_kg(weight_amount, weight_unit)
    length_m = length_to_meters(length_amount, length_unit)
    if weight_kg is None or length_m is None or length_m <= 0 or weight_kg <= 0:
        return None
    density = weight_kg / length_m
    return f"{format_linear_density(density)} kg/m"


def paginate_list(items: list, page: int, page_size: str, route: str, route_params: dict) -> tuple[list, dict]:
    total = len(items)
    if page_size == "all":
        start = 1 if total else 0
        return (
            items,
            {
                "page": 1,
                "pages": 1,
                "total": total,
                "start": start,
                "end": total,
                "page_size": page_size,
                "prev_url": None,
                "next_url": None,
            },
        )
    per_page = int(page_size)
    pages = max(1, math.ceil(total / per_page))
    page = min(max(1, page), pages)
    start_index = (page - 1) * per_page
    end_index = min(start_index + per_page, total)
    prev_url = url_for(route, **route_params, page=page - 1) if page > 1 else None
    next_url = url_for(route, **route_params, page=page + 1) if page < pages else None
    return (
        items[start_index:end_index],
        {
            "page": page,
            "pages": pages,
            "total": total,
            "start": start_index + 1 if total else 0,
            "end": end_index,
            "page_size": page_size,
            "prev_url": prev_url,
            "next_url": next_url,
        },
    )


def paginate_query(query, page: int, page_size: str, route: str, route_params: dict) -> tuple[list, dict]:
    if page_size == "all":
        items = query.all()
        total = len(items)
        start = 1 if total else 0
        return (
            items,
            {
                "page": 1,
                "pages": 1,
                "total": total,
                "start": start,
                "end": total,
                "page_size": page_size,
                "prev_url": None,
                "next_url": None,
            },
        )
    per_page = int(page_size)
    total = query.total()
    pages = max(1, math.ceil(total / per_page)) if total else 1
    page = min(max(1, page), pages)
    items = query.page(page, per_page)
    start_index = (page - 1) * per_page
    end_index = min(start_index + per_page, total)
    prev_url = url_for(route, **route_params, page=page - 1) if page > 1 else None
    next_url = url_for(route, **route_params, page=page + 1) if page < pages else None
    return (
        items,
        {
            "page": page,
            "pages": pages,
            "total": total,
            "start": start_index + 1 if total else 0,
            "end": end_index,
            "page_size": page_size,
            "prev_url": prev_url,
            "next_url": next_url,
        },
    )


def prefetch_car_relations(cars: list[Car]) -> None:
    railroad_ids = {car.railroad_id for car in cars if car.railroad_id}
    class_ids = {car.car_class_id for car in cars if car.car_class_id}
    location_ids = {car.location_id for car in cars if car.location_id}
    railroads = {rid: Railroad.query.get(rid) for rid in railroad_ids}
    classes = {cid: CarClass.query.get(cid) for cid in class_ids}
    locations = {lid: Location.query.get(lid) for lid in location_ids}
    logo_ids = {
        railroad.representative_logo_id
        for railroad in railroads.values()
        if railroad and railroad.representative_logo_id
    }
    logos = {lid: RailroadLogo.query.get(lid) for lid in logo_ids}
    for car in cars:
        if car.railroad_id:
            railroad = railroads.get(car.railroad_id)
            car._railroad_ref = railroad
            if railroad and railroad.representative_logo_id:
                railroad._representative_logo_ref = logos.get(railroad.representative_logo_id)
        if car.car_class_id:
            car._car_class_ref = classes.get(car.car_class_id)
        if car.location_id:
            car._location_ref = locations.get(car.location_id)


def parse_number(value: str) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_length_value(value: str, unit: str, fallback_unit: str) -> tuple[float | None, str]:
    cleaned = value.strip()
    resolved_unit = (unit or fallback_unit).strip().lower()
    if not cleaned:
        return None, resolved_unit
    amount = parse_number(cleaned)
    if amount is None or resolved_unit not in LENGTH_UNIT_TO_IN:
        return None, resolved_unit
    return amount, resolved_unit


def plan_fill_with_compression(
    target_units: int,
    options: list[dict[str, int]],
) -> tuple[list[dict[str, int]], int]:
    if target_units <= 0 or not options:
        return [], max(target_units, 0)
    min_units = min(option["min_units"] for option in options if option["min_units"] > 0) if options else 0
    if min_units <= 0:
        return [], target_units
    max_compress = max(option["compress_units"] for option in options) if options else 0
    max_blocks = int(math.ceil(target_units / min_units)) + 2
    max_nominal = target_units + (max_blocks * max_compress)
    dp: list[dict[str, int] | None] = [None] * (max_nominal + 1)
    dp[0] = {"count": 0, "pref": 0, "compress": 0}
    plans: list[list[int] | None] = [None] * (max_nominal + 1)
    plans[0] = []

    for total in range(1, max_nominal + 1):
        best_entry = None
        best_plan = None
        for option in options:
            nominal = option["nominal_units"]
            if nominal > total:
                continue
            prev = dp[total - nominal]
            if prev is None:
                continue
            count = prev["count"] + 1
            pref = prev["pref"] + option["pref"]
            compress = prev["compress"] + option["compress_units"]
            candidate = {"count": count, "pref": pref, "compress": compress}
            if (
                best_entry is None
                or count < best_entry["count"]
                or (count == best_entry["count"] and compress < best_entry["compress"])
                or (count == best_entry["count"] and compress == best_entry["compress"] and pref < best_entry["pref"])
            ):
                best_entry = candidate
                prev_plan = plans[total - nominal] or []
                best_plan = prev_plan + [option["key"]]
        dp[total] = best_entry
        plans[total] = best_plan

    best_fit_total = None
    best_fit_entry = None
    best_fit_plan: list[int] | None = None
    for total in range(target_units, max_nominal + 1):
        entry = dp[total]
        if entry is None:
            continue
        if total - entry["compress"] > target_units:
            continue
        plan = plans[total] or []
        required_compress = max(total - target_units, 0)
        if (
            best_fit_entry is None
            or entry["count"] < best_fit_entry["count"]
            or (
                entry["count"] == best_fit_entry["count"]
                and required_compress < max(best_fit_total - target_units, 0)  # type: ignore[operator]
            )
            or (
                entry["count"] == best_fit_entry["count"]
                and required_compress == max(best_fit_total - target_units, 0)  # type: ignore[operator]
                and entry["pref"] < best_fit_entry["pref"]
            )
            or (
                entry["count"] == best_fit_entry["count"]
                and required_compress == max(best_fit_total - target_units, 0)  # type: ignore[operator]
                and entry["pref"] == best_fit_entry["pref"]
                and total < best_fit_total  # type: ignore[operator]
            )
        ):
            best_fit_entry = entry
            best_fit_total = total
            best_fit_plan = plan

    if best_fit_entry is None or best_fit_total is None or best_fit_plan is None:
        fallback_total = None
        fallback_entry = None
        fallback_plan = None
        for total in range(target_units, -1, -1):
            entry = dp[total]
            if entry is None:
                continue
            fallback_total = total
            fallback_entry = entry
            fallback_plan = plans[total] or []
            break
        if fallback_entry is None or fallback_total is None or fallback_plan is None:
            return [], target_units
        best_fit_total = fallback_total
        best_fit_entry = fallback_entry
        best_fit_plan = fallback_plan

    remaining_compress = max(best_fit_total - target_units, 0)
    segments: list[dict[str, int]] = []
    for key in best_fit_plan:
        option = next((opt for opt in options if opt["key"] == key), None)
        if option is None:
            continue
        compress_used = min(option["compress_units"], remaining_compress)
        remaining_compress -= compress_used
        segments.append({"key": key, "units": option["nominal_units"] - compress_used})

    used_units = sum(segment["units"] for segment in segments)
    leftover = max(target_units - used_units, 0)
    return segments, leftover


def build_foam_dp(max_units: int, sizes_units: list[int]) -> list[tuple[int, list[int]] | None]:
    dp: list[tuple[int, list[int]] | None] = [None] * (max_units + 1)
    dp[0] = (0, [])
    for value in range(1, max_units + 1):
        best: tuple[int, list[int]] | None = None
        for size in sizes_units:
            if size > value or dp[value - size] is None:
                continue
            count, plan = dp[value - size]
            candidate = (count + 1, plan + [size])
            if best is None or candidate[0] < best[0]:
                best = candidate
        dp[value] = best
    return dp


def select_foam_plan(
    target_units: int,
    dp: list[tuple[int, list[int]] | None],
) -> tuple[int, list[int], int]:
    if target_units <= 0:
        return 0, [], 0
    if target_units < len(dp) and dp[target_units] is not None:
        count, plan = dp[target_units]
        return count, plan, 0
    best: tuple[int, int, list[int]] | None = None
    for value in range(min(target_units, len(dp) - 1), -1, -1):
        if dp[value] is None:
            continue
        leftover = target_units - value
        count, plan = dp[value]
        candidate = (leftover, count, plan)
        if best is None or candidate[:2] < best[:2]:
            best = candidate
            if leftover == 0:
                break
    if best is None:
        return 0, [], target_units
    return best[1], best[2], best[0]

def get_or_create_location(name: str) -> Optional[Location]:
    if not name:
        return None
    loc = Location.query.filter_by(name=name).first()
    if loc:
        return loc
    location_type = "bag"
    if "-F" in name:
        location_type = "flat"
    elif "staging" in name.lower() or " st" in name.lower():
        location_type = "staging_track"
    elif "yard" in name.lower() or " yd" in name.lower():
        location_type = "yard_track"
    elif "Carrier" in name or "carrier" in name:
        location_type = "carrier"
    loc = Location(name=name, location_type=location_type)
    db.session.add(loc)
    return loc


def normalize_color_list(value: str) -> str:
    if not value:
        return ""
    return ",".join([part.strip() for part in value.split(",") if part.strip()])


def allowed_logo_extension(filename: str) -> bool:
    _, ext = os.path.splitext(filename.lower())
    return ext in {".png", ".jpg", ".jpeg", ".svg"}


def save_logo_file(file_storage, railroad_id: int) -> str | None:
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_logo_extension(file_storage.filename):
        return None
    filename = secure_filename(file_storage.filename)
    _, ext = os.path.splitext(filename)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_name = f"railroad-{railroad_id}-logo-{timestamp}{ext.lower()}"
    upload_dir = current_app.config.get("LOGO_UPLOAD_FOLDER")
    if not upload_dir:
        return None
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, safe_name)
    file_storage.save(file_path)
    return f"uploads/railroad-logos/{safe_name}"


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = normalize_username(request.form.get("username"))
        password = request.form.get("password", "")
        throttled, retry_after = password_auth_is_throttled(username)
        if throttled:
            flash(f"Too many sign-in attempts from this client. Try again in about {retry_after} seconds.", "danger")
            response = make_response(render_template("login.html", next_url=get_post_login_redirect()), 429)
            response.headers["Retry-After"] = str(retry_after)
            return response
        user = User.query.filter_by(username=username).first()
        if user and is_user_locked(user):
            flash("That account is temporarily locked after repeated sign-in failures. Try again later.", "danger")
            return render_template("login.html", next_url=get_post_login_redirect()), 423
        if not user or not user.is_active or not verify_password(user.password_hash, password):
            record_failed_password_attempt(username)
            flash("Invalid username or password.", "danger")
            return render_template("login.html", next_url=get_post_login_redirect()), 401
        clear_failed_password_throttle(username)
        clear_failed_auth_attempts(user, "password")
        if user_has_verified_mfa(user):
            start_pending_login(user)
            return redirect(url_for("main.login_verify"))
        complete_login(user)
        flash("MFA is not enabled. Effective permissions are limited to read-only until TOTP or a passkey is configured.", "info")
        return redirect(consume_login_redirect())
    return render_template("login.html", next_url=get_post_login_redirect())


@main_bp.route("/login/verify", methods=["GET", "POST"])
def login_verify():
    user = get_pending_user()
    if not user:
        return redirect(url_for("main.login"))
    if is_user_locked(user):
        clear_session_and_auth_state()
        flash("That account is temporarily locked after repeated verification failures. Try again later.", "danger")
        return redirect(url_for("main.login"))
    if request.method == "POST":
        code = request.form.get("totp_code", "").strip()
        secret = get_user_totp_secret(user)
        counter = get_valid_totp_counter(secret, code) if (user.totp_enabled and secret) else None
        if counter is None or (user.last_totp_counter is not None and counter <= user.last_totp_counter):
            locked = register_failed_auth_attempt(user, "mfa")
            if locked:
                clear_session_and_auth_state()
                flash("That account is temporarily locked after repeated verification failures. Try again later.", "danger")
                return redirect(url_for("main.login"))
            flash("Invalid authentication code.", "danger")
            return render_template("login_verify.html", user=user), 401
        user.last_totp_counter = counter
        clear_failed_auth_attempts(user, "mfa")
        complete_login(user)
        return redirect(consume_login_redirect())
    return render_template("login_verify.html", user=user)


@main_bp.route("/login/passkeys/options", methods=["POST"])
def login_passkey_options():
    payload = request.get_json(silent=True) or {}
    user = get_login_passkey_user()
    if not user or not user.is_active or not user.has_passkeys or is_user_locked(user):
        return jsonify({"error": "Passkey sign-in is unavailable for that account."}), 400
    start_pending_login(user)
    session["login_next"] = get_post_login_redirect_from_json(payload)
    options = generate_authentication_options(
        rp_id=get_webauthn_rp_id(),
        allow_credentials=get_passkey_descriptors(user),
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    session["passkey_auth_challenge"] = bytes_to_base64url(options.challenge)
    return jsonify(json.loads(options_to_json(options)))


@main_bp.route("/login/passkeys/verify", methods=["POST"])
def login_passkey_verify():
    user = get_pending_user()
    payload = request.get_json(silent=True) or {}
    credential = payload.get("credential")
    challenge = session.get("passkey_auth_challenge")
    if not user or not isinstance(credential, dict) or not isinstance(challenge, str):
        return jsonify({"error": "Passkey authentication could not be completed."}), 400
    credential_id = credential.get("id")
    record = find_passkey_record(user, credential_id)
    if not record:
        locked = register_failed_auth_attempt(user, "mfa") if user else False
        if locked:
            clear_session_and_auth_state()
            return jsonify({"error": "That account is temporarily locked. Try again later."}), 423
        return jsonify({"error": "Passkey credential was not recognized."}), 400
    try:
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=get_webauthn_rp_id(),
            expected_origin=get_webauthn_origin(),
            credential_public_key=base64url_to_bytes(str(record.get("public_key", ""))),
            credential_current_sign_count=int(record.get("sign_count", 0) or 0),
            require_user_verification=True,
        )
    except Exception:
        locked = register_failed_auth_attempt(user, "mfa") if user else False
        if locked:
            clear_session_and_auth_state()
            return jsonify({"error": "That account is temporarily locked. Try again later."}), 423
        return jsonify({"error": "Passkey authentication could not be completed."}), 400
    record["sign_count"] = verification.new_sign_count
    user.passkeys = list(user.passkeys or [])
    session.pop("passkey_auth_challenge", None)
    clear_failed_auth_attempts(user, "mfa")
    complete_login(user)
    return jsonify({"ok": True, "redirect_url": consume_login_redirect()})


@main_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Signed out.", "success")
    return redirect(url_for("main.login"))


@main_bp.route("/setup", methods=["GET", "POST"])
def setup_users():
    if users_exist():
        return redirect(url_for("main.login"))
    form_values = build_user_form_defaults()
    if request.method == "POST":
        form_values["username"] = request.form.get("username", "").strip()
        form_values["display_name"] = request.form.get("display_name", "").strip()
        form_values["email"] = request.form.get("email", "").strip()
        try:
            user = create_user_from_form(force_superuser=True)
        except ValueError as exc:
            flash(str(exc), "danger")
            return render_template("account_setup.html", users=[], form_values=form_values, setup_mode=True), 400
        establish_authenticated_session(user, update_last_login=True)
        updated_count = backfill_ownerless_documents(user)
        if updated_count:
            flash(f"Assigned ownership for {updated_count} existing records to {get_user_label(user)}.", "success")
        flash("Initial administrator account created. Configure TOTP or a passkey to unlock write access.", "success")
        ensure_db_backup()
        return redirect(url_for("main.account_security"))
    return render_template("account_setup.html", users=[], form_values=form_values, setup_mode=True)


@main_bp.route("/account", methods=["GET", "POST"])
@main_bp.route("/account/security", methods=["GET", "POST"])
def account_security():
    user = g.current_user
    if request.method == "POST":
        user.email = normalize_email(request.form.get("email")) or None
        db.session.commit()
        ensure_db_backup()
        flash("Account email updated.", "success")
        return redirect(url_for("main.account_security"))
    return render_template("account_security.html", user=user)


@main_bp.route("/account/password", methods=["GET", "POST"])
def account_password():
    user = g.current_user
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not verify_password(user.password_hash, current_password):
            flash("Current password is incorrect.", "danger")
            return render_template("account_password.html"), 400
        if new_password != confirm_password:
            flash("New passwords do not match.", "danger")
            return render_template("account_password.html"), 400
        validate_password_strength(new_password)
        bump_session_version(user)
        user.password_hash = hash_password(new_password)
        db.session.commit()
        ensure_db_backup()
        clear_session_and_auth_state()
        flash("Password updated. Sign in again with the new password.", "success")
        return redirect(url_for("main.login"))
    return render_template("account_password.html")


@main_bp.route("/account/totp/start", methods=["POST"])
def account_totp_start():
    user = g.current_user
    secret = generate_totp_secret()
    user.totp_secret_encrypted = encrypt_value(secret)
    user.totp_enabled = False
    db.session.commit()
    return redirect(url_for("main.account_totp_setup"))


@main_bp.route("/account/totp/setup", methods=["GET"])
def account_totp_setup():
    user = g.current_user
    secret = get_user_totp_secret(user)
    if not secret:
        flash("Start TOTP setup first.", "danger")
        return redirect(url_for("main.account_security"))
    totp_uri = build_totp_uri(secret, user.username or "", get_webauthn_rp_name())
    qr_code_data_uri = None
    try:
        qr_code_data_uri = build_qr_data_uri(totp_uri)
    except ModuleNotFoundError:
        flash("QR code rendering is unavailable in this Python environment. The setup secret is shown below instead.", "info")
    return render_template(
        "totp_setup.html",
        user=user,
        enrollment_secret=secret,
        totp_uri=totp_uri,
        qr_code_data_uri=qr_code_data_uri,
    )


@main_bp.route("/account/totp/enable", methods=["POST"])
def account_totp_enable():
    user = g.current_user
    secret = get_user_totp_secret(user)
    code = request.form.get("totp_code", "").strip()
    counter = get_valid_totp_counter(secret, code) if secret else None
    if counter is None or (user.last_totp_counter is not None and counter <= user.last_totp_counter):
        flash("Invalid TOTP code.", "danger")
        return redirect(url_for("main.account_security"))
    user.last_totp_counter = counter
    user.totp_enabled = True
    bump_session_version(user)
    db.session.commit()
    session["session_version"] = int(user.session_version or 1)
    mark_recent_auth()
    ensure_db_backup()
    flash("TOTP is enabled.", "success")
    return redirect(url_for("main.account_security"))


@main_bp.route("/account/totp/disable", methods=["POST"])
def account_totp_disable():
    user = g.current_user
    user.totp_secret_encrypted = None
    user.totp_enabled = False
    user.last_totp_counter = None
    bump_session_version(user)
    db.session.commit()
    session["session_version"] = int(user.session_version or 1)
    mark_recent_auth()
    ensure_db_backup()
    flash("TOTP was disabled.", "success")
    return redirect(url_for("main.account_security"))


@main_bp.route("/account/passkeys/options", methods=["POST"])
def account_passkey_options():
    user = g.current_user
    options = generate_registration_options(
        rp_id=get_webauthn_rp_id(),
        rp_name=get_webauthn_rp_name(),
        user_id=str(user.id).encode("utf-8"),
        user_name=user.username or "",
        user_display_name=get_user_label(user),
        exclude_credentials=get_passkey_descriptors(user),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    session["passkey_registration_challenge"] = bytes_to_base64url(options.challenge)
    return jsonify(json.loads(options_to_json(options)))


@main_bp.route("/account/passkeys/verify", methods=["POST"])
def account_passkey_verify():
    user = g.current_user
    payload = request.get_json(silent=True) or {}
    credential = payload.get("credential")
    challenge = session.get("passkey_registration_challenge")
    if not isinstance(credential, dict) or not isinstance(challenge, str):
        return jsonify({"error": "Passkey registration could not be completed."}), 400
    try:
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=get_webauthn_rp_id(),
            expected_origin=get_webauthn_origin(),
            require_user_verification=True,
        )
    except Exception:
        return jsonify({"error": "Passkey registration could not be completed."}), 400
    user.passkeys = list(user.passkeys or [])
    user.passkeys.append(
        {
            "credential_id": bytes_to_base64url(verification.credential_id),
            "public_key": bytes_to_base64url(verification.credential_public_key),
            "sign_count": verification.sign_count,
            "label": request.headers.get("X-Passkey-Label", "").strip() or f"Passkey {len(user.passkeys) + 1}",
            "device_type": str(getattr(verification, "credential_device_type", "")),
            "backed_up": bool(getattr(verification, "credential_backed_up", False)),
        }
    )
    session.pop("passkey_registration_challenge", None)
    bump_session_version(user)
    db.session.commit()
    session["session_version"] = int(user.session_version or 1)
    mark_recent_auth()
    ensure_db_backup()
    return jsonify({"ok": True})


@main_bp.route("/account/passkeys/<credential_id>/delete", methods=["POST"])
def account_passkey_delete(credential_id: str):
    user = g.current_user
    user.passkeys = [item for item in (user.passkeys or []) if item.get("credential_id") != credential_id]
    bump_session_version(user)
    db.session.commit()
    session["session_version"] = int(user.session_version or 1)
    mark_recent_auth()
    ensure_db_backup()
    flash("Passkey removed.", "success")
    return redirect(url_for("main.account_security"))


@main_bp.route("/")
def index():
    return redirect(url_for("main.inventory"))


@main_bp.route("/inventory")
def inventory():
    car_query = Car.query.order_by("id", reverse=True)
    page_size = get_page_size()
    page = get_page_number()
    paged_cars, pagination = paginate_query(car_query, page, page_size, "main.inventory", {})
    prefetch_car_relations(paged_cars)
    return render_template("inventory.html", cars=paged_cars, pagination=pagination)


@main_bp.route("/inventory2")
def inventory2():
    return render_template("inventory2.html")


@main_bp.route("/reports")
def reports():
    return render_template("reports.html")


def inspection_type_tree(types: list[InspectionType], excluded_id: int | None = None) -> list[dict]:
    by_parent: dict[int | None, list[InspectionType]] = {}
    for inspection_type in types:
        if excluded_id and inspection_type.id == excluded_id:
            continue
        by_parent.setdefault(inspection_type.parent_id, []).append(inspection_type)
    for group in by_parent.values():
        group.sort(key=lambda item: (item.name or "").lower())

    results: list[dict] = []

    def walk(parent_id: int | None, depth: int) -> None:
        for inspection_type in by_parent.get(parent_id, []):
            label = f"{'-- ' * depth}{inspection_type.name or ''}"
            results.append({"type": inspection_type, "label": label, "depth": depth})
            walk(inspection_type.id, depth + 1)

    walk(None, 0)
    return results


@main_bp.route("/reports/inspections")
def inspections_report():
    inspection_types = InspectionType.query.all()
    type_rows = inspection_type_tree(inspection_types)
    selected_type_id = request.args.get("inspection_type_id", "").strip()
    selected_result = request.args.get("result", "").strip()
    inspections: list[CarInspection] = []
    if selected_type_id.isdigit() and selected_result in {"passed", "failed"}:
        type_id = int(selected_type_id)
        all_inspections = CarInspection.query.filter_by(inspection_type_id=type_id).all()
        all_inspections.sort(
            key=lambda inspection: (inspection.inspection_date is None, inspection.inspection_date or ""),
            reverse=True,
        )
        latest_by_car: dict[int, CarInspection] = {}
        for inspection in all_inspections:
            if inspection.car_id is None:
                continue
            if inspection.car_id in latest_by_car:
                continue
            latest_by_car[inspection.car_id] = inspection
        passed_flag = selected_result == "passed"
        inspections = [
            inspection
            for inspection in latest_by_car.values()
            if inspection.passed is not None and inspection.passed == passed_flag
        ]
        inspections.sort(
            key=lambda inspection: (inspection.inspection_date is None, inspection.inspection_date or ""),
            reverse=True,
        )
    return render_template(
        "inspection_report.html",
        inspection_types=type_rows,
        selected_type_id=selected_type_id,
        selected_result=selected_result,
        inspections=inspections,
    )


def update_last_inspection_date(car_id: int | None) -> None:
    if not car_id:
        return
    car = Car.query.get(car_id)
    if not car:
        return
    inspections = CarInspection.query.filter_by(car_id=car.id).all()
    if inspections:
        inspections.sort(
            key=lambda inspection: (inspection.inspection_date is None, inspection.inspection_date or ""),
            reverse=True,
        )
        car.last_inspection_date = inspections[0].inspection_date
    else:
        car.last_inspection_date = None


@main_bp.route("/inspections/<int:inspection_id>/delete", methods=["POST"])
def inspection_delete(inspection_id: int):
    inspection = CarInspection.query.get_or_404(inspection_id)
    car_id = inspection.car_id
    db.session.delete(inspection)
    update_last_inspection_date(car_id)
    db.session.commit()
    ensure_db_backup()
    next_url = request.form.get("next", "").strip()
    if next_url.startswith("/"):
        return redirect(next_url)
    if car_id:
        return redirect(url_for("main.car_detail", car_id=car_id))
    return redirect(url_for("main.reports"))


@main_bp.route("/inventory/export")
def inventory_export():
    cars = Car.query.order_by("id").all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Reporting Mark",
            "Railroad",
            "Car Class",
            "Car Type",
            "Wheel Arrangement",
            "Tender Axles",
            "Capacity (Lettering)",
            "Weight (Lettering)",
            "Load Limit",
            "AAR Plate",
            "Location",
            "Brand",
            "UPC",
            "Car #",
            "DCC ID",
            "DCC Decoder Type",
            "DCC Decoder Connection Type",
            "DCC Decoder Keep-Alive",
            "Notes",
            "Traction Drivers",
            "Built (Lettering)",
            "Alt Date",
            "Reweight date",
            "Repack Bearings Date",
            "Last Inspection Date",
            "Other Lettering",
            "MSRP",
            "Price",
            "Load",
            "Repairs Req’d",
        ]
    )
    for car in cars:
        class_type = car.car_class.car_type if car.car_class else ""
        class_wheel = car.car_class.wheel_arrangement if car.car_class else ""
        class_tender = car.car_class.tender_axles if car.car_class else ""
        class_capacity = car.car_class.capacity if car.car_class else ""
        class_weight = car.car_class.weight if car.car_class else ""
        class_load_limit = car.car_class.load_limit if car.car_class else ""
        class_aar_plate = car.car_class.aar_plate if car.car_class else ""
        writer.writerow(
            [
                car.railroad.reporting_mark if car.railroad else (car.reporting_mark_override or ""),
                car.railroad.name if car.railroad else "",
                car.car_class.code if car.car_class else "",
                car.car_type_override or class_type or "",
                car.wheel_arrangement_override or class_wheel or "",
                car.tender_axles_override or class_tender or "",
                car.capacity_override or class_capacity or "",
                car.weight_override or class_weight or "",
                car.load_limit_override or class_load_limit or "",
                car.aar_plate_override or class_aar_plate or "",
                car.location.name if car.location else "",
                car.brand or "",
                car.upc or "",
                car.car_number or "",
                car.dcc_id or "",
                car.dcc_decoder_type or "",
                car.dcc_decoder_connection_type or "",
                "Yes" if car.dcc_decoder_keep_alive else "",
                car.notes or "",
                "Yes" if car.traction_drivers else "",
                car.built or "",
                car.alt_date or "",
                car.reweight_date or "",
                car.repack_bearings_date or "",
                car.last_inspection_date or "",
                car.other_lettering or "",
                car.msrp or "",
                car.price or "",
                car.load or "",
                car.repairs_required or "",
            ]
        )
    response = Response(output.getvalue(), mimetype="text/csv")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    response.headers["Content-Disposition"] = f"attachment; filename=inventory-export-{timestamp}.csv"
    return response


@main_bp.route("/reports/locomotive-dcc-export")
def locomotive_dcc_export():
    cars = Car.query.order_by("id").all()
    locomotive_cars = []
    for car in cars:
        class_is_locomotive = car.car_class.is_locomotive if car.car_class else None
        is_locomotive = (
            car.is_locomotive_override if car.is_locomotive_override is not None else class_is_locomotive
        )
        if is_locomotive and (
            car.dcc_id or car.dcc_decoder_type or car.dcc_decoder_connection_type or car.dcc_decoder_keep_alive
        ):
            locomotive_cars.append(car)
    locomotive_cars.sort(
        key=lambda car: (
            car.dcc_id or "",
            car.railroad.reporting_mark if car.railroad else (car.reporting_mark_override or ""),
            car.car_number or "",
        )
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "DCC ID",
            "Decoder Type",
            "Decoder Connection Type",
            "Decoder Keep-Alive",
            "Reporting Mark",
            "Car #",
            "Car Class",
            "Class Era",
        ]
    )
    for car in locomotive_cars:
        writer.writerow(
            [
                car.dcc_id or "",
                car.dcc_decoder_type or "",
                car.dcc_decoder_connection_type or "",
                "Yes" if car.dcc_decoder_keep_alive else "",
                car.railroad.reporting_mark if car.railroad else (car.reporting_mark_override or ""),
                car.car_number or "",
                car.car_class.code if car.car_class else "",
                car.car_class.era if car.car_class and car.car_class.era else "",
            ]
        )
    response = Response(output.getvalue(), mimetype="text/csv")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    response.headers["Content-Disposition"] = (
        f"attachment; filename=locomotive-dcc-export-{timestamp}.csv"
    )
    return response


@main_bp.route("/reports/era-histogram")
def era_histogram():
    mode = request.args.get("mode", "car").strip().lower()
    if mode not in {"car", "class"}:
        mode = "car"

    current_year = datetime.now().year
    cars = Car.query.order_by("id").all()
    decade_counts: dict[int, int] = {}
    class_seen: dict[int, set[int]] = {}
    for car in cars:
        if not car.car_class or not car.car_class.era:
            continue
        era_text = car.car_class.era.lower()
        years = [int(year) for year in re.findall(r"(\d{4})", era_text)]
        if not years and "present" in era_text:
            years = [current_year]
        if not years:
            continue
        start_year = min(years)
        end_year = max(years)
        if "present" in era_text or "current" in era_text or "today" in era_text:
            end_year = current_year
        if end_year < start_year:
            end_year = start_year
        start_decade = (start_year // 10) * 10
        end_decade = (end_year // 10) * 10
        if mode == "class":
            if car.car_class_id is None:
                continue
            for decade in range(start_decade, end_decade + 1, 10):
                class_seen.setdefault(decade, set()).add(car.car_class_id)
        else:
            for decade in range(start_decade, end_decade + 1, 10):
                decade_counts[decade] = decade_counts.get(decade, 0) + 1

    if mode == "class":
        decade_counts = {decade: len(class_ids) for decade, class_ids in class_seen.items()}

    if decade_counts:
        min_decade = min(decade_counts.keys())
        max_decade = max(max(decade_counts.keys()), (current_year // 10) * 10)
        for decade in range(min_decade, max_decade + 1, 10):
            decade_counts.setdefault(decade, 0)

    sorted_counts = sorted(decade_counts.items(), key=lambda item: item[0])
    max_count = max((count for _, count in sorted_counts), default=0)
    return render_template(
        "era_histogram.html",
        era_counts=sorted_counts,
        max_count=max_count,
        total=sum(decade_counts.values()),
        mode=mode,
    )


@main_bp.route("/reports/introduction-years")
def introduction_years():
    current_year = datetime.now().year
    classes = CarClass.query.order_by("code").all()
    cars = Car.query.order_by("id").all()

    class_by_year: dict[int, list[CarClass]] = {}
    for car_class in classes:
        if not car_class.era:
            continue
        match = re.search(r"(\d{4})", car_class.era)
        if not match:
            continue
        year = int(match.group(1))
        class_by_year.setdefault(year, []).append(car_class)

    built_by_year: dict[int, list[Car]] = {}
    for car in cars:
        if not car.built:
            continue
        match = re.search(r"(\d{4})", car.built)
        if not match:
            continue
        year = int(match.group(1))
        built_by_year.setdefault(year, []).append(car)

    all_years = set(class_by_year.keys()) | set(built_by_year.keys())
    if not all_years:
        return render_template("introduction_years.html", year_entries=[], current_year=current_year)

    start_year = min(all_years)
    year_entries = []
    for year in range(start_year, current_year + 1):
        class_entries = class_by_year.get(year, [])
        built_entries = built_by_year.get(year, [])
        if not class_entries and not built_entries:
            continue
        class_entries = sorted(class_entries, key=lambda item: item.code)
        built_entries = sorted(built_entries, key=lambda item: item.id)
        year_entries.append(
            {
                "year": year,
                "classes": class_entries,
                "cars": built_entries,
            }
        )

    return render_template(
        "introduction_years.html",
        year_entries=year_entries,
        current_year=current_year,
    )


@main_bp.route("/reports/financial-summary")
def financial_summary():
    cars = Car.query.order_by("id").all()
    summary = {
        "total_cars": len(cars),
        "msrp_total": 0.0,
        "msrp_count": 0,
        "price_total": 0.0,
        "price_count": 0,
        "worth_total": 0.0,
        "worth_count": 0,
        "missing_msrp_with_price": 0,
        "missing_worth": 0,
        "minimum_worth_total": 0.0,
    }
    categories = {
        "Locomotives": {
            "count": 0,
            "msrp_total": 0.0,
            "msrp_count": 0,
            "price_total": 0.0,
            "price_count": 0,
            "worth_total": 0.0,
            "worth_count": 0,
            "missing_msrp_with_price": 0,
        },
        "Cars": {
            "count": 0,
            "msrp_total": 0.0,
            "msrp_count": 0,
            "price_total": 0.0,
            "price_count": 0,
            "worth_total": 0.0,
            "worth_count": 0,
            "missing_msrp_with_price": 0,
        },
    }

    for car in cars:
        class_is_locomotive = car.car_class.is_locomotive if car.car_class else None
        is_locomotive = (
            car.is_locomotive_override if car.is_locomotive_override is not None else class_is_locomotive
        )
        bucket = categories["Locomotives"] if is_locomotive else categories["Cars"]
        bucket["count"] += 1

        msrp_value = parse_currency(car.msrp)
        price_value = parse_currency(car.price)

        if msrp_value is not None:
            summary["msrp_total"] += msrp_value
            summary["msrp_count"] += 1
            bucket["msrp_total"] += msrp_value
            bucket["msrp_count"] += 1
        if price_value is not None:
            summary["price_total"] += price_value
            summary["price_count"] += 1
            bucket["price_total"] += price_value
            bucket["price_count"] += 1
        if msrp_value is None and price_value is not None:
            summary["missing_msrp_with_price"] += 1
            bucket["missing_msrp_with_price"] += 1

        worth_value = select_worth_value(msrp_value, price_value)
        if worth_value is not None:
            summary["worth_total"] += worth_value
            summary["worth_count"] += 1
            bucket["worth_total"] += worth_value
            bucket["worth_count"] += 1
        else:
            summary["missing_worth"] += 1
            summary["minimum_worth_total"] += 5.0

    def format_summary(values: dict[str, float | int]) -> dict[str, str | int]:
        coverage = None
        if values["count"]:
            coverage = (values["msrp_count"] / values["count"]) * 100
        return {
            "count": values["count"],
            "msrp_total": format_currency(values["msrp_total"]) if values["msrp_count"] else "-",
            "msrp_count": values["msrp_count"],
            "msrp_coverage": format_percent(coverage),
            "price_total": format_currency(values["price_total"]) if values["price_count"] else "-",
            "price_count": values["price_count"],
            "worth_total": format_currency(values["worth_total"]) if values["worth_count"] else "-",
            "worth_count": values["worth_count"],
            "missing_msrp_with_price": values["missing_msrp_with_price"],
        }

    assumed_worth_total = summary["worth_total"] + summary["minimum_worth_total"]

    return render_template(
        "financial_report.html",
        total_cars=summary["total_cars"],
        msrp_count=summary["msrp_count"],
        price_count=summary["price_count"],
        worth_count=summary["worth_count"],
        missing_msrp_with_price=summary["missing_msrp_with_price"],
        missing_worth=summary["missing_worth"],
        price_total=format_currency(summary["price_total"]) if summary["price_count"] else "-",
        worth_total=format_currency(summary["worth_total"]) if summary["worth_count"] else "-",
        assumed_worth_total=format_currency(assumed_worth_total),
        category_summaries={name: format_summary(values) for name, values in categories.items()},
    )


@main_bp.route("/reports/financial-summary/msrp")
def financial_msrp_summary():
    cars = Car.query.order_by("id").all()
    total_cars = len(cars)
    msrp_total = 0.0
    msrp_count = 0
    categories = {
        "Locomotives": {"count": 0, "msrp_total": 0.0, "msrp_count": 0},
        "Cars": {"count": 0, "msrp_total": 0.0, "msrp_count": 0},
    }

    for car in cars:
        class_is_locomotive = car.car_class.is_locomotive if car.car_class else None
        is_locomotive = (
            car.is_locomotive_override if car.is_locomotive_override is not None else class_is_locomotive
        )
        bucket = categories["Locomotives"] if is_locomotive else categories["Cars"]
        bucket["count"] += 1

        msrp_value = parse_currency(car.msrp)
        if msrp_value is not None:
            msrp_total += msrp_value
            msrp_count += 1
            bucket["msrp_total"] += msrp_value
            bucket["msrp_count"] += 1

    msrp_coverage = None
    if total_cars:
        msrp_coverage = (msrp_count / total_cars) * 100

    def format_msrp_summary(values: dict[str, float | int]) -> dict[str, str | int]:
        coverage = None
        if values["count"]:
            coverage = (values["msrp_count"] / values["count"]) * 100
        return {
            "count": values["count"],
            "msrp_total": format_currency(values["msrp_total"]) if values["msrp_count"] else "-",
            "msrp_count": values["msrp_count"],
            "msrp_coverage": format_percent(coverage),
        }

    return render_template(
        "financial_msrp_report.html",
        total_cars=total_cars,
        msrp_count=msrp_count,
        msrp_total=format_currency(msrp_total) if msrp_count else "-",
        msrp_coverage=format_percent(msrp_coverage),
        category_summaries={name: format_msrp_summary(values) for name, values in categories.items()},
    )


@main_bp.route("/reports/worth-by-category")
def worth_by_category_report():
    cars = Car.query.order_by("id").all()
    railroads: dict[str, dict[str, float | int]] = {}
    classes: dict[str, dict[str, float | int]] = {}

    def ensure_bucket(container: dict[str, dict[str, float | int]], key: str) -> dict[str, float | int]:
        if key not in container:
            container[key] = {
                "count": 0,
                "worth_total": 0.0,
                "worth_count": 0,
                "missing_worth": 0,
            }
        return container[key]

    for car in cars:
        railroad_label = "Unknown Railroad"
        if car.railroad:
            railroad_label = car.railroad.reporting_mark or car.railroad.name or "Unknown Railroad"
        elif car.reporting_mark_override:
            railroad_label = car.reporting_mark_override
        class_label = car.car_class.code if car.car_class and car.car_class.code else "Unclassified"

        msrp_value = parse_currency(car.msrp)
        price_value = parse_currency(car.price)
        worth_value = select_worth_value(msrp_value, price_value)

        for container, key in ((railroads, railroad_label), (classes, class_label)):
            bucket = ensure_bucket(container, key)
            bucket["count"] += 1
            if worth_value is None:
                bucket["missing_worth"] += 1
            else:
                bucket["worth_total"] += worth_value
                bucket["worth_count"] += 1

    def format_bucket(values: dict[str, float | int]) -> dict[str, str | int]:
        missing_percent = None
        if values["count"]:
            missing_percent = (values["missing_worth"] / values["count"]) * 100
        return {
            "count": values["count"],
            "worth_total": format_currency(values["worth_total"]) if values["worth_count"] else "-",
            "missing_worth": values["missing_worth"],
            "missing_percent": format_percent(missing_percent),
        }

    railroad_rows = [
        {"label": label, **format_bucket(values)} for label, values in sorted(railroads.items())
    ]
    class_rows = [{"label": label, **format_bucket(values)} for label, values in sorted(classes.items())]

    return render_template(
        "worth_by_category_report.html",
        railroad_rows=railroad_rows,
        class_rows=class_rows,
    )


@main_bp.route("/reports/missing-worth")
def missing_worth_report():
    cars = Car.query.order_by("id").all()
    missing = []
    for car in cars:
        msrp_value = parse_currency(car.msrp)
        price_value = parse_currency(car.price)
        worth_value = select_worth_value(msrp_value, price_value)
        if worth_value is None:
            missing.append(car)
    missing.sort(
        key=lambda car: (
            car.railroad.reporting_mark if car.railroad else (car.reporting_mark_override or ""),
            car.car_number or "",
        )
    )
    return render_template("missing_worth_report.html", cars=missing, total=len(missing))


@main_bp.route("/reports/repairs")
def repairs_report():
    cars = Car.query.order_by("id").all()
    repairs = []
    for car in cars:
        if car.repairs_required and car.repairs_required.strip():
            repairs.append(car)
    return render_template("repairs_report.html", cars=repairs, total=len(repairs))


@main_bp.route("/reports/conflicts")
def conflict_report():
    cars = Car.query.order_by("id").all()
    car_key_map = {}
    dcc_map = {}
    for car in cars:
        reporting_mark = (
            car.railroad.reporting_mark if car.railroad else (car.reporting_mark_override or "")
        )
        car_number = car.car_number or ""
        if car_number:
            key = f"{reporting_mark} {car_number}".strip()
            car_key_map.setdefault(key, []).append(car)
        if car.dcc_id:
            dcc_map.setdefault(car.dcc_id, []).append(car)

    car_conflicts = {key: items for key, items in car_key_map.items() if len(items) > 1}
    dcc_conflicts = {key: items for key, items in dcc_map.items() if len(items) > 1}

    return render_template(
        "conflicts_report.html",
        car_conflicts=car_conflicts,
        dcc_conflicts=dcc_conflicts,
    )


@main_bp.route("/railroads")
def railroads():
    sort = (request.args.get("sort") or "alpha").strip().lower()
    if sort not in {"alpha", "count"}:
        sort = "alpha"

    railroads = Railroad.query.all()
    railroad_counts: dict[int, int] = {}
    for car in Car.query.all():
        if car.railroad_id:
            railroad_counts[car.railroad_id] = railroad_counts.get(car.railroad_id, 0) + 1

    for railroad in railroads:
        railroad.inventory_count = railroad_counts.get(railroad.id, 0)

    if sort == "count":
        railroads.sort(
            key=lambda railroad: (
                -railroad.inventory_count,
                (railroad.name or railroad.reporting_mark or "").lower(),
            )
        )
    else:
        railroads.sort(
            key=lambda railroad: (
                (railroad.name or railroad.reporting_mark or "").lower(),
                (railroad.reporting_mark or "").lower(),
            )
        )

    page_size = get_page_size()
    page = get_page_number()
    route_params = {"sort": sort} if sort != "alpha" else {}
    paged_railroads, pagination = paginate_list(railroads, page, page_size, "main.railroads", route_params)
    return render_template(
        "railroads.html",
        railroads=paged_railroads,
        pagination=pagination,
        current_sort=sort,
    )


@main_bp.route("/locations")
def locations():
    locations = Location.query.order_by("name").all()
    page_size = get_page_size()
    page = get_page_number()
    paged_locations, pagination = paginate_list(locations, page, page_size, "main.locations", {})
    return render_template("locations.html", locations=paged_locations, pagination=pagination)


@main_bp.route("/locations/new", methods=["GET", "POST"])
def location_new():
    if request.method == "POST":
        location = Location(name=request.form.get("name", "").strip())
        owner_error = apply_owner_form(location, request.form)
        if owner_error:
            return owner_error, 400
        location.location_type = request.form.get("location_type", "").strip()
        parent_id = request.form.get("parent_id", "").strip()
        if parent_id and parent_id.isdigit():
            location.parent = Location.query.get(int(parent_id))
        external_length_value = request.form.get("external_length_value", "").strip()
        external_length_unit = request.form.get("external_length_unit", "").strip().lower()
        if external_length_value and external_length_unit:
            location.external_length = f"{external_length_value} {external_length_unit}"
        else:
            location.external_length = external_length_value or None
        external_width_value = request.form.get("external_width_value", "").strip()
        external_width_unit = request.form.get("external_width_unit", "").strip().lower()
        if external_width_value and external_width_unit:
            location.external_width = f"{external_width_value} {external_width_unit}"
        else:
            location.external_width = external_width_value or None
        external_height_value = request.form.get("external_height_value", "").strip()
        external_height_unit = request.form.get("external_height_unit", "").strip().lower()
        if external_height_value and external_height_unit:
            location.external_height = f"{external_height_value} {external_height_unit}"
        else:
            location.external_height = external_height_value or None
        external_weight_value = request.form.get("external_weight_value", "").strip()
        external_weight_unit = request.form.get("external_weight_unit", "").strip().lower()
        if external_weight_value and external_weight_unit:
            location.external_weight = f"{external_weight_value} {external_weight_unit}"
        else:
            location.external_weight = external_weight_value or None
        flat_length_value = request.form.get("flat_length_value", "").strip()
        flat_length_unit = request.form.get("flat_length_unit", "").strip().lower()
        if flat_length_value and flat_length_unit:
            location.flat_length = f"{flat_length_value} {flat_length_unit}"
        else:
            location.flat_length = flat_length_value or None
        flat_rows = request.form.get("flat_rows", "").strip()
        location.flat_rows = int(flat_rows) if flat_rows.isdigit() else None
        flat_height_value = request.form.get("flat_height_value", "").strip()
        flat_height_unit = request.form.get("flat_height_unit", "").strip().lower()
        if flat_height_value and flat_height_unit:
            location.flat_height = f"{flat_height_value} {flat_height_unit}"
        else:
            location.flat_height = flat_height_value or None
        flat_row_width_value = request.form.get("flat_row_width_value", "").strip()
        flat_row_width_unit = request.form.get("flat_row_width_unit", "").strip().lower()
        if flat_row_width_value and flat_row_width_unit:
            location.flat_row_width = f"{flat_row_width_value} {flat_row_width_unit}"
        else:
            location.flat_row_width = flat_row_width_value or None
        flat_weight_value = request.form.get("flat_weight_value", "").strip()
        flat_weight_unit = request.form.get("flat_weight_unit", "").strip().lower()
        if flat_weight_value and flat_weight_unit:
            location.flat_weight = f"{flat_weight_value} {flat_weight_unit}"
        else:
            location.flat_weight = flat_weight_value or None
        location.flat_scale = request.form.get("flat_scale", "").strip() or None
        location.flat_gauge = request.form.get("flat_gauge", "").strip() or None
        db.session.add(location)
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.location_detail", location_id=location.id))
    locations = Location.query.order_by("name").all()
    location_types = current_app.config.get("LOCATION_TYPES", [])
    flat_length_unit = get_default_length_unit()
    flat_weight_unit = get_default_weight_unit()
    return render_template(
        "location_form.html",
        location=None,
        owner_username=(g.current_user.username if getattr(g, "current_user", None) else ""),
        enabled_users=get_enabled_users(),
        locations=locations,
        descendant_ids=set(),
        location_types=location_types,
        external_length_value="",
        external_length_unit=flat_length_unit,
        external_width_value="",
        external_width_unit=flat_length_unit,
        external_height_value="",
        external_height_unit=flat_length_unit,
        external_weight_value="",
        external_weight_unit=flat_weight_unit,
        flat_length_value="",
        flat_length_unit=flat_length_unit,
        flat_rows_value="",
        flat_height_value="",
        flat_height_unit=flat_length_unit,
        flat_row_width_value="",
        flat_row_width_unit=flat_length_unit,
        flat_weight_value="",
        flat_weight_unit=flat_weight_unit,
        flat_scale_value="",
        flat_gauge_value="",
        scale_options=get_scale_options(),
        gauge_options=get_gauge_options(),
    )


@main_bp.route("/railroads/<int:railroad_id>")
def railroad_detail(railroad_id: int):
    railroad = Railroad.query.get_or_404(railroad_id)
    cars = Car.query.filter_by(railroad_id=railroad.id).order_by("id", reverse=True).all()
    page_size = get_page_size()
    page = get_page_number()
    paged_cars, cars_pagination = paginate_list(
        cars,
        page,
        page_size,
        "main.railroad_detail",
        {"railroad_id": railroad.id},
    )
    return render_template(
        "railroad_detail.html",
        railroad=railroad,
        cars=paged_cars,
        cars_pagination=cars_pagination,
    )


@main_bp.route("/railroads/<int:railroad_id>/delete", methods=["POST"])
def railroad_delete(railroad_id: int):
    railroad = Railroad.query.get_or_404(railroad_id)
    if Car.query.filter_by(railroad_id=railroad.id).count() > 0:
        return "Cannot delete railroad with cars assigned.", 400
    db.session.delete(railroad)
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.railroads"))


@main_bp.route("/railroads/<int:railroad_id>/edit", methods=["GET", "POST"])
def railroad_edit(railroad_id: int):
    railroad = Railroad.query.get_or_404(railroad_id)
    if request.method == "POST":
        railroad.reporting_mark = request.form.get("reporting_mark", "").strip()
        railroad.name = request.form.get("name", "").strip()
        railroad.start_date = request.form.get("start_date", "").strip()
        railroad.end_date = request.form.get("end_date", "").strip()
        railroad.merged_into = request.form.get("merged_into", "").strip()
        railroad.merged_from = request.form.get("merged_from", "").strip()
        railroad.notes = request.form.get("notes", "").strip()
        scheme_ids = request.form.getlist("color_scheme_id")
        scheme_descriptions = request.form.getlist("color_scheme_description")
        scheme_starts = request.form.getlist("color_scheme_start")
        scheme_ends = request.form.getlist("color_scheme_end")
        scheme_colors = request.form.getlist("color_scheme_colors")
        existing_schemes = {str(scheme.id): scheme for scheme in railroad.color_schemes}
        kept_scheme_ids = set()
        new_schemes = []
        for index, description in enumerate(scheme_descriptions):
            description = description.strip()
            scheme_id = scheme_ids[index] if index < len(scheme_ids) else ""
            if not description:
                continue
            start_date = scheme_starts[index].strip() if index < len(scheme_starts) else ""
            end_date = scheme_ends[index].strip() if index < len(scheme_ends) else ""
            colors = normalize_color_list(scheme_colors[index]) if index < len(scheme_colors) else ""
            if scheme_id and scheme_id in existing_schemes:
                scheme = existing_schemes[scheme_id]
                scheme.description = description
                scheme.start_date = start_date or None
                scheme.end_date = end_date or None
                scheme.colors = colors or None
                kept_scheme_ids.add(scheme_id)
            else:
                new_schemes.append(
                    RailroadColorScheme(
                        description=description,
                        start_date=start_date or None,
                        end_date=end_date or None,
                        colors=colors or None,
                    )
                )
        for scheme_id, scheme in existing_schemes.items():
            if scheme_id not in kept_scheme_ids:
                db.session.delete(scheme)
        for scheme in new_schemes:
            scheme.railroad_id = railroad.id
            db.session.add(scheme)

        logo_ids = request.form.getlist("logo_id")
        logo_descriptions = request.form.getlist("logo_description")
        logo_starts = request.form.getlist("logo_start")
        logo_ends = request.form.getlist("logo_end")
        logo_existing_paths = request.form.getlist("logo_existing_path")
        representative_index = request.form.get("representative_logo_index", "").strip()
        existing_logos = {str(logo.id): logo for logo in railroad.logos}
        kept_logo_ids = set()
        row_logos: list[RailroadLogo | None] = []
        for index, description in enumerate(logo_descriptions):
            logo_id = logo_ids[index] if index < len(logo_ids) else ""
            start_date = logo_starts[index].strip() if index < len(logo_starts) else ""
            end_date = logo_ends[index].strip() if index < len(logo_ends) else ""
            existing_path = logo_existing_paths[index].strip() if index < len(logo_existing_paths) else ""
            file_storage = request.files.get(f"logo_image_{index}")
            description = description.strip()
            row_has_data = bool(
                description
                or start_date
                or end_date
                or existing_path
                or (file_storage and file_storage.filename)
            )
            if not row_has_data:
                row_logos.append(None)
                continue
            new_path = save_logo_file(file_storage, railroad.id)
            logo = existing_logos.get(logo_id) if logo_id else None
            if not logo:
                logo = RailroadLogo(railroad_id=railroad.id)
                db.session.add(logo)
            logo.description = description
            logo.start_date = start_date or None
            logo.end_date = end_date or None
            if new_path:
                if logo.image_path and logo.image_path.startswith("uploads/railroad-logos/"):
                    old_path = os.path.join(current_app.static_folder or "", logo.image_path)
                    if os.path.exists(old_path):
                        os.remove(old_path)
                logo.image_path = new_path
            else:
                logo.image_path = existing_path or None
            if logo_id:
                kept_logo_ids.add(logo_id)
            row_logos.append(logo)
        for logo_id, logo in existing_logos.items():
            if logo_id not in kept_logo_ids:
                db.session.delete(logo)

        slogan_ids = request.form.getlist("slogan_id")
        slogan_descriptions = request.form.getlist("slogan_description")
        slogan_texts = request.form.getlist("slogan_text")
        slogan_starts = request.form.getlist("slogan_start")
        slogan_ends = request.form.getlist("slogan_end")
        existing_slogans = {str(slogan.id): slogan for slogan in railroad.slogans}
        kept_slogan_ids = set()
        new_slogans = []
        for index, description in enumerate(slogan_descriptions):
            description = description.strip()
            slogan_id = slogan_ids[index] if index < len(slogan_ids) else ""
            slogan_text = slogan_texts[index].strip() if index < len(slogan_texts) else ""
            start_date = slogan_starts[index].strip() if index < len(slogan_starts) else ""
            end_date = slogan_ends[index].strip() if index < len(slogan_ends) else ""
            if not (description or slogan_text or start_date or end_date):
                continue
            if slogan_id and slogan_id in existing_slogans:
                slogan = existing_slogans[slogan_id]
                slogan.description = description
                slogan.slogan_text = slogan_text or None
                slogan.start_date = start_date or None
                slogan.end_date = end_date or None
                kept_slogan_ids.add(slogan_id)
            else:
                new_slogans.append(
                    RailroadSlogan(
                        description=description,
                        slogan_text=slogan_text or None,
                        start_date=start_date or None,
                        end_date=end_date or None,
                    )
                )
        for slogan_id, slogan in existing_slogans.items():
            if slogan_id not in kept_slogan_ids:
                db.session.delete(slogan)
        for slogan in new_slogans:
            slogan.railroad_id = railroad.id
            db.session.add(slogan)

        db.session.flush()
        if representative_index.isdigit():
            rep_index = int(representative_index)
            representative_logo = row_logos[rep_index] if rep_index < len(row_logos) else None
            railroad.representative_logo_id = representative_logo.id if representative_logo else None
        else:
            railroad.representative_logo_id = None
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.railroad_detail", railroad_id=railroad.id))
    return render_template("railroad_form.html", railroad=railroad)


@main_bp.route("/tools")
def tools():
    return render_template("tools.html")


def attach_location_refs(items: list) -> None:
    location_ids = {item.location_id for item in items if getattr(item, "location_id", None)}
    locations = {loc_id: Location.query.get(loc_id) for loc_id in location_ids}
    for item in items:
        loc_id = getattr(item, "location_id", None)
        if loc_id:
            item._location_ref = locations.get(loc_id)


def parse_quantity(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    return None


def apply_tool_form(tool: ToolItem, form) -> str | None:
    name = form.get("name", "").strip()
    location_id = form.get("location_id", "").strip()
    if not name:
        return "Tool name is required."
    if not location_id.isdigit():
        return "Location is required."
    owner_error = apply_owner_form(tool, form)
    if owner_error:
        return owner_error
    tool.name = name
    tool.description = form.get("description", "").strip()
    tool.brand = form.get("brand", "").strip()
    tool.quantity = parse_quantity(form.get("quantity", ""))
    tool.location_id = int(location_id)
    return None


def apply_part_form(part: PartItem, form) -> str | None:
    name = form.get("name", "").strip()
    location_id = form.get("location_id", "").strip()
    if not name:
        return "Part name is required."
    if not location_id.isdigit():
        return "Location is required."
    owner_error = apply_owner_form(part, form)
    if owner_error:
        return owner_error
    part.name = name
    part.description = form.get("description", "").strip()
    part.brand = form.get("brand", "").strip()
    part.upc = form.get("upc", "").strip()
    part.quantity = parse_quantity(form.get("quantity", ""))
    part.location_id = int(location_id)
    return None


def redirect_next(fallback: str):
    next_url = request.form.get("next", "").strip() or request.args.get("next", "").strip()
    return redirect(next_url or fallback)


@main_bp.route("/tool-inventory")
def tool_inventory():
    tools = ToolItem.query.order_by("name").all()
    attach_location_refs(tools)
    return render_template("tool_inventory.html", tools=tools)


@main_bp.route("/tool-inventory/new", methods=["GET", "POST"])
def tool_new():
    locations = Location.query.order_by("name").all()
    enabled_users = get_enabled_users()
    preset_location_id = request.args.get("location_id", "").strip()
    next_url = request.args.get("next", "").strip()
    if request.method == "POST":
        tool = ToolItem()
        error = apply_tool_form(tool, request.form)
        if error:
            return error, 400
        db.session.add(tool)
        db.session.commit()
        ensure_db_backup()
        return redirect_next(url_for("main.tool_inventory"))
    return render_template(
        "tool_form.html",
        tool=None,
        owner_username=(g.current_user.username if getattr(g, "current_user", None) else ""),
        enabled_users=enabled_users,
        locations=locations,
        preset_location_id=preset_location_id,
        form_action=url_for("main.tool_new", location_id=preset_location_id, next=next_url),
        next_url=next_url,
    )


@main_bp.route("/tool-inventory/<int:tool_id>/edit", methods=["GET", "POST"])
def tool_edit(tool_id: int):
    tool = ToolItem.query.get_or_404(tool_id)
    locations = Location.query.order_by("name").all()
    enabled_users = get_enabled_users()
    next_url = request.args.get("next", "").strip()
    if request.method == "POST":
        error = apply_tool_form(tool, request.form)
        if error:
            return error, 400
        db.session.commit()
        ensure_db_backup()
        return redirect_next(url_for("main.tool_inventory"))
    return render_template(
        "tool_form.html",
        tool=tool,
        owner_username=(tool.owner.username if tool.owner else ""),
        enabled_users=enabled_users,
        locations=locations,
        preset_location_id=str(tool.location_id or ""),
        form_action=url_for("main.tool_edit", tool_id=tool.id, next=next_url),
        next_url=next_url,
    )


@main_bp.route("/tool-inventory/<int:tool_id>/delete", methods=["POST"])
def tool_delete(tool_id: int):
    tool = ToolItem.query.get_or_404(tool_id)
    db.session.delete(tool)
    db.session.commit()
    ensure_db_backup()
    return redirect_next(url_for("main.tool_inventory"))


@main_bp.route("/parts-inventory")
def parts_inventory():
    parts = PartItem.query.order_by("name").all()
    attach_location_refs(parts)
    return render_template("parts_inventory.html", parts=parts)


@main_bp.route("/parts-inventory/new", methods=["GET", "POST"])
def part_new():
    locations = Location.query.order_by("name").all()
    enabled_users = get_enabled_users()
    preset_location_id = request.args.get("location_id", "").strip()
    next_url = request.args.get("next", "").strip()
    if request.method == "POST":
        part = PartItem()
        error = apply_part_form(part, request.form)
        if error:
            return error, 400
        db.session.add(part)
        db.session.commit()
        ensure_db_backup()
        return redirect_next(url_for("main.parts_inventory"))
    return render_template(
        "part_form.html",
        part=None,
        owner_username=(g.current_user.username if getattr(g, "current_user", None) else ""),
        enabled_users=enabled_users,
        locations=locations,
        preset_location_id=preset_location_id,
        form_action=url_for("main.part_new", location_id=preset_location_id, next=next_url),
        next_url=next_url,
    )


@main_bp.route("/parts-inventory/<int:part_id>/edit", methods=["GET", "POST"])
def part_edit(part_id: int):
    part = PartItem.query.get_or_404(part_id)
    locations = Location.query.order_by("name").all()
    enabled_users = get_enabled_users()
    next_url = request.args.get("next", "").strip()
    if request.method == "POST":
        error = apply_part_form(part, request.form)
        if error:
            return error, 400
        db.session.commit()
        ensure_db_backup()
        return redirect_next(url_for("main.parts_inventory"))
    return render_template(
        "part_form.html",
        part=part,
        owner_username=(part.owner.username if part.owner else ""),
        enabled_users=enabled_users,
        locations=locations,
        preset_location_id=str(part.location_id or ""),
        form_action=url_for("main.part_edit", part_id=part.id, next=next_url),
        next_url=next_url,
    )


@main_bp.route("/parts-inventory/<int:part_id>/delete", methods=["POST"])
def part_delete(part_id: int):
    part = PartItem.query.get_or_404(part_id)
    db.session.delete(part)
    db.session.commit()
    ensure_db_backup()
    return redirect_next(url_for("main.parts_inventory"))


@main_bp.route("/tools/aar-plate-viewer")
def aar_plate_viewer():
    return render_template("aar_plate_viewer.html")


CONSIST_POWER_TYPES = ["steam", "diesel", "electric", "gas turbine", "unpowered", "other"]
DEFAULT_CONSIST_LOCO_COUNT = 2
DEFAULT_CONSIST_CAR_COUNT = 10


def normalize_car_ids(values: list[str]) -> list[int]:
    seen: set[int] = set()
    car_ids: list[int] = []
    for value in values:
        value = value.strip()
        if not value or not value.isdigit():
            continue
        car_id = int(value)
        if car_id in seen:
            continue
        seen.add(car_id)
        car_ids.append(car_id)
    return car_ids


def is_locomotive(car: Car) -> bool:
    if car.is_locomotive_override is not None:
        return bool(car.is_locomotive_override)
    if car.car_class and car.car_class.is_locomotive is not None:
        return bool(car.car_class.is_locomotive)
    return False


def matches_power_type(car: Car, power_type: str) -> bool:
    if not power_type:
        return True
    target = power_type.lower()
    candidates = []
    if car.power_type_override:
        candidates.append(car.power_type_override)
    if car.car_class and car.car_class.power_type:
        candidates.append(car.car_class.power_type)
    return any(target == candidate.lower() for candidate in candidates)


def matches_era(car: Car, era: str) -> bool:
    if not era:
        return True
    if not car.car_class or not car.car_class.era:
        return False
    target_range = parse_era_range(era)
    class_range = parse_era_range(car.car_class.era)
    if target_range and class_range:
        target_start, target_end = target_range
        class_start, class_end = class_range
        if class_end is None:
            class_end = target_end
        if target_end is None:
            target_end = class_end
        return not (
            class_start is None
            or target_start is None
            or (class_end is not None and target_start > class_end)
            or (target_end is not None and class_start > target_end)
        )
    return era.lower() in car.car_class.era.lower()


def parse_era_range(value: str) -> tuple[int | None, int | None] | None:
    if not value:
        return None
    text = value.lower()
    years = [int(year) for year in re.findall(r"\b(\d{4})\b", text)]
    if years:
        if "present" in text or "current" in text or "today" in text:
            return years[0], None
        if len(years) >= 2:
            start, end = years[0], years[1]
            if end < start:
                start, end = end, start
            return start, end
        return years[0], years[0]
    decade_match = re.search(r"\b(\d{3})0s\b", text)
    if decade_match:
        start = int(decade_match.group(1) + "0")
        return start, start + 9
    return None


def pick_cars(candidates: list[Car], count: int, selected_ids: set[int]) -> list[Car]:
    picks: list[Car] = []
    for car in candidates:
        if car.id is None or car.id in selected_ids:
            continue
        selected_ids.add(car.id)
        picks.append(car)
        if len(picks) >= count:
            break
    return picks


def build_wizard_consist(era: str, power_type: str, primary_railroad_id: int) -> list[Car]:
    all_cars = Car.query.order_by("id").all()
    primary_cars = [car for car in all_cars if car.railroad_id == primary_railroad_id]
    if not primary_cars:
        return []
    selected_ids: set[int] = set()

    locomotives = [car for car in primary_cars if is_locomotive(car)]
    filtered_locos = [car for car in locomotives if matches_power_type(car, power_type) and matches_era(car, era)]
    if not filtered_locos:
        return []
    chosen_locos = pick_cars(filtered_locos, DEFAULT_CONSIST_LOCO_COUNT, selected_ids)
    if len(chosen_locos) < DEFAULT_CONSIST_LOCO_COUNT:
        chosen_locos += pick_cars(locomotives, DEFAULT_CONSIST_LOCO_COUNT - len(chosen_locos), selected_ids)

    rolling_stock = [car for car in all_cars if not is_locomotive(car)]
    same_era_cars = [car for car in rolling_stock if matches_era(car, era)]
    primary_era_cars = [car for car in same_era_cars if car.railroad_id == primary_railroad_id]
    other_era_cars = [car for car in same_era_cars if car.railroad_id != primary_railroad_id]
    other_target = max(0, int(round(DEFAULT_CONSIST_CAR_COUNT * 0.2)))
    primary_target = max(0, DEFAULT_CONSIST_CAR_COUNT - other_target)
    chosen_cars: list[Car] = []
    chosen_cars += pick_cars(primary_era_cars, primary_target, selected_ids)
    chosen_cars += pick_cars(other_era_cars, other_target, selected_ids)
    if len(chosen_cars) < DEFAULT_CONSIST_CAR_COUNT:
        chosen_cars += pick_cars(primary_era_cars, DEFAULT_CONSIST_CAR_COUNT - len(chosen_cars), selected_ids)
    if len(chosen_cars) < DEFAULT_CONSIST_CAR_COUNT:
        chosen_cars += pick_cars(same_era_cars, DEFAULT_CONSIST_CAR_COUNT - len(chosen_cars), selected_ids)

    return chosen_locos + chosen_cars


def build_consist_name(railroad: Railroad | None, power_type: str, era: str) -> str:
    segments: list[str] = []
    if railroad:
        segments.append(railroad.name or railroad.reporting_mark or "Unknown Railroad")
    if power_type:
        segments.append(power_type.title())
    segments.append("Consist")
    if era:
        segments.append(f"({era})")
    return " ".join(segments)


@main_bp.route("/tools/consist-creation")
def consist_creation():
    return redirect(url_for("main.consists"))


@main_bp.route("/consists")
def consists():
    consists = Consist.query.order_by("name").all()
    page_size = get_page_size()
    page = get_page_number()
    paged_consists, pagination = paginate_list(consists, page, page_size, "main.consists", {})
    return render_template("consists.html", consists=paged_consists, pagination=pagination)


@main_bp.route("/consists/new", methods=["GET", "POST"])
def consist_new():
    railroads = Railroad.query.order_by("reporting_mark").all()
    cars = Car.query.order_by("id").all()
    form_errors: list[str] = []
    form_data = {"name": "", "era": "", "power_type": "", "primary_railroad_id": "", "notes": ""}
    selected_ids: list[int] = []

    if request.method == "POST":
        form_data = {
            "name": request.form.get("name", "").strip(),
            "era": request.form.get("era", "").strip(),
            "power_type": request.form.get("power_type", "").strip(),
            "primary_railroad_id": request.form.get("primary_railroad_id", "").strip(),
            "notes": request.form.get("notes", "").strip(),
        }
        selected_ids = normalize_car_ids(request.form.getlist("car_ids"))
        primary_railroad_id = int(form_data["primary_railroad_id"]) if form_data["primary_railroad_id"].isdigit() else None
        if not selected_ids:
            form_errors.append("Select at least one car for the consist.")
        if not form_errors:
            consist_name = form_data["name"] or f"Consist {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            consist = Consist(
                name=consist_name,
                era=form_data["era"] or None,
                power_type=form_data["power_type"] or None,
                primary_railroad_id=primary_railroad_id,
                car_ids=selected_ids,
                notes=form_data["notes"] or None,
            )
            db.session.add(consist)
            db.session.commit()
            ensure_db_backup()
            return redirect(url_for("main.consist_detail", consist_id=consist.id))

    return render_template(
        "consist_form.html",
        consist=None,
        form_data=form_data,
        railroads=railroads,
        cars=cars,
        form_errors=form_errors,
        selected_ids=selected_ids,
        power_type_options=CONSIST_POWER_TYPES,
        form_title="Create Manual Consist",
        form_action=url_for("main.consist_new"),
        submit_label="Save Consist",
        cancel_url=url_for("main.consists"),
    )


@main_bp.route("/consists/wizard", methods=["GET", "POST"])
def consist_wizard():
    railroads = Railroad.query.order_by("reporting_mark").all()
    form_errors: list[str] = []
    wizard_form = {"name": "", "era": "", "power_type": "", "primary_railroad_id": ""}
    if request.method == "POST":
        wizard_form = {
            "name": request.form.get("name", "").strip(),
            "era": request.form.get("era", "").strip(),
            "power_type": request.form.get("power_type", "").strip(),
            "primary_railroad_id": request.form.get("primary_railroad_id", "").strip(),
        }
        if not wizard_form["era"]:
            form_errors.append("Enter a time era for the wizard.")
        if not wizard_form["power_type"]:
            form_errors.append("Choose a power type for the wizard.")
        if not wizard_form["primary_railroad_id"].isdigit():
            form_errors.append("Choose a primary railroad for the wizard.")
        if not form_errors:
            primary_railroad_id = int(wizard_form["primary_railroad_id"])
            primary_cars = [car for car in Car.query.order_by("id").all() if car.railroad_id == primary_railroad_id]
            primary_locos = [car for car in primary_cars if is_locomotive(car)]
            filtered_locos = [
                car
                for car in primary_cars
                if is_locomotive(car)
                and matches_power_type(car, wizard_form["power_type"])
                and matches_era(car, wizard_form["era"])
            ]
            if not filtered_locos:
                form_errors.append("No locomotives found for that power type and era on the primary railroad.")
            else:
                selected_cars = build_wizard_consist(
                    wizard_form["era"],
                    wizard_form["power_type"],
                    primary_railroad_id,
                )
                if not selected_cars:
                    form_errors.append("No matching cars found for the wizard inputs.")
                else:
                    railroad = Railroad.query.get(primary_railroad_id)
                    consist_name = wizard_form["name"] or build_consist_name(
                        railroad, wizard_form["power_type"], wizard_form["era"]
                    )
                    consist = Consist(
                        name=consist_name,
                        era=wizard_form["era"],
                        power_type=wizard_form["power_type"],
                        primary_railroad_id=primary_railroad_id,
                        car_ids=[car.id for car in selected_cars if car.id],
                    )
                    db.session.add(consist)
                    db.session.commit()
                    ensure_db_backup()
                    return redirect(url_for("main.consist_detail", consist_id=consist.id))

    return render_template(
        "consist_wizard.html",
        railroads=railroads,
        wizard_form=wizard_form,
        form_errors=form_errors,
        power_type_options=CONSIST_POWER_TYPES,
    )


@main_bp.route("/consists/<int:consist_id>")
def consist_detail(consist_id: int):
    consist = Consist.query.get_or_404(consist_id)
    cars = consist.cars
    return render_template("consist_detail.html", consist=consist, cars=cars)


@main_bp.route("/consists/<int:consist_id>/edit", methods=["GET", "POST"])
def consist_edit(consist_id: int):
    consist = Consist.query.get_or_404(consist_id)
    railroads = Railroad.query.order_by("reporting_mark").all()
    cars = Car.query.order_by("id").all()
    form_errors: list[str] = []
    form_data = {
        "name": consist.name or "",
        "era": consist.era or "",
        "power_type": consist.power_type or "",
        "primary_railroad_id": str(consist.primary_railroad_id or ""),
        "notes": consist.notes or "",
    }
    selected_ids = consist.car_ids or []

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        era = request.form.get("era", "").strip()
        power_type = request.form.get("power_type", "").strip()
        primary_railroad_id_value = request.form.get("primary_railroad_id", "").strip()
        notes = request.form.get("notes", "").strip()
        selected_ids = normalize_car_ids(request.form.getlist("car_ids"))
        primary_railroad_id = int(primary_railroad_id_value) if primary_railroad_id_value.isdigit() else None

        if not selected_ids:
            form_errors.append("Select at least one car for the consist.")

        if not form_errors:
            consist.name = name or None
            consist.era = era or None
            consist.power_type = power_type or None
            consist.primary_railroad_id = primary_railroad_id
            consist.notes = notes or None
            consist.car_ids = selected_ids
            db.session.commit()
            ensure_db_backup()
            return redirect(url_for("main.consist_detail", consist_id=consist.id))
        form_data = {
            "name": name,
            "era": era,
            "power_type": power_type,
            "primary_railroad_id": primary_railroad_id_value,
            "notes": notes,
        }

    return render_template(
        "consist_form.html",
        consist=consist,
        form_data=form_data,
        railroads=railroads,
        cars=cars,
        form_errors=form_errors,
        selected_ids=selected_ids,
        power_type_options=CONSIST_POWER_TYPES,
        form_title="Edit Consist",
        form_action=url_for("main.consist_edit", consist_id=consist.id),
        submit_label="Save Changes",
        cancel_url=url_for("main.consist_detail", consist_id=consist.id),
    )


@main_bp.route("/consists/<int:consist_id>/delete", methods=["POST"])
def consist_delete(consist_id: int):
    consist = Consist.query.get_or_404(consist_id)
    db.session.delete(consist)
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.consists"))


def draw_wrapped_text(draw, text, x, y, max_width, font, line_height) -> None:
    if not text:
        return
    words = text.split()
    line = []
    for word in words:
        test = " ".join(line + [word]).strip()
        if not test:
            continue
        width = draw.textlength(test, font=font)
        if width <= max_width or not line:
            line.append(word)
        else:
            draw.text((x, y), " ".join(line), fill="#111111", font=font)
            y += line_height
            line = [word]
    if line:
        draw.text((x, y), " ".join(line), fill="#111111", font=font)


def wrap_text_lines(draw, text, max_width, font) -> list[str]:
    if not text:
        return []
    words = text.split()
    lines = []
    line = []
    for word in words:
        test = " ".join(line + [word]).strip()
        if not test:
            continue
        if draw.textlength(test, font=font) <= max_width or not line:
            line.append(word)
        else:
            lines.append(" ".join(line))
            line = [word]
    if line:
        lines.append(" ".join(line))
    return lines


def draw_centered_text(draw, text, x, y, width, font) -> None:
    if not text:
        return
    text_width = draw.textlength(text, font=font)
    draw.text((x + max((width - text_width) / 2, 0), y), text, fill="#111111", font=font)


CODE128_PATTERNS = [
    "212222",
    "222122",
    "222221",
    "121223",
    "121322",
    "131222",
    "122213",
    "122312",
    "132212",
    "221213",
    "221312",
    "231212",
    "112232",
    "122132",
    "122231",
    "113222",
    "123122",
    "123221",
    "223211",
    "221132",
    "221231",
    "213212",
    "223112",
    "312131",
    "311222",
    "321122",
    "321221",
    "312212",
    "322112",
    "322211",
    "212123",
    "212321",
    "232121",
    "111323",
    "131123",
    "131321",
    "112313",
    "132113",
    "132311",
    "211313",
    "231113",
    "231311",
    "112133",
    "112331",
    "132131",
    "113123",
    "113321",
    "133121",
    "313121",
    "211331",
    "231131",
    "213113",
    "213311",
    "213131",
    "311123",
    "311321",
    "331121",
    "312113",
    "312311",
    "332111",
    "314111",
    "221411",
    "431111",
    "111224",
    "111422",
    "121124",
    "121421",
    "141122",
    "141221",
    "112214",
    "112412",
    "122114",
    "122411",
    "142112",
    "142211",
    "241211",
    "221114",
    "413111",
    "241112",
    "134111",
    "111242",
    "121142",
    "121241",
    "114212",
    "124112",
    "124211",
    "411212",
    "421112",
    "421211",
    "212141",
    "214121",
    "412121",
    "111143",
    "111341",
    "131141",
    "114113",
    "114311",
    "411113",
    "411311",
    "113141",
    "114131",
    "311141",
    "411131",
    "211412",
    "211214",
    "211232",
    "2331112",
]


def code128_values(text: str) -> list[int]:
    values = []
    for char in text:
        code = ord(char) - 32
        if 0 <= code <= 95:
            values.append(code)
    return values


def draw_code128(
    draw,
    text: str,
    x: int,
    y: int,
    height: int,
    max_width: int,
    center: bool = False,
    module_width_max: int = 2,
) -> int:
    values = code128_values(text)
    if not values or height <= 0 or max_width <= 0:
        return 0
    start_code = 104
    checksum = start_code
    for idx, value in enumerate(values, start=1):
        checksum += value * idx
    checksum %= 103
    codes = [start_code] + values + [checksum, 106]
    patterns = [CODE128_PATTERNS[code] for code in codes]
    modules = sum(sum(int(digit) for digit in pattern) for pattern in patterns)
    if modules <= 0:
        return 0
    module_width = max(1, min(module_width_max, max_width // modules))
    total_width = modules * module_width
    cursor = x
    if center and max_width > total_width:
        cursor = x + (max_width - total_width) // 2
    for pattern in patterns:
        is_bar = True
        for digit in pattern:
            width = int(digit) * module_width
            if is_bar:
                draw.rectangle([cursor, y, cursor + width, y + height], fill="#111111")
            cursor += width
            is_bar = not is_bar
    return total_width


def draw_barcode_with_label(
    draw,
    code: str,
    label_lines: list[str],
    x: int,
    y: int,
    width: int,
    height: int,
    label_font,
    label_line_height: int,
    module_width_max: int = 2,
    barcode_height_scale: float = 1.0,
) -> None:
    label_height = max(len(label_lines) * label_line_height, 0)
    barcode_height = max(int(height - label_height - 2), int(label_line_height * 2))
    barcode_height = min(barcode_height, height)
    barcode_height = max(int(barcode_height * barcode_height_scale), int(label_line_height * 2))
    draw_code128(
        draw,
        code,
        x,
        y,
        barcode_height,
        width,
        center=True,
        module_width_max=module_width_max,
    )
    label_y = y + barcode_height + 2
    for line in label_lines:
        label_width = draw.textlength(line, font=label_font)
        label_x = x + max((width - label_width) / 2, 0)
        draw.text((label_x, label_y), line, fill="#111111", font=label_font)
        label_y += label_line_height


@main_bp.route("/tools/prr-home-shop-repair", methods=["GET", "POST"])
def prr_home_shop_repair():
    if request.method == "POST":
        via = request.form.get("via", "").strip()
        from_value = request.form.get("from_value", "").strip()
        date_value = request.form.get("date_value", "").strip()
        main_defects = request.form.get("main_defects", "").strip()
        car_initials = request.form.get("car_initials", "").strip()
        car_number = request.form.get("car_number", "").strip()
        inspector = request.form.get("inspector", "").strip()
        responsibility = request.form.get("responsibility", "").strip()

        template_path = os.path.join(current_app.root_path, "static", "tools", "PRR-home-shop-defect.png")
        image = Image.open(template_path).convert("RGB")
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", size=48)
        except OSError:
            font = ImageFont.load_default()

        draw_centered_text(draw, via, 360, 1040, 2500, font)
        draw_centered_text(draw, from_value, 460, 1225, 1600, font)
        draw_centered_text(draw, date_value, 2300, 1225, 600, font)
        draw_centered_text(draw, main_defects, 700, 1395, 2100, font)
        draw_centered_text(draw, car_initials, 520, 1575, 1000, font)
        draw_centered_text(draw, car_number, 1400, 1575, 600, font)
        draw_centered_text(draw, inspector, 2200, 1575, 700, font)
        draw_centered_text(draw, responsibility, 1250, 1795, 1200, font)

        output = io.BytesIO()
        image.save(output, format="PNG")
        output.seek(0)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"prr-home-shop-{timestamp}.png"
        generated_dir = os.path.join(current_app.root_path, "static", "tools", "generated")
        os.makedirs(generated_dir, exist_ok=True)
        output_path = os.path.join(generated_dir, filename)
        with open(output_path, "wb") as output_file:
            output_file.write(output.getvalue())

        return render_template(
            "prr_home_shop_form.html",
            image_filename=filename,
            form_data=request.form,
        )

    return render_template("prr_home_shop_form.html", image_filename=None, form_data={})


@main_bp.route("/tools/prr-home-shop-repair/render")
def prr_home_shop_render():
    filename = request.args.get("file", "").strip()
    if not filename:
        return redirect(url_for("main.prr_home_shop_repair"))
    image_path = os.path.join(current_app.root_path, "static", "tools", "generated", filename)
    if not os.path.exists(image_path):
        return redirect(url_for("main.prr_home_shop_repair"))
    return render_template("prr_home_shop_render.html", image_filename=filename)


@main_bp.route("/car-classes")
def car_classes():
    classes = CarClass.query.order_by("code").all()
    car_classes = [c for c in classes if not c.is_locomotive]
    locomotive_classes = [c for c in classes if c.is_locomotive]
    page_size = get_page_size()
    page = get_page_number()
    paged_classes, pagination = paginate_list(car_classes, page, page_size, "main.car_classes", {})
    return render_template("car_classes.html", car_classes=paged_classes, pagination=pagination)


@main_bp.route("/locomotive-classes")
def locomotive_classes():
    classes = CarClass.query.order_by("code").all()
    locomotive_classes = [c for c in classes if c.is_locomotive]
    page_size = get_page_size()
    page = get_page_number()
    paged_classes, pagination = paginate_list(
        locomotive_classes,
        page,
        page_size,
        "main.locomotive_classes",
        {},
    )
    return render_template("locomotive_classes.html", locomotive_classes=paged_classes, pagination=pagination)


@main_bp.route("/loads")
def loads():
    loads = LoadType.query.order_by("name").all()
    page_size = get_page_size()
    page = get_page_number()
    paged_loads, pagination = paginate_list(loads, page, page_size, "main.loads", {})
    return render_template("loads.html", loads=paged_loads, pagination=pagination)


@main_bp.route("/loads/new", methods=["GET", "POST"])
def load_new():
    classes = CarClass.query.order_by("code").all()
    railroads = Railroad.query.order_by("name").all()
    enabled_users = get_enabled_users()
    if request.method == "POST":
        load = LoadType(name=request.form.get("name", "").strip())
        try:
            apply_load_form(load, request.form)
        except ValueError as exc:
            return str(exc), 400
        db.session.add(load)
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.load_detail", load_id=load.id))
    default_length_unit = get_default_length_unit()
    default_weight_unit = get_default_weight_unit()
    return render_template(
        "load_form.html",
        load=None,
        owner_username=(g.current_user.username if getattr(g, "current_user", None) else ""),
        enabled_users=enabled_users,
        classes=classes,
        railroads=railroads,
        length_value="",
        length_unit=default_length_unit,
        width_value="",
        width_unit=default_length_unit,
        height_value="",
        height_unit=default_length_unit,
        weight_value="",
        weight_unit=default_weight_unit,
    )


@main_bp.route("/loads/<int:load_id>")
def load_detail(load_id: int):
    load = LoadType.query.get_or_404(load_id)
    placements = LoadPlacement.query.filter_by(load_id=load.id).all()
    page_size = get_page_size()
    page = get_page_number()
    paged_placements, placements_pagination = paginate_list(
        placements,
        page,
        page_size,
        "main.load_detail",
        {"load_id": load.id},
    )
    return render_template(
        "load_detail.html",
        load=load,
        placements=paged_placements,
        placements_pagination=placements_pagination,
    )


@main_bp.route("/loads/<int:load_id>/edit", methods=["GET", "POST"])
def load_edit(load_id: int):
    load = LoadType.query.get_or_404(load_id)
    classes = CarClass.query.order_by("code").all()
    railroads = Railroad.query.order_by("name").all()
    enabled_users = get_enabled_users()
    if request.method == "POST":
        load.name = request.form.get("name", "").strip()
        try:
            apply_load_form(load, request.form)
        except ValueError as exc:
            return str(exc), 400
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.load_detail", load_id=load.id))
    length_value, length_unit = parse_actual_length(load.length)
    width_value, width_unit = parse_actual_length(load.width)
    height_value, height_unit = parse_actual_length(load.height)
    weight_value, weight_unit = parse_actual_weight(load.weight)
    if not length_unit:
        length_unit = get_default_length_unit()
    if not width_unit:
        width_unit = get_default_length_unit()
    if not height_unit:
        height_unit = get_default_length_unit()
    if not weight_unit:
        weight_unit = get_default_weight_unit()
    return render_template(
        "load_form.html",
        load=load,
        owner_username=(load.owner.username if load.owner else ""),
        enabled_users=enabled_users,
        classes=classes,
        railroads=railroads,
        length_value=length_value,
        length_unit=length_unit,
        width_value=width_value,
        width_unit=width_unit,
        height_value=height_value,
        height_unit=height_unit,
        weight_value=weight_value,
        weight_unit=weight_unit,
    )


@main_bp.route("/loads/<int:load_id>/delete", methods=["POST"])
def load_delete(load_id: int):
    load = LoadType.query.get_or_404(load_id)
    db.session.delete(load)
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.loads"))


@main_bp.route("/loads/<int:load_id>/placements/new", methods=["GET", "POST"])
def load_placement_new(load_id: int):
    load = LoadType.query.get_or_404(load_id)
    cars = Car.query.order_by("id", reverse=True).all()
    locations = Location.query.order_by("name").all()
    if request.method == "POST":
        placement = LoadPlacement(load_id=load.id)
        if not apply_load_placement_form(placement, request.form):
            return "Select a car or location for this load placement.", 400
        db.session.add(placement)
        db.session.commit()
        ensure_db_backup()
        if placement.car_id:
            car = Car.query.get(placement.car_id)
            if car:
                maybe_run_nmra_loaded_weight_check(car, car.actual_weight, car.actual_length, car.scale, True)
        return redirect(url_for("main.load_detail", load_id=load.id))
    return render_template(
        "load_placement_form.html",
        load=load,
        placement=None,
        cars=cars,
        locations=locations,
        preset_car_id=request.args.get("car_id", "").strip(),
        preset_location_id=request.args.get("location_id", "").strip(),
    )


@main_bp.route("/load-placements/new", methods=["GET", "POST"])
def load_placement_new_generic():
    loads = LoadType.query.order_by("name").all()
    cars = Car.query.order_by("id", reverse=True).all()
    locations = Location.query.order_by("name").all()
    if request.method == "POST":
        load_id = request.form.get("load_id", "").strip()
        if not load_id.isdigit():
            return "Select a load type for this placement.", 400
        load = LoadType.query.get_or_404(int(load_id))
        placement = LoadPlacement(load_id=load.id)
        if not apply_load_placement_form(placement, request.form):
            return "Select a car or location for this load placement.", 400
        db.session.add(placement)
        db.session.commit()
        ensure_db_backup()
        if placement.car_id:
            car = Car.query.get(placement.car_id)
            if car:
                maybe_run_nmra_loaded_weight_check(car, car.actual_weight, car.actual_length, car.scale, True)
        return redirect(url_for("main.load_detail", load_id=load.id))
    return render_template(
        "load_placement_form.html",
        load=None,
        placement=None,
        loads=loads,
        cars=cars,
        locations=locations,
        preset_car_id=request.args.get("car_id", "").strip(),
        preset_location_id=request.args.get("location_id", "").strip(),
    )


@main_bp.route("/load-placements/<int:placement_id>/edit", methods=["GET", "POST"])
def load_placement_edit(placement_id: int):
    placement = LoadPlacement.query.get_or_404(placement_id)
    cars = Car.query.order_by("id", reverse=True).all()
    locations = Location.query.order_by("name").all()
    if request.method == "POST":
        previous_car_id = placement.car_id
        if not apply_load_placement_form(placement, request.form):
            return "Select a car or location for this load placement.", 400
        db.session.commit()
        ensure_db_backup()
        if previous_car_id and previous_car_id != placement.car_id:
            previous_car = Car.query.get(previous_car_id)
            if previous_car:
                maybe_run_nmra_loaded_weight_check(
                    previous_car,
                    previous_car.actual_weight,
                    previous_car.actual_length,
                    previous_car.scale,
                    True,
                )
        if placement.car_id:
            car = Car.query.get(placement.car_id)
            if car:
                maybe_run_nmra_loaded_weight_check(car, car.actual_weight, car.actual_length, car.scale, True)
        return redirect(url_for("main.load_detail", load_id=placement.load_id))
    return render_template(
        "load_placement_form.html",
        load=placement.load,
        placement=placement,
        cars=cars,
        locations=locations,
        preset_car_id=str(placement.car_id or ""),
        preset_location_id=str(placement.location_id or ""),
    )


@main_bp.route("/load-placements/<int:placement_id>/delete", methods=["POST"])
def load_placement_delete(placement_id: int):
    placement = LoadPlacement.query.get_or_404(placement_id)
    load_id = placement.load_id
    car_id = placement.car_id
    db.session.delete(placement)
    db.session.commit()
    ensure_db_backup()
    if car_id:
        car = Car.query.get(car_id)
        if car:
            maybe_run_nmra_loaded_weight_check(car, car.actual_weight, car.actual_length, car.scale, True)
    return redirect(url_for("main.load_detail", load_id=load_id))


@main_bp.route("/car-classes/<int:class_id>")
def car_class_detail(class_id: int):
    car_class = CarClass.query.get_or_404(class_id)
    cars = Car.query.filter_by(car_class_id=car_class.id).order_by("id", reverse=True).all()
    page_size = get_page_size()
    page = get_page_number()
    paged_cars, cars_pagination = paginate_list(
        cars,
        page,
        page_size,
        "main.car_class_detail",
        {"class_id": car_class.id},
    )
    return render_template(
        "car_class_detail.html",
        car_class=car_class,
        cars=paged_cars,
        cars_pagination=cars_pagination,
    )


@main_bp.route("/car-classes/<int:class_id>/delete", methods=["POST"])
def car_class_delete(class_id: int):
    car_class = CarClass.query.get_or_404(class_id)
    if Car.query.filter_by(car_class_id=car_class.id).count() > 0:
        return "Cannot delete class with cars assigned.", 400
    db.session.delete(car_class)
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.car_classes"))


@main_bp.route("/car-classes/<int:class_id>/edit", methods=["GET", "POST"])
def car_class_edit(class_id: int):
    car_class = CarClass.query.get_or_404(class_id)
    if request.method == "POST":
        car_class.code = request.form.get("code", "").strip()
        car_class.car_type = request.form.get("car_type", "").strip()
        car_class.era = request.form.get("era", "").strip()
        car_class.wheel_arrangement = request.form.get("wheel_arrangement", "").strip()
        car_class.tender_axles = request.form.get("tender_axles", "").strip()
        car_class.is_locomotive = request.form.get("is_locomotive") == "on"
        car_class.power_type = request.form.get("power_type", "").strip()
        car_class.capacity = request.form.get("capacity", "").strip()
        car_class.weight = request.form.get("weight", "").strip()
        car_class.load_limit = request.form.get("load_limit", "").strip()
        car_class.aar_plate = request.form.get("aar_plate", "").strip()
        car_class.internal_length = request.form.get("internal_length", "").strip()
        car_class.internal_width = request.form.get("internal_width", "").strip()
        car_class.internal_height = request.form.get("internal_height", "").strip()
        car_class.external_length = request.form.get("external_length", "").strip()
        car_class.external_width = request.form.get("external_width", "").strip()
        car_class.external_height = request.form.get("external_height", "").strip()
        car_class.cubic_feet = request.form.get("cubic_feet", "").strip()
        car_class.notes = request.form.get("notes", "").strip()
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.car_class_detail", class_id=car_class.id))
    return render_template("car_class_form.html", car_class=car_class, power_type_options=CONSIST_POWER_TYPES)


def build_car_label(car: Car) -> str:
    reporting_mark = car.railroad.reporting_mark if car.railroad else car.reporting_mark_override
    if reporting_mark and car.car_number:
        return f"{reporting_mark} {car.car_number}"
    if reporting_mark:
        return reporting_mark
    if car.car_number:
        return car.car_number
    return f"Car {car.id}"


def build_car_type_class(car: Car) -> str:
    car_class = car.car_class
    if not car_class and car.car_class_id:
        class_id = int(car.car_class_id) if isinstance(car.car_class_id, (int, str)) and str(car.car_class_id).isdigit() else None
        if class_id is not None:
            car_class = CarClass.query.get(class_id)
    car_type = car.car_type_override or (car_class.car_type if car_class else "")
    class_code = car_class.code if car_class else ""
    if class_code and car_type and car_type != class_code:
        return f"{class_code} / {car_type}"
    if class_code:
        return class_code
    if car_type:
        return car_type
    return ""


def build_flat_pack_plan(
    cars: list[dict],
    rows_count: int,
    flat_length_in: float,
    flat_unit: str,
    foam_options: list[dict[str, int | str]],
    packing_mode: str,
    end_foam_key: int | None,
    compression_enabled: bool,
    foam_label_map: dict[int, str],
    foam_color_map: dict[int, str],
    precision: int = 100,
) -> dict:
    capacity_units = int(round(flat_length_in * precision))
    end_foam_units = 0
    end_foam_min_units = 0
    end_foam_nominal_units = 0
    if end_foam_key is not None:
        end_foam_units = end_foam_key
        end_option = next((opt for opt in foam_options if opt["key"] == end_foam_key), None)
        if end_option:
            end_foam_nominal_units = int(end_option["nominal_units"])
            end_foam_min_units = int(end_option["min_units"]) if compression_enabled else end_foam_nominal_units

    rows: list[dict] = []
    unplaced: list[dict] = []
    cars_sorted = sorted(cars, key=lambda item: item["length_units"], reverse=True)

    for car in cars_sorted:
        best_row = None
        best_score = None
        best_required = 0
        for row in rows:
            row_end_units = row.get("end_foam_units", end_foam_nominal_units)
            remaining_units = capacity_units - row["used_units"]
            additional_units = car["length_units"] + (row_end_units if row["cars"] else (2 * row_end_units))
            if additional_units > remaining_units and compression_enabled and end_foam_min_units:
                min_row_end = min(row_end_units, end_foam_min_units)
                if min_row_end < row_end_units:
                    row_count = len(row["cars"])
                    reduction = (row_end_units - min_row_end) * (row_count + 1)
                    adjusted_used = row["used_units"] - reduction
                    adjusted_remaining = capacity_units - adjusted_used
                    min_additional = car["length_units"] + (
                        min_row_end if row["cars"] else (2 * min_row_end)
                    )
                    if min_additional <= adjusted_remaining:
                        row["used_units"] = adjusted_used
                        row["end_foam_units"] = min_row_end
                        row_end_units = min_row_end
                        remaining_units = adjusted_remaining
                        additional_units = min_additional
            if additional_units > remaining_units:
                continue
            new_remaining = remaining_units - additional_units
            foam_count = 0
            leftover_units = new_remaining
            if packing_mode == "dense" and foam_options:
                plan, leftover_units = plan_fill_with_compression(new_remaining, foam_options)
                foam_count = len(plan)
            elif packing_mode == "fill" and foam_options:
                sizes_units = [int(opt["nominal_units"]) for opt in foam_options]
                foam_dp = build_foam_dp(capacity_units, sizes_units) if sizes_units else None
                if foam_dp:
                    foam_count, _, leftover_units = select_foam_plan(new_remaining, foam_dp)
            score = (leftover_units, foam_count, new_remaining)
            if best_score is None or score < best_score:
                best_score = score
                best_row = row
                best_required = additional_units
        if best_row is not None:
            best_row["cars"].append(car)
            best_row["used_units"] += best_required
        elif len(rows) < rows_count:
            row_end_units = end_foam_nominal_units
            base_units = car["length_units"] + (2 * row_end_units)
            if base_units > capacity_units and compression_enabled and end_foam_min_units:
                row_end_units = end_foam_min_units
                base_units = car["length_units"] + (2 * row_end_units)
            if base_units <= capacity_units:
                rows.append({"cars": [car], "used_units": base_units, "end_foam_units": row_end_units})
            else:
                unplaced.append(car)
        else:
            unplaced.append(car)

    total_foam_blocks = 0
    total_gap_in = 0.0
    total_foam_in = 0.0
    foam_counts: dict[int, int] = {}
    row_entries: list[dict] = []

    for index, row in enumerate(rows, start=1):
        used_units = row["used_units"]
        remaining_units = max(capacity_units - used_units, 0)
        foam_plan: list[dict[str, int]] = []
        foam_count = 0
        leftover_units = remaining_units
        if packing_mode == "dense" and foam_options:
            foam_plan, leftover_units = plan_fill_with_compression(remaining_units, foam_options)
            foam_count = len(foam_plan)
        elif packing_mode == "fill" and foam_options:
            sizes_units = [int(opt["nominal_units"]) for opt in foam_options]
            foam_dp = build_foam_dp(capacity_units, sizes_units) if sizes_units else None
            if foam_dp:
                foam_count, plan_units, leftover_units = select_foam_plan(remaining_units, foam_dp)
                foam_plan = [{"key": key, "units": key} for key in plan_units]
        foam_segments = [
            (segment["units"] / precision, segment["key"])
            for segment in foam_plan
            if segment["units"] > 0
        ]
        foam_length_in = sum(length for length, _ in foam_segments)
        gap_in = max(leftover_units / precision, 0.0)
        if gap_in < (0.5 / precision):
            gap_in = 0.0
        row_end_units = row.get("end_foam_units", end_foam_min_units)
        end_foam_blocks = (len(row["cars"]) + 1) if end_foam_units and row["cars"] else 0
        end_foam_length_in = (row_end_units / precision) * end_foam_blocks if end_foam_units else 0.0
        total_foam_blocks += foam_count + end_foam_blocks
        total_foam_in += foam_length_in + end_foam_length_in
        total_gap_in += gap_in
        if end_foam_units and end_foam_blocks:
            foam_counts[end_foam_units] = foam_counts.get(end_foam_units, 0) + end_foam_blocks
        for segment in foam_plan:
            key = segment["key"]
            foam_counts[key] = foam_counts.get(key, 0) + 1

        segments = []
        foam_label = ""
        foam_width_percent = 0.0
        if end_foam_units and row["cars"]:
            foam_display = inches_to_unit(row_end_units / precision, flat_unit)
            foam_label = foam_label_map.get(end_foam_units, "Foam")
            if not foam_label and foam_display is not None:
                foam_label = f"{format_length_value(foam_display)} {flat_unit}"
            foam_width_percent = ((row_end_units / precision) / flat_length_in) * 100 if flat_length_in else 0
            segments.append(
                {
                    "type": "foam",
                    "label": foam_label,
                    "color": foam_color_map.get(end_foam_units, ""),
                    "foam_key": end_foam_units,
                    "width_percent": foam_width_percent,
                }
            )
        for idx, car in enumerate(row["cars"]):
            length_in = car["length_in"]
            display_length = inches_to_unit(length_in, flat_unit)
            primary_label_short = car["label"]
            primary_label = primary_label_short
            secondary_label = car.get("type_class") or ""
            if display_length is not None:
                primary_label = f"{primary_label} ({format_length_value(display_length)} {flat_unit})"
            label = f"{primary_label} | {secondary_label}" if secondary_label else primary_label
            segments.append(
                {
                    "type": "car",
                    "label": label,
                    "primary_label": primary_label,
                    "primary_label_short": primary_label_short,
                    "secondary_label": secondary_label,
                    "type_class": car.get("type_class") or "",
                    "car_id": car.get("id"),
                    "width_percent": (length_in / flat_length_in) * 100 if flat_length_in else 0,
                }
            )
            if end_foam_units and idx < len(row["cars"]) - 1:
                segments.append(
                    {
                        "type": "foam",
                        "label": foam_label,
                        "color": foam_color_map.get(end_foam_units, ""),
                        "foam_key": end_foam_units,
                        "width_percent": foam_width_percent,
                    }
                )
        if end_foam_units and row["cars"]:
            segments.append(
                {
                    "type": "foam",
                    "label": foam_label,
                    "color": foam_color_map.get(end_foam_units, ""),
                    "foam_key": end_foam_units,
                    "width_percent": foam_width_percent,
                }
            )
        for length_in, key in foam_segments:
            display_length = inches_to_unit(length_in, flat_unit)
            display_label = foam_label_map.get(key, "Foam")
            if display_length is not None and display_label:
                display_label = display_label or f"{format_length_value(display_length)} {flat_unit}"
            segments.append(
                {
                    "type": "foam",
                    "label": display_label,
                    "color": foam_color_map.get(key, ""),
                    "foam_key": key,
                    "width_percent": (length_in / flat_length_in) * 100 if flat_length_in else 0,
                }
            )
        if gap_in > 0:
            gap_display = inches_to_unit(gap_in, flat_unit)
            gap_label = "Gap"
            if gap_display is not None:
                gap_label = f"Gap {format_length_value(gap_display)} {flat_unit}"
            segments.append(
                {
                    "type": "gap",
                    "label": gap_label,
                    "width_percent": (gap_in / flat_length_in) * 100 if flat_length_in else 0,
                }
            )

        used_in = used_units / precision
        used_display = inches_to_unit(used_in, flat_unit)
        remaining_display = inches_to_unit(remaining_units / precision, flat_unit)

        row_entries.append(
            {
                "index": index,
                "segments": segments,
                "foam_blocks": foam_count,
                "used_display": format_length_value(used_display) if used_display is not None else "",
                "remaining_display": format_length_value(remaining_display) if remaining_display is not None else "",
            }
        )

    for index in range(len(rows) + 1, rows_count + 1):
        gap_label = f"Gap {format_length_value(inches_to_unit(flat_length_in, flat_unit) or flat_length_in)} {flat_unit}"
        row_entries.append(
            {
                "index": index,
                "segments": [
                    {
                        "type": "gap",
                        "label": gap_label,
                        "width_percent": 100,
                    }
                ],
                "foam_blocks": 0,
                "used_display": "",
                "remaining_display": format_length_value(
                    inches_to_unit(flat_length_in, flat_unit) or flat_length_in
                ),
            }
        )

    return {
        "rows": row_entries,
        "unplaced": unplaced,
        "total_foam_blocks": total_foam_blocks,
        "total_gap_in": total_gap_in,
        "total_foam_in": total_foam_in,
        "foam_counts": foam_counts,
    }


@main_bp.route("/locations/<int:location_id>/flat-pack", methods=["GET", "POST"])
def flat_pack(location_id: int):
    location = Location.query.get_or_404(location_id)
    cars = Car.query.filter_by(location_id=location.id).order_by("id").all()
    prefetch_car_relations(cars)
    default_length_unit = get_default_length_unit()
    foam_blocks = get_foam_blocks()

    flat_length_value, flat_length_unit = parse_actual_length(location.flat_length)
    if flat_length_value and not flat_length_unit:
        flat_length_unit = default_length_unit
    flat_rows = location.flat_rows
    flat_scale_value = location.flat_scale or ""
    flat_gauge_value = location.flat_gauge or ""
    flat_height_value = location.flat_height or ""
    flat_row_width_value = location.flat_row_width or ""
    flat_weight_value = location.flat_weight or ""
    missing_flat_settings = not (flat_length_value and flat_rows)

    selected_ids = {car.id for car in cars}
    selected_foam_ids: list[int] = []
    print_width_value = "11"
    print_width_unit = "in"
    print_height_value = "8.5"
    print_height_unit = "in"
    packing_mode = "frugal"
    compression_enabled = True
    errors: list[str] = []
    pack_result = None

    if request.method == "POST":
        selected_ids = {int(value) for value in request.form.getlist("car_ids") if value.isdigit()} or selected_ids
        selected_foam_ids = [
            int(value) for value in request.form.getlist("foam_block_ids") if value.isdigit()
        ]
        packing_mode = request.form.get("packing_mode", "").strip() or packing_mode
        if packing_mode not in {"frugal", "fill", "dense"}:
            packing_mode = "frugal"
        compression_enabled = request.form.get("compression_enabled") == "on"
        print_width_value = request.form.get("print_width_value", "").strip() or print_width_value
        print_width_unit = request.form.get("print_width_unit", "").strip().lower() or print_width_unit
        print_height_value = request.form.get("print_height_value", "").strip() or print_height_value
        print_height_unit = request.form.get("print_height_unit", "").strip().lower() or print_height_unit

    if request.method != "POST":
        ext_width_value, ext_width_unit = parse_actual_length(location.external_width)
        ext_width_unit = ext_width_unit or default_length_unit
        ext_width_in = length_to_inches(ext_width_value, ext_width_unit) if ext_width_value else None
        if ext_width_in is not None and ext_width_in < 9.5:
            height_in = max(ext_width_in - 1.0, 0.0)
            height_value = inches_to_unit(height_in, ext_width_unit)
            if height_value is not None:
                print_height_value = format_length_value(height_value)
                print_height_unit = ext_width_unit

    flat_length_in = None
    if flat_length_value:
        flat_length_in = length_to_inches(flat_length_value, flat_length_unit or default_length_unit)
    if request.method == "POST":
        if flat_length_in is None or flat_length_in <= 0:
            errors.append("Flat length is required.")
        if not flat_rows or flat_rows <= 0:
            errors.append("Row count is required.")

    foam_block_options = []
    for index, block in enumerate(foam_blocks):
        length_value, length_unit = parse_actual_length(block.get("length"))
        length_unit = length_unit or default_length_unit
        length_in = length_to_inches(length_value, length_unit) if length_value else None
        if length_in is None or length_in <= 0:
            continue
        compression_value, compression_unit = parse_actual_length(block.get("compression"))
        compression_unit = compression_unit or length_unit
        compression_in = length_to_inches(compression_value, compression_unit) if compression_value else 0.0
        compression_in = max(0.0, min(compression_in or 0.0, length_in))
        foam_block_options.append(
            {
                "id": index,
                "length_label": block.get("length") or "",
                "width_label": block.get("width") or "",
                "height_label": block.get("height") or "",
                "compression_label": block.get("compression") or "",
                "label": " x ".join(
                    part
                    for part in [
                        block.get("length") or "",
                        block.get("width") or "",
                        block.get("height") or "",
                    ]
                    if part
                )
                + (f" ({block.get('weight')})" if block.get("weight") else ""),
                "length_in": length_in,
                "compression_in": compression_in,
            }
        )

    foam_block_map = {block["id"]: block for block in foam_block_options}
    if not selected_foam_ids:
        selected_foam_ids = [block["id"] for block in foam_block_options]
    selected_foam_ids = [value for value in selected_foam_ids if value in foam_block_map]
    selected_foam_blocks = [foam_block_map[value] for value in selected_foam_ids]
    foam_palette = [
        "#e8b07a",
        "#c9a777",
        "#d98c6b",
        "#b9c4a5",
        "#c7a4c4",
        "#a3b2cc",
        "#e1c67a",
        "#b7b09a",
    ]
    foam_label_map: dict[int, str] = {}
    foam_color_map: dict[int, str] = {}
    foam_meta_map: dict[int, dict[str, str]] = {}
    foam_options: list[dict[str, int]] = []
    for idx, block in enumerate(selected_foam_blocks):
        units_key = int(round(block["length_in"] * 100))
        compress_units = int(round(block.get("compression_in", 0.0) * 100))
        compress_units = min(compress_units, units_key)
        foam_label_map[units_key] = block.get("length_label") or block.get("label") or "Foam"
        foam_color_map[units_key] = foam_palette[idx % len(foam_palette)]
        foam_meta_map[units_key] = {
            "length": block.get("length_label") or "",
            "width": block.get("width_label") or "",
            "height": block.get("height_label") or "",
        }
        foam_options.append(
            {
                "key": units_key,
                "nominal_units": units_key,
                "min_units": max(units_key - compress_units, 0),
                "compress_units": compress_units,
                "pref": idx,
            }
        )

    car_entries = []
    missing_length = []
    for car in cars:
        label = build_car_label(car)
        length_value, length_unit = parse_actual_length(car.actual_length)
        length_unit = length_unit or default_length_unit
        length_in = length_to_inches(length_value, length_unit) if length_value and length_unit else None
        length_display = f"{length_value} {length_unit}" if length_value else ""
        entry = {
            "id": car.id,
            "label": label,
            "type_class": build_car_type_class(car),
            "length_display": length_display or "-",
            "length_in": length_in,
        }
        if length_in is None or length_in <= 0:
            missing_length.append(entry)
        car_entries.append(entry)

    if request.method == "POST" and not errors and flat_length_in:
        selected_entries = [entry for entry in car_entries if entry["id"] in selected_ids and entry["length_in"]]
        cars_for_pack = []
        for entry in selected_entries:
            length_in = float(entry["length_in"])
            cars_for_pack.append(
                {
                    "id": entry["id"],
                    "label": entry["label"],
                    "type_class": entry["type_class"],
                    "length_in": length_in,
                    "length_units": int(round(length_in * 100)),
                }
            )
        if not cars_for_pack:
            errors.append("No cars selected with valid lengths.")
        else:
            pack_result = build_flat_pack_plan(
                cars_for_pack,
                flat_rows or 0,
                flat_length_in,
                flat_length_unit,
                foam_options,
                packing_mode,
                foam_options[0]["key"] if foam_options else None,
                compression_enabled,
                foam_label_map,
                foam_color_map,
            )
            foam_display = inches_to_unit(pack_result["total_foam_in"], flat_length_unit)
            gap_display = inches_to_unit(pack_result["total_gap_in"], flat_length_unit)
            pack_result["total_foam_display"] = (
                format_length_value(foam_display) if foam_display is not None else ""
            )
            pack_result["total_gap_display"] = (
                format_length_value(gap_display) if gap_display is not None else ""
            )
            pack_result["flat_unit"] = flat_length_unit
            foam_counts = pack_result.get("foam_counts", {})
            pack_result["foam_legend"] = [
                {
                    "length": foam_meta_map.get(key, {}).get("length", "") or foam_label_map.get(key, "Foam"),
                    "width": foam_meta_map.get(key, {}).get("width", ""),
                    "height": foam_meta_map.get(key, {}).get("height", ""),
                    "color": foam_color_map.get(key, ""),
                    "count": foam_counts.get(key, 0),
                }
                for key in foam_label_map
            ]

    return render_template(
        "flat_pack.html",
        location=location,
        car_entries=car_entries,
        missing_length=missing_length,
        selected_ids=selected_ids,
        flat_length_value=flat_length_value,
        flat_length_unit=flat_length_unit or default_length_unit,
        flat_rows_value=str(flat_rows) if flat_rows is not None else "",
        flat_scale_value=flat_scale_value,
        flat_gauge_value=flat_gauge_value,
        flat_height_value=flat_height_value,
        flat_row_width_value=flat_row_width_value,
        flat_weight_value=flat_weight_value,
        foam_block_options=foam_block_options,
        selected_foam_blocks=selected_foam_blocks,
        selected_foam_ids=selected_foam_ids,
        packing_mode=packing_mode,
        compression_enabled=compression_enabled,
        missing_flat_settings=missing_flat_settings,
        print_width_value=print_width_value,
        print_width_unit=print_width_unit,
        print_height_value=print_height_value,
        print_height_unit=print_height_unit,
        errors=errors,
        pack_result=pack_result,
    )


@main_bp.route("/locations/<int:location_id>/flat-pack/view", methods=["POST"])
def flat_pack_view(location_id: int):
    location = Location.query.get_or_404(location_id)
    cars = Car.query.filter_by(location_id=location.id).order_by("id").all()
    prefetch_car_relations(cars)
    default_length_unit = get_default_length_unit()
    foam_blocks = get_foam_blocks()

    selected_ids = [int(value) for value in request.form.getlist("car_ids") if value.isdigit()]
    selected_foam_ids = [int(value) for value in request.form.getlist("foam_block_ids") if value.isdigit()]
    packing_mode = request.form.get("packing_mode", "").strip() or "frugal"
    if packing_mode not in {"frugal", "fill", "dense"}:
        packing_mode = "frugal"
    compression_enabled = request.form.get("compression_enabled") == "on"
    print_width_value = request.form.get("print_width_value", "").strip() or "11"
    print_width_unit = request.form.get("print_width_unit", "").strip().lower() or "in"
    print_height_value = request.form.get("print_height_value", "").strip() or "8.5"
    print_height_unit = request.form.get("print_height_unit", "").strip().lower() or "in"

    flat_length_value, flat_length_unit = parse_actual_length(location.flat_length)
    if flat_length_value and not flat_length_unit:
        flat_length_unit = default_length_unit
    flat_length_in = length_to_inches(flat_length_value, flat_length_unit) if flat_length_value else None
    flat_rows = location.flat_rows
    errors: list[str] = []
    pack_result = None

    if flat_length_in is None or flat_length_in <= 0:
        errors.append("Flat length is required.")
    if not flat_rows or flat_rows <= 0:
        errors.append("Row count is required.")

    foam_block_options = []
    for index, block in enumerate(foam_blocks):
        length_value, length_unit = parse_actual_length(block.get("length"))
        length_unit = length_unit or default_length_unit
        length_in = length_to_inches(length_value, length_unit) if length_value else None
        if length_in is None or length_in <= 0:
            continue
        compression_value, compression_unit = parse_actual_length(block.get("compression"))
        compression_unit = compression_unit or length_unit
        compression_in = length_to_inches(compression_value, compression_unit) if compression_value else 0.0
        compression_in = max(0.0, min(compression_in or 0.0, length_in))
        foam_block_options.append(
            {
                "id": index,
                "length_in": length_in,
                "length_label": block.get("length") or "",
                "width_label": block.get("width") or "",
                "height_label": block.get("height") or "",
                "compression_label": block.get("compression") or "",
                "compression_in": compression_in,
            }
        )
    foam_block_map = {block["id"]: block for block in foam_block_options}
    if not selected_foam_ids:
        selected_foam_ids = [block["id"] for block in foam_block_options]
    selected_foam_ids = [value for value in selected_foam_ids if value in foam_block_map]
    foam_palette = [
        "#e8b07a",
        "#c9a777",
        "#d98c6b",
        "#b9c4a5",
        "#c7a4c4",
        "#a3b2cc",
        "#e1c67a",
        "#b7b09a",
    ]
    foam_label_map: dict[int, str] = {}
    foam_color_map: dict[int, str] = {}
    foam_meta_map: dict[int, dict[str, str]] = {}
    foam_options: list[dict[str, int]] = []
    for idx, block_id in enumerate(selected_foam_ids):
        block = foam_block_map[block_id]
        units_key = int(round(block["length_in"] * 100))
        compress_units = int(round(block.get("compression_in", 0.0) * 100))
        compress_units = min(compress_units, units_key)
        foam_label_map[units_key] = foam_block_map[block_id].get("length_label") or "Foam"
        foam_color_map[units_key] = foam_palette[idx % len(foam_palette)]
        foam_meta_map[units_key] = {
            "length": foam_block_map[block_id].get("length_label") or "",
            "width": foam_block_map[block_id].get("width_label") or "",
            "height": foam_block_map[block_id].get("height_label") or "",
        }
        foam_options.append(
            {
                "key": units_key,
                "nominal_units": units_key,
                "min_units": max(units_key - compress_units, 0),
                "compress_units": compress_units,
                "pref": idx,
            }
        )

    cars_for_pack = []
    for car in cars:
        if selected_ids and car.id not in selected_ids:
            continue
        length_value, length_unit = parse_actual_length(car.actual_length)
        length_unit = length_unit or default_length_unit
        length_in = length_to_inches(length_value, length_unit) if length_value and length_unit else None
        if length_in is None or length_in <= 0:
            continue
        cars_for_pack.append(
            {
                "id": car.id,
                "label": build_car_label(car),
                "type_class": build_car_type_class(car),
                "length_in": length_in,
                "length_units": int(round(length_in * 100)),
            }
        )
    if not cars_for_pack:
        errors.append("No cars selected with valid lengths.")

    if not errors and flat_length_in:
        pack_result = build_flat_pack_plan(
            cars_for_pack,
            flat_rows or 0,
            flat_length_in,
            flat_length_unit,
            foam_options,
            packing_mode,
            foam_options[0]["key"] if foam_options else None,
            compression_enabled,
            foam_label_map,
            foam_color_map,
        )
        foam_display = inches_to_unit(pack_result["total_foam_in"], flat_length_unit)
        gap_display = inches_to_unit(pack_result["total_gap_in"], flat_length_unit)
        pack_result["total_foam_display"] = format_length_value(foam_display) if foam_display is not None else ""
        pack_result["total_gap_display"] = format_length_value(gap_display) if gap_display is not None else ""
        pack_result["flat_unit"] = flat_length_unit
        foam_counts = pack_result.get("foam_counts", {})
        pack_result["foam_legend"] = [
            {
                "length": foam_meta_map.get(key, {}).get("length", "") or foam_label_map.get(key, "Foam"),
                "width": foam_meta_map.get(key, {}).get("width", ""),
                "height": foam_meta_map.get(key, {}).get("height", ""),
                "color": foam_color_map.get(key, ""),
                "count": foam_counts.get(key, 0),
            }
            for key in foam_label_map
        ]

    if not request.form.get("print_height_value", "").strip():
        ext_width_value, ext_width_unit = parse_actual_length(location.external_width)
        ext_width_unit = ext_width_unit or default_length_unit
        ext_width_in = length_to_inches(ext_width_value, ext_width_unit) if ext_width_value else None
        if ext_width_in is not None and ext_width_in < 9.5:
            height_in = max(ext_width_in - 1.0, 0.0)
            height_value = inches_to_unit(height_in, ext_width_unit)
            if height_value is not None:
                print_height_value = format_length_value(height_value)
                print_height_unit = ext_width_unit

    return render_template(
        "flat_pack_view.html",
        location=location,
        errors=errors,
        pack_result=pack_result,
        selected_ids=selected_ids,
        selected_foam_ids=selected_foam_ids,
        packing_mode=packing_mode,
        compression_enabled=compression_enabled,
        print_width_value=print_width_value,
        print_width_unit=print_width_unit,
        print_height_value=print_height_value,
        print_height_unit=print_height_unit,
    )


@main_bp.route("/locations/<int:location_id>/flat-pack/pdf", methods=["POST"])
def flat_pack_pdf(location_id: int):
    location = Location.query.get_or_404(location_id)
    cars = Car.query.filter_by(location_id=location.id).order_by("id").all()
    prefetch_car_relations(cars)
    default_length_unit = get_default_length_unit()
    foam_blocks = get_foam_blocks()

    selected_ids = {int(value) for value in request.form.getlist("car_ids") if value.isdigit()}
    selected_foam_ids = [int(value) for value in request.form.getlist("foam_block_ids") if value.isdigit()]
    packing_mode = request.form.get("packing_mode", "").strip() or "frugal"
    if packing_mode not in {"frugal", "fill", "dense"}:
        packing_mode = "frugal"
    print_width_value = request.form.get("print_width_value", "").strip() or "11"
    print_width_unit = request.form.get("print_width_unit", "").strip().lower() or "in"
    print_height_value = request.form.get("print_height_value", "").strip() or "8.5"
    print_height_unit = request.form.get("print_height_unit", "").strip().lower() or "in"
    wireframe = request.form.get("wireframe") == "1"

    flat_length_value, flat_length_unit = parse_actual_length(location.flat_length)
    if flat_length_value and not flat_length_unit:
        flat_length_unit = default_length_unit
    flat_length_in = length_to_inches(flat_length_value, flat_length_unit) if flat_length_value else None
    flat_rows = location.flat_rows
    if flat_length_in is None or flat_length_in <= 0:
        return "Flat length is required.", 400
    if not flat_rows or flat_rows <= 0:
        return "Row count is required.", 400

    foam_block_options = []
    for index, block in enumerate(foam_blocks):
        length_value, length_unit = parse_actual_length(block.get("length"))
        length_unit = length_unit or default_length_unit
        length_in = length_to_inches(length_value, length_unit) if length_value else None
        if length_in is None or length_in <= 0:
            continue
        compression_value, compression_unit = parse_actual_length(block.get("compression"))
        compression_unit = compression_unit or length_unit
        compression_in = length_to_inches(compression_value, compression_unit) if compression_value else 0.0
        compression_in = max(0.0, min(compression_in or 0.0, length_in))
        foam_block_options.append(
            {
                "id": index,
                "length_in": length_in,
                "length_label": block.get("length") or "",
                "width_label": block.get("width") or "",
                "height_label": block.get("height") or "",
                "compression_label": block.get("compression") or "",
                "compression_in": compression_in,
            }
        )
    foam_block_map = {block["id"]: block for block in foam_block_options}
    if not selected_foam_ids:
        selected_foam_ids = [block["id"] for block in foam_block_options]
    selected_foam_ids = [value for value in selected_foam_ids if value in foam_block_map]
    foam_palette = [
        "#e8b07a",
        "#c9a777",
        "#d98c6b",
        "#b9c4a5",
        "#c7a4c4",
        "#a3b2cc",
        "#e1c67a",
        "#b7b09a",
    ]
    foam_label_map: dict[int, str] = {}
    foam_color_map: dict[int, str] = {}
    foam_meta_map: dict[int, dict[str, str]] = {}
    foam_options: list[dict[str, int]] = []
    for idx, block_id in enumerate(selected_foam_ids):
        block = foam_block_map[block_id]
        units_key = int(round(block["length_in"] * 100))
        compress_units = int(round(block.get("compression_in", 0.0) * 100))
        compress_units = min(compress_units, units_key)
        foam_label_map[units_key] = foam_block_map[block_id].get("length_label") or "Foam"
        foam_color_map[units_key] = foam_palette[idx % len(foam_palette)]
        foam_meta_map[units_key] = {
            "length": foam_block_map[block_id].get("length_label") or "",
            "width": foam_block_map[block_id].get("width_label") or "",
            "height": foam_block_map[block_id].get("height_label") or "",
        }
        foam_options.append(
            {
                "key": units_key,
                "nominal_units": units_key,
                "min_units": max(units_key - compress_units, 0),
                "compress_units": compress_units,
                "pref": idx,
            }
        )

    cars_for_pack = []
    for car in cars:
        if selected_ids and car.id not in selected_ids:
            continue
        length_value, length_unit = parse_actual_length(car.actual_length)
        length_unit = length_unit or default_length_unit
        length_in = length_to_inches(length_value, length_unit) if length_value and length_unit else None
        if length_in is None or length_in <= 0:
            continue
        cars_for_pack.append(
            {
                "id": car.id,
                "label": build_car_label(car),
                "type_class": build_car_type_class(car),
                "length_in": length_in,
                "length_units": int(round(length_in * 100)),
            }
        )
    if not cars_for_pack:
        return "No cars selected with valid lengths.", 400

    compression_enabled = request.form.get("compression_enabled") == "on"
    pack_result = build_flat_pack_plan(
        cars_for_pack,
        flat_rows,
        flat_length_in,
        flat_length_unit,
        foam_options,
        packing_mode,
        foam_options[0]["key"] if foam_options else None,
        compression_enabled,
        foam_label_map,
        foam_color_map,
    )
    foam_counts = pack_result.get("foam_counts", {})
    pack_result["foam_legend"] = [
        {
            "length": foam_meta_map.get(key, {}).get("length", "") or foam_label_map.get(key, "Foam"),
            "width": foam_meta_map.get(key, {}).get("width", ""),
            "height": foam_meta_map.get(key, {}).get("height", ""),
            "color": foam_color_map.get(key, ""),
            "count": foam_counts.get(key, 0),
        }
        for key in foam_label_map
    ]

    print_width_amount, print_width_unit = parse_length_value(
        print_width_value,
        print_width_unit,
        "in",
    )
    print_height_amount, print_height_unit = parse_length_value(
        print_height_value,
        print_height_unit,
        "in",
    )
    diagram_width_in = length_to_inches(str(print_width_amount), print_width_unit) if print_width_amount else 11
    diagram_height_in = length_to_inches(str(print_height_amount), print_height_unit) if print_height_amount else 8.5

    dpi = 150
    page_width_in = 11.0
    page_height_in = 8.5
    width_px = max(int(round(page_width_in * dpi)), 300)
    height_px = max(int(round(page_height_in * dpi)), 300)
    margin = int(0.35 * dpi)
    row_gap = int(0.15 * dpi)

    image = Image.new("RGB", (width_px, height_px), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", size=21)
        font_small = ImageFont.truetype("DejaVuSans.ttf", size=18)
        font_small_bold = ImageFont.truetype("DejaVuSans-Bold.ttf", size=18)
        font_tiny = ImageFont.truetype("DejaVuSans.ttf", size=15)
    except OSError:
        font = ImageFont.load_default()
        font_small = font
        font_small_bold = font
        font_tiny = font

    title = f"Flat Packing: {location.name}"
    max_width_px = max(width_px - (margin * 2), 1)
    max_height_px = max(height_px - (margin * 2), 1)
    diagram_width_px = int(round(min(diagram_width_in * dpi, max_width_px)))
    diagram_height_px = int(round(min(diagram_height_in * dpi, max_height_px)))
    title_bbox = font.getbbox(title) if hasattr(font, "getbbox") else (0, 0, 0, int(font.size * 1.2))
    title_height = title_bbox[3] - title_bbox[1]
    title_width = title_bbox[2] - title_bbox[0]
    origin_x = margin + max((max_width_px - diagram_width_px) // 2, 0)
    origin_y = margin + max((max_height_px - diagram_height_px) // 2, 0)
    title_x = origin_x
    title_y = max(origin_y - title_height - row_gap, margin / 2)
    draw.text((title_x, title_y), title, fill="#111111", font=font)
    barcode_x = int(title_x + title_width + (0.15 * dpi))
    barcode_y = int(title_y)
    barcode_height = int(max(title_height, dpi * 0.3))
    max_barcode_width = int(max_width_px + margin - barcode_x)
    draw_code128(draw, location.name or "", barcode_x, barcode_y, barcode_height, max_barcode_width)
    available_height = diagram_height_px - row_gap
    row_height = (
        (available_height - (row_gap * max(flat_rows - 1, 0))) / flat_rows if flat_rows else available_height
    )
    row_width = diagram_width_px

    current_y = origin_y + row_gap
    colors = {"car": "#6c8ebf", "foam": "#d4a373", "gap": "#cccccc"}
    foam_keys = list(foam_color_map.keys())
    foam_hatch_angles: dict[int, int] = {}
    if wireframe and len(foam_keys) <= 2:
        for idx, key in enumerate(foam_keys):
            foam_hatch_angles[key] = 45 if idx % 2 == 0 else -45

    def draw_hatch(target_image: Image.Image, x0: int, y0: int, width: int, height: int, angle: int) -> None:
        if width <= 0 or height <= 0:
            return
        spacing = max(int(min(width, height) / 4), 6)
        hatch = Image.new("RGB", (width, height), "white")
        hatch_draw = ImageDraw.Draw(hatch)
        if angle == 45:
            for offset in range(-height, width, spacing):
                hatch_draw.line((offset, height, offset + height, 0), fill="#000000", width=1)
        else:
            for offset in range(-height, width, spacing):
                hatch_draw.line((offset, 0, offset + height, height), fill="#000000", width=1)
        target_image.paste(hatch, (x0, y0))
    for row in pack_result["rows"]:
        x = origin_x
        draw.rectangle([x, current_y, x + row_width, current_y + row_height], outline="#444444", width=1)
        for segment in row["segments"]:
            seg_width = row_width * (segment["width_percent"] / 100.0)
            if seg_width <= 0:
                continue
            if wireframe:
                draw.rectangle(
                    [x, current_y, x + seg_width, current_y + row_height],
                    outline="#000000",
                    width=1,
                )
                if segment["type"] == "foam":
                    foam_key = segment.get("foam_key")
                    angle = foam_hatch_angles.get(foam_key)
                    if angle:
                        draw_hatch(
                            image,
                            int(x),
                            int(current_y),
                            int(seg_width),
                            int(row_height),
                            angle,
                        )
            else:
                fill_color = segment.get("color") or colors.get(segment["type"], "#dddddd")
                draw.rectangle(
                    [x, current_y, x + seg_width, current_y + row_height],
                    fill=fill_color,
                    outline="#444444",
                    width=1,
                )
            label = segment["label"]
            primary_label = segment.get("primary_label_short") or segment.get("primary_label")
            secondary_label = segment.get("secondary_label") or segment.get("type_class")
            if (not secondary_label) and label and " | " in label:
                parts = label.split(" | ", 1)
                if parts:
                    primary_label = primary_label or parts[0]
                    secondary_label = parts[1] if len(parts) > 1 else secondary_label
            if segment["type"] == "car" and primary_label:
                primary_font = font_small_bold
                secondary_font = font_tiny
                line_gap = 4
                primary_box = draw.textbbox((0, 0), primary_label, font=primary_font)
                primary_w = primary_box[2] - primary_box[0]
                primary_h = primary_box[3] - primary_box[1]
                secondary_w = 0
                secondary_h = 0
                if secondary_label:
                    secondary_box = draw.textbbox((0, 0), secondary_label, font=secondary_font)
                    secondary_w = secondary_box[2] - secondary_box[0]
                    secondary_h = secondary_box[3] - secondary_box[1]
                total_w = max(primary_w, secondary_w)
                total_h = primary_h + (secondary_h + line_gap if secondary_label else 0)
                text_x = x + max((seg_width - total_w) / 2, 0)
                text_y = current_y + max((row_height - total_h) / 2, 0)
                draw.text((text_x, text_y), primary_label, fill="#111111", font=primary_font)
                if secondary_label:
                    draw.text(
                        (text_x, text_y + primary_h + line_gap),
                        secondary_label,
                        fill="#111111",
                        font=secondary_font,
                    )
                car_id = segment.get("car_id")
                if car_id is not None:
                    barcode_label = f"C{car_id}"
                    label_font = font_tiny
                    label_box = draw.textbbox((0, 0), barcode_label, font=label_font)
                    label_w = label_box[2] - label_box[0]
                    label_h = label_box[3] - label_box[1]
                    barcode_height = int(min(row_height * 0.2, dpi * 0.25))
                    barcode_height = max(barcode_height, int(dpi * 0.15))
                    barcode_width = int(max(seg_width - 6, 10))
                    barcode_width = min(barcode_width, int(seg_width))
                    barcode_total_h = barcode_height + label_h + 2
                    barcode_top = text_y + total_h + 4
                    max_barcode_top = current_y + row_height - barcode_total_h - 2
                    barcode_top = min(barcode_top, max_barcode_top)
                    barcode_top = max(barcode_top, current_y + 2)
                    barcode_x = int(x + max((seg_width - barcode_width) / 2, 0))
                    if barcode_height > 0 and barcode_width > 0:
                        draw_code128(
                            draw,
                            barcode_label,
                            barcode_x,
                            int(barcode_top),
                            barcode_height,
                            barcode_width,
                            center=True,
                        )
                        label_x = x + max((seg_width - label_w) / 2, 0)
                        label_y = barcode_top + barcode_height + 2
                        draw.text((label_x, label_y), barcode_label, fill="#111111", font=label_font)
            elif label:
                bbox = draw.textbbox((0, 0), label, font=font)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
                if text_w <= seg_width and text_h <= row_height:
                    text_x = x + max((seg_width - text_w) / 2, 0)
                    text_y = current_y + max((row_height - text_h) / 2, 0)
                    draw.text((text_x, text_y), label, fill="#111111", font=font)
            x += seg_width
        current_y += row_height + row_gap

    output = io.BytesIO()
    pdf_image = image.convert("RGBA")
    pdf_image.save(
        output,
        format="PDF",
        resolution=dpi,
        quality=100,
        subsampling=0,
    )
    output.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"flat-pack-{location.id}-{timestamp}.pdf"
    return Response(
        output.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@main_bp.route("/locations/<int:location_id>/inventory/pdf")
def location_inventory_pdf(location_id: int):
    location = Location.query.get_or_404(location_id)
    cars = Car.query.filter_by(location_id=location.id).order_by("id").all()
    if not cars:
        return "No cars in this location.", 400
    prefetch_car_relations(cars)

    dpi = 200
    page_width_in = 8.5
    page_height_in = 11.0
    margin_in = 0.25
    top_label_height_in = 0.75
    top_label_gap_in = 0.2
    columns = 3
    rows = 10
    page_width_px = int(round(page_width_in * dpi))
    page_height_px = int(round(page_height_in * dpi))
    margin_px = int(round(margin_in * dpi))
    top_label_height_px = int(round(top_label_height_in * dpi))
    top_label_gap_px = int(round(top_label_gap_in * dpi))
    table_top_px = margin_px + top_label_height_px + top_label_gap_px
    table_height_px = page_height_px - margin_px - table_top_px
    table_width_px = page_width_px - (margin_px * 2)
    cell_width_px = max(int(table_width_px / columns), 1)
    cell_height_px = max(int(table_height_px / rows), 1)
    top_label_width_px = cell_width_px

    inner_margin = int(dpi * 0.05)
    text_width = max(cell_width_px - (inner_margin * 2), 1)

    title_size = max(int(dpi * 0.12), 22)
    class_size = max(int(dpi * 0.1), 18)
    label_size = max(int(dpi * 0.09), 16)
    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", size=title_size)
        class_font = ImageFont.truetype("DejaVuSans.ttf", size=class_size)
        label_font = ImageFont.truetype("DejaVuSans.ttf", size=label_size)
    except OSError:
        title_font = ImageFont.load_default()
        class_font = title_font
        label_font = title_font

    title_line_height = title_size + 2
    class_line_height = class_size + 2
    label_line_height = label_size + 2

    labels_per_page = columns * rows
    page_images = []
    page_draw = None

    for idx, car in enumerate(cars):
        if idx % labels_per_page == 0:
            page_image = Image.new("RGB", (page_width_px, page_height_px), "white")
            page_images.append(page_image)
            page_draw = ImageDraw.Draw(page_image)
            table_left = margin_px
            table_top = table_top_px
            table_right = table_left + (cell_width_px * columns)
            table_bottom = table_top + (cell_height_px * rows)
            page_draw.rectangle(
                [table_left, table_top, table_right, table_bottom],
                outline="#111111",
                width=1,
            )
            for col_idx in range(1, columns):
                x = table_left + col_idx * cell_width_px
                page_draw.line([(x, table_top), (x, table_bottom)], fill="#111111", width=1)
            for row_idx in range(1, rows):
                y = table_top + row_idx * cell_height_px
                page_draw.line([(table_left, y), (table_right, y)], fill="#111111", width=1)

            if len(page_images) == 1:
                location_code = location.name or f"Location {location.id}"
                label_x = margin_px
                label_y = margin_px
                label_text_width = max(top_label_width_px - (inner_margin * 2), 1)
                header_line = location_code
                line_width = page_draw.textlength(header_line, font=title_font)
                text_x = label_x + inner_margin + max((label_text_width - line_width) / 2, 0)
                text_y = label_y + inner_margin
                page_draw.text((text_x, text_y), header_line, fill="#111111", font=title_font)
                barcode_top = text_y + title_line_height + int(dpi * 0.02)
                barcode_height = label_y + top_label_height_px - inner_margin - barcode_top
                if barcode_height <= label_line_height:
                    barcode_top = label_y + inner_margin
                    barcode_height = max(top_label_height_px - (inner_margin * 2), 1)
                label_lines = wrap_text_lines(page_draw, location_code, label_text_width, label_font)
                if not label_lines:
                    label_lines = [location_code]
                draw_barcode_with_label(
                    page_draw,
                    location_code,
                    label_lines[:2],
                    label_x + inner_margin,
                    int(barcode_top),
                    label_text_width,
                    int(barcode_height),
                    label_font,
                    label_line_height,
                    module_width_max=6,
                    barcode_height_scale=0.7,
                )
        if page_draw is None:
            continue
        local_index = idx % labels_per_page
        row = local_index // columns
        col = local_index % columns
        origin_x = margin_px + col * cell_width_px
        origin_y = table_top_px + row * cell_height_px
        cursor_y = origin_y + inner_margin

        text_top = cursor_y
        reporting_mark = car.railroad.reporting_mark if car.railroad else car.reporting_mark_override
        car_number = car.car_number
        header_line = ""
        if reporting_mark and car_number:
            header_line = f"{reporting_mark} {car_number}"
        elif reporting_mark:
            header_line = reporting_mark
        elif car_number:
            header_line = str(car_number)
        else:
            header_line = f"Car {car.id}"

        text_lines = [header_line]
        class_info = build_car_type_class(car)
        if class_info:
            class_lines = wrap_text_lines(page_draw, class_info, text_width, class_font)
            if class_lines:
                text_lines.append(class_lines[0])
        text_lines = text_lines[:2]
        for line in text_lines:
            line_width = page_draw.textlength(line, font=title_font if line == header_line else class_font)
            page_draw.text(
                (origin_x + inner_margin + max((text_width - line_width) / 2, 0), cursor_y),
                line,
                fill="#111111",
                font=title_font if line == header_line else class_font,
            )
            cursor_y += title_line_height if line == header_line else class_line_height

        cursor_y += int(dpi * 0.02)
        text_bottom = max(cursor_y - int(dpi * 0.02), text_top)
        barcode_top = cursor_y
        barcode_area_height = origin_y + cell_height_px - inner_margin - barcode_top
        if barcode_area_height <= label_line_height:
            barcode_top = origin_y + inner_margin
            barcode_area_height = max(cell_height_px - (inner_margin * 2), 1)

        car_code = f"C{car.id}"
        draw_barcode_with_label(
            page_draw,
            car_code,
            [car_code],
            origin_x + inner_margin,
            int(barcode_top),
            text_width,
            int(barcode_area_height),
            label_font,
            label_line_height,
            module_width_max=6,
            barcode_height_scale=0.7,
        )

    output = io.BytesIO()
    pdf_images = [page_images[0].convert("RGB")]
    pdf_images[0].save(
        output,
        format="PDF",
        save_all=True,
        append_images=[img.convert("RGB") for img in page_images[1:]],
        resolution=dpi,
        quality=100,
        subsampling=0,
    )
    output.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"location-inventory-{location.id}-{timestamp}.pdf"
    return Response(
        output.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

@main_bp.route("/locations/<int:location_id>")
def location_detail(location_id: int):
    location = Location.query.get_or_404(location_id)
    cars = Car.query.filter_by(location_id=location.id).order_by("id", reverse=True).all()
    tools = ToolItem.query.filter_by(location_id=location.id).order_by("name").all()
    parts = PartItem.query.filter_by(location_id=location.id).order_by("name").all()
    descendant_ids = get_location_descendant_ids(location)
    child_cars = []
    child_tools = []
    child_parts = []
    child_locations = {}
    if descendant_ids:
        child_locations = {loc.id: loc for loc in Location.query.all() if loc.id in descendant_ids}
        child_cars = [car for car in Car.query.all() if car.location_id in descendant_ids]
        prefetch_car_relations(child_cars)
        child_tools = [tool for tool in ToolItem.query.all() if tool.location_id in descendant_ids]
        child_parts = [part for part in PartItem.query.all() if part.location_id in descendant_ids]
        attach_location_refs(child_tools)
        attach_location_refs(child_parts)
    page_size = get_page_size()
    page = get_page_number()
    paged_cars, cars_pagination = paginate_list(
        cars,
        page,
        page_size,
        "main.location_detail",
        {"location_id": location.id},
    )
    return render_template(
        "location_detail.html",
        location=location,
        cars=paged_cars,
        cars_pagination=cars_pagination,
        tools=tools,
        parts=parts,
        child_cars=child_cars,
        child_tools=child_tools,
        child_parts=child_parts,
    )


@main_bp.route("/locations/<int:location_id>/inspect", methods=["GET", "POST"])
def location_inspect(location_id: int):
    location = Location.query.get_or_404(location_id)
    inspection_types = InspectionType.query.all()
    type_rows = inspection_type_tree(inspection_types)
    if request.method == "POST":
        inspection_date = request.form.get("inspection_date", "").strip()
        inspection_details = request.form.get("inspection_details", "").strip()
        inspection_type_id = request.form.get("inspection_type_id", "").strip()
        inspection_passed = request.form.get("inspection_passed", "").strip()
        if not inspection_date:
            return "Inspection date is required.", 400
        if not inspection_type_id.isdigit():
            return "Inspection type is required.", 400
        if inspection_passed not in {"passed", "failed"}:
            return "Inspection result is required.", 400
        cars = Car.query.filter_by(location_id=location.id).all()
        if not cars:
            return "No cars in this location to inspect.", 400
        for car in cars:
            db.session.add(
                CarInspection(
                    car_id=car.id,
                    inspection_date=inspection_date,
                    details=inspection_details or None,
                    inspection_type_id=int(inspection_type_id),
                    passed=inspection_passed == "passed",
                )
            )
            car.last_inspection_date = inspection_date
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.location_detail", location_id=location.id))
    cars_count = Car.query.filter_by(location_id=location.id).count()
    return render_template(
        "location_inspection_form.html",
        location=location,
        inspection_types=type_rows,
        cars_count=cars_count,
    )


def get_location_descendant_ids(location: Location) -> set[int]:
    descendants = set()
    queue = list(location.children)
    while queue:
        current = queue.pop(0)
        if current.id in descendants:
            continue
        descendants.add(current.id)
        queue.extend(current.children)
    return descendants


@main_bp.route("/locations/<int:location_id>/edit", methods=["GET", "POST"])
def location_edit(location_id: int):
    location = Location.query.get_or_404(location_id)
    descendant_ids = get_location_descendant_ids(location)
    if request.method == "POST":
        owner_error = apply_owner_form(location, request.form)
        if owner_error:
            return owner_error, 400
        location.name = request.form.get("name", "").strip()
        location.location_type = request.form.get("location_type", "").strip()
        parent_id = request.form.get("parent_id", "").strip()
        if parent_id and parent_id.isdigit():
            parent_id_value = int(parent_id)
            if parent_id_value == location.id or parent_id_value in descendant_ids:
                return "Invalid parent location selection.", 400
            location.parent = Location.query.get(parent_id_value)
        else:
            location.parent = None
        external_length_value = request.form.get("external_length_value", "").strip()
        external_length_unit = request.form.get("external_length_unit", "").strip().lower()
        if external_length_value and external_length_unit:
            location.external_length = f"{external_length_value} {external_length_unit}"
        else:
            location.external_length = external_length_value or None
        external_width_value = request.form.get("external_width_value", "").strip()
        external_width_unit = request.form.get("external_width_unit", "").strip().lower()
        if external_width_value and external_width_unit:
            location.external_width = f"{external_width_value} {external_width_unit}"
        else:
            location.external_width = external_width_value or None
        external_height_value = request.form.get("external_height_value", "").strip()
        external_height_unit = request.form.get("external_height_unit", "").strip().lower()
        if external_height_value and external_height_unit:
            location.external_height = f"{external_height_value} {external_height_unit}"
        else:
            location.external_height = external_height_value or None
        external_weight_value = request.form.get("external_weight_value", "").strip()
        external_weight_unit = request.form.get("external_weight_unit", "").strip().lower()
        if external_weight_value and external_weight_unit:
            location.external_weight = f"{external_weight_value} {external_weight_unit}"
        else:
            location.external_weight = external_weight_value or None
        flat_length_value = request.form.get("flat_length_value", "").strip()
        flat_length_unit = request.form.get("flat_length_unit", "").strip().lower()
        if flat_length_value and flat_length_unit:
            location.flat_length = f"{flat_length_value} {flat_length_unit}"
        else:
            location.flat_length = flat_length_value or None
        flat_rows = request.form.get("flat_rows", "").strip()
        location.flat_rows = int(flat_rows) if flat_rows.isdigit() else None
        flat_height_value = request.form.get("flat_height_value", "").strip()
        flat_height_unit = request.form.get("flat_height_unit", "").strip().lower()
        if flat_height_value and flat_height_unit:
            location.flat_height = f"{flat_height_value} {flat_height_unit}"
        else:
            location.flat_height = flat_height_value or None
        flat_row_width_value = request.form.get("flat_row_width_value", "").strip()
        flat_row_width_unit = request.form.get("flat_row_width_unit", "").strip().lower()
        if flat_row_width_value and flat_row_width_unit:
            location.flat_row_width = f"{flat_row_width_value} {flat_row_width_unit}"
        else:
            location.flat_row_width = flat_row_width_value or None
        flat_weight_value = request.form.get("flat_weight_value", "").strip()
        flat_weight_unit = request.form.get("flat_weight_unit", "").strip().lower()
        if flat_weight_value and flat_weight_unit:
            location.flat_weight = f"{flat_weight_value} {flat_weight_unit}"
        else:
            location.flat_weight = flat_weight_value or None
        location.flat_scale = request.form.get("flat_scale", "").strip() or None
        location.flat_gauge = request.form.get("flat_gauge", "").strip() or None
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.location_detail", location_id=location.id))
    locations = Location.query.order_by("name").all()
    location_types = current_app.config.get("LOCATION_TYPES", [])
    external_length_value, external_length_unit = parse_actual_length(location.external_length)
    if external_length_value and not external_length_unit:
        external_length_unit = get_default_length_unit()
    external_width_value, external_width_unit = parse_actual_length(location.external_width)
    if external_width_value and not external_width_unit:
        external_width_unit = get_default_length_unit()
    external_height_value, external_height_unit = parse_actual_length(location.external_height)
    if external_height_value and not external_height_unit:
        external_height_unit = get_default_length_unit()
    external_weight_value, external_weight_unit = parse_actual_weight(location.external_weight)
    if external_weight_value and not external_weight_unit:
        external_weight_unit = get_default_weight_unit()
    flat_length_value, flat_length_unit = parse_actual_length(location.flat_length)
    if flat_length_value and not flat_length_unit:
        flat_length_unit = get_default_length_unit()
    flat_height_value, flat_height_unit = parse_actual_length(location.flat_height)
    if flat_height_value and not flat_height_unit:
        flat_height_unit = get_default_length_unit()
    flat_row_width_value, flat_row_width_unit = parse_actual_length(location.flat_row_width)
    if flat_row_width_value and not flat_row_width_unit:
        flat_row_width_unit = get_default_length_unit()
    flat_weight_value, flat_weight_unit = parse_actual_weight(location.flat_weight)
    if flat_weight_value and not flat_weight_unit:
        flat_weight_unit = get_default_weight_unit()
    return render_template(
        "location_form.html",
        location=location,
        owner_username=(location.owner.username if location.owner else ""),
        enabled_users=get_enabled_users(),
        locations=locations,
        descendant_ids=descendant_ids,
        location_types=location_types,
        external_length_value=external_length_value,
        external_length_unit=external_length_unit or get_default_length_unit(),
        external_width_value=external_width_value,
        external_width_unit=external_width_unit or get_default_length_unit(),
        external_height_value=external_height_value,
        external_height_unit=external_height_unit or get_default_length_unit(),
        external_weight_value=external_weight_value,
        external_weight_unit=external_weight_unit or get_default_weight_unit(),
        flat_length_value=flat_length_value,
        flat_length_unit=flat_length_unit or get_default_length_unit(),
        flat_rows_value=str(location.flat_rows) if location.flat_rows is not None else "",
        flat_height_value=flat_height_value,
        flat_height_unit=flat_height_unit or get_default_length_unit(),
        flat_row_width_value=flat_row_width_value,
        flat_row_width_unit=flat_row_width_unit or get_default_length_unit(),
        flat_weight_value=flat_weight_value,
        flat_weight_unit=flat_weight_unit or get_default_weight_unit(),
        flat_scale_value=location.flat_scale or "",
        flat_gauge_value=location.flat_gauge or "",
        scale_options=get_scale_options(),
        gauge_options=get_gauge_options(),
    )


@main_bp.route("/locations/<int:location_id>/delete", methods=["POST"])
def location_delete(location_id: int):
    location = Location.query.get_or_404(location_id)
    if Car.query.filter_by(location_id=location.id).count() > 0:
        return "Cannot delete location with cars assigned.", 400
    if Location.query.filter_by(parent_id=location.id).count() > 0:
        return "Cannot delete location with child locations assigned.", 400
    db.session.delete(location)
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.locations"))


@main_bp.route("/cars/<int:car_id>")
def car_detail(car_id: int):
    car = Car.query.get_or_404(car_id)
    compare_cars = []
    for other in Car.query.order_by("id").all():
        if other.id == car.id:
            continue
        reporting_mark = other.railroad.reporting_mark if other.railroad else other.reporting_mark_override
        compare_cars.append(
            {
                "id": other.id,
                "reporting_mark": reporting_mark,
                "car_number": other.car_number,
            }
        )
    return render_template(
        "car_detail.html",
        car=car,
        compare_cars=compare_cars,
        car_payload=serialize_car(car),
        scale_label=format_scale_label(car.scale),
        gauge_label=format_gauge_label(car.gauge),
        linear_density=calculate_linear_density(car),
    )


@main_bp.route("/cars/<int:car_id>/inspect", methods=["GET", "POST"])
def car_inspect(car_id: int):
    car = Car.query.get_or_404(car_id)
    inspection_types = InspectionType.query.all()
    type_rows = inspection_type_tree(inspection_types)
    if request.method == "POST":
        inspection_date = request.form.get("inspection_date", "").strip()
        inspection_details = request.form.get("inspection_details", "").strip()
        inspection_type_id = request.form.get("inspection_type_id", "").strip()
        inspection_passed = request.form.get("inspection_passed", "").strip()
        if not inspection_date:
            return "Inspection date is required.", 400
        if not inspection_type_id.isdigit():
            return "Inspection type is required.", 400
        if inspection_passed not in {"passed", "failed"}:
            return "Inspection result is required.", 400
        inspection = CarInspection(
            car_id=car.id,
            inspection_date=inspection_date,
            details=inspection_details or None,
            inspection_type_id=int(inspection_type_id),
            passed=inspection_passed == "passed",
        )
        db.session.add(inspection)
        car.last_inspection_date = inspection_date
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.car_detail", car_id=car.id))
    return render_template("car_inspection_form.html", car=car, inspection_types=type_rows)


@main_bp.route("/cars/<int:car_id>/delete", methods=["POST"])
def car_delete(car_id: int):
    car = Car.query.get_or_404(car_id)
    db.session.delete(car)
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.inventory"))


@main_bp.route("/cars/by-number")
def car_by_number():
    number = request.args.get("number", "").strip()
    if not number:
        return redirect(url_for("main.inventory"))
    normalized = number.strip()
    if normalized.lower().startswith("c") and normalized[1:].isdigit():
        car = Car.query.get(int(normalized[1:]))
        if car:
            return redirect(url_for("main.car_detail", car_id=car.id))
    cars = Car.query.filter_by(car_number=number).order_by("id", reverse=True).all()
    if len(cars) == 1:
        return redirect(url_for("main.car_detail", car_id=cars[0].id))
    if not cars and number.isdigit():
        car = Car.query.get(int(number))
        if car:
            return redirect(url_for("main.car_detail", car_id=car.id))
    return render_template("car_number_list.html", number=number, cars=cars)


def build_unique_text_values(values: list[str | None]) -> list[str]:
    seen = set()
    options: list[str] = []
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if not normalized:
            continue
        lower = normalized.lower()
        if lower in {"none", "null", "-"}:
            continue
        if lower in seen:
            continue
        seen.add(lower)
        options.append(normalized)
    return options


@main_bp.route("/cars/<int:car_id>/edit", methods=["GET", "POST"])
def car_edit(car_id: int):
    car = Car.query.get_or_404(car_id)
    if request.method == "POST":
        previous_weight = car.actual_weight
        previous_length = car.actual_length
        previous_scale = car.scale
        try:
            apply_car_form(car, request.form)
        except ValueError as exc:
            return str(exc), 400
        db.session.commit()
        ensure_db_backup()
        maybe_run_nmra_weight_check(car, previous_weight, previous_length, previous_scale)
        maybe_run_nmra_loaded_weight_check(car, previous_weight, previous_length, previous_scale)
        return redirect(url_for("main.car_detail", car_id=car.id))
    railroads = Railroad.query.order_by("reporting_mark").all()
    classes = CarClass.query.order_by("code").all()
    locations = Location.query.order_by("name").all()
    enabled_users = get_enabled_users()
    wheel_arrangements = build_unique_text_values([c.wheel_arrangement for c in classes])
    tender_arrangements = build_unique_text_values([c.tender_axles for c in classes])
    scale_value = normalize_scale_input(car.scale)
    gauge_value = normalize_gauge_input(car.gauge)
    actual_weight_value, actual_weight_unit = parse_actual_weight(car.actual_weight)
    actual_length_value, actual_length_unit = parse_actual_length(car.actual_length)
    if not actual_weight_unit:
        actual_weight_unit = get_default_weight_unit()
    if not actual_length_unit:
        actual_length_unit = get_default_length_unit()
    load_length_value, load_length_unit = parse_actual_length(car.load_length)
    load_width_value, load_width_unit = parse_actual_length(car.load_width)
    load_height_value, load_height_unit = parse_actual_length(car.load_height)
    if not load_length_unit:
        load_length_unit = get_default_length_unit()
    if not load_width_unit:
        load_width_unit = get_default_length_unit()
    if not load_height_unit:
        load_height_unit = get_default_length_unit()
    return render_template(
        "car_form.html",
        car=car,
        owner_username=(car.owner.username if car.owner else ""),
        enabled_users=enabled_users,
        railroads=railroads,
        classes=classes,
        locations=locations,
        wheel_arrangements=wheel_arrangements,
        tender_arrangements=tender_arrangements,
        prefill={},
        scale_options=get_scale_options(),
        gauge_options=get_gauge_options(),
        scale_value=scale_value,
        gauge_value=gauge_value,
        actual_weight_value=actual_weight_value,
        actual_weight_unit=actual_weight_unit,
        actual_length_value=actual_length_value,
        actual_length_unit=actual_length_unit,
        load_length_value=load_length_value,
        load_length_unit=load_length_unit,
        load_width_value=load_width_value,
        load_width_unit=load_width_unit,
        load_height_value=load_height_value,
        load_height_unit=load_height_unit,
        common_scale_gauge=get_common_scale_gauge_pairs(),
        power_type_options=CONSIST_POWER_TYPES,
        form_action=url_for("main.car_edit", car_id=car.id),
    )


@main_bp.route("/cars/new", methods=["GET", "POST"])
def car_new():
    if request.method == "POST":
        car = Car()
        try:
            apply_car_form(car, request.form)
        except ValueError as exc:
            return str(exc), 400
        db.session.add(car)
        db.session.commit()
        ensure_db_backup()
        maybe_run_nmra_weight_check(car, None, None, None)
        maybe_run_nmra_loaded_weight_check(car, None, None, None)
        return redirect(url_for("main.car_detail", car_id=car.id))
    prefill = {
        "reporting_mark": request.args.get("reporting_mark", "").strip(),
        "railroad_name": request.args.get("railroad_name", "").strip(),
        "car_class": request.args.get("car_class", "").strip(),
        "car_type": request.args.get("car_type", "").strip(),
        "power_type": request.args.get("power_type", "").strip(),
        "aar_plate": request.args.get("aar_plate", "").strip(),
        "capacity": request.args.get("capacity", "").strip(),
        "weight": request.args.get("weight", "").strip(),
        "load_limit": request.args.get("load_limit", "").strip(),
        "actual_weight": request.args.get("actual_weight", "").strip(),
        "actual_length": request.args.get("actual_length", "").strip(),
        "scale": request.args.get("scale", "").strip(),
        "gauge": request.args.get("gauge", "").strip(),
        "load_length": request.args.get("load_length", "").strip(),
        "load_width": request.args.get("load_width", "").strip(),
        "load_height": request.args.get("load_height", "").strip(),
        "built": request.args.get("built", "").strip(),
        "brand": request.args.get("brand", "").strip(),
        "price": request.args.get("price", "").strip(),
        "msrp": request.args.get("msrp", "").strip(),
        "location": request.args.get("location", "").strip(),
    }
    railroads = Railroad.query.order_by("reporting_mark").all()
    classes = CarClass.query.order_by("code").all()
    locations = Location.query.order_by("name").all()
    enabled_users = get_enabled_users()
    wheel_arrangements = build_unique_text_values([c.wheel_arrangement for c in classes])
    tender_arrangements = build_unique_text_values([c.tender_axles for c in classes])
    scale_value = normalize_scale_input(prefill.get("scale", ""))
    gauge_value = normalize_gauge_input(prefill.get("gauge", ""))
    actual_weight_value, actual_weight_unit = parse_actual_weight(prefill.get("actual_weight", ""))
    actual_length_value, actual_length_unit = parse_actual_length(prefill.get("actual_length", ""))
    if not actual_weight_unit:
        actual_weight_unit = get_default_weight_unit()
    if not actual_length_unit:
        actual_length_unit = get_default_length_unit()
    load_length_value, load_length_unit = parse_actual_length(prefill.get("load_length", ""))
    load_width_value, load_width_unit = parse_actual_length(prefill.get("load_width", ""))
    load_height_value, load_height_unit = parse_actual_length(prefill.get("load_height", ""))
    if not load_length_unit:
        load_length_unit = get_default_length_unit()
    if not load_width_unit:
        load_width_unit = get_default_length_unit()
    if not load_height_unit:
        load_height_unit = get_default_length_unit()
    return render_template(
        "car_form.html",
        car=None,
        owner_username=(g.current_user.username if getattr(g, "current_user", None) else ""),
        enabled_users=enabled_users,
        railroads=railroads,
        classes=classes,
        locations=locations,
        wheel_arrangements=wheel_arrangements,
        tender_arrangements=tender_arrangements,
        prefill=prefill,
        scale_options=get_scale_options(),
        gauge_options=get_gauge_options(),
        scale_value=scale_value,
        gauge_value=gauge_value,
        actual_weight_value=actual_weight_value,
        actual_weight_unit=actual_weight_unit,
        actual_length_value=actual_length_value,
        actual_length_unit=actual_length_unit,
        load_length_value=load_length_value,
        load_length_unit=load_length_unit,
        load_width_value=load_width_value,
        load_width_unit=load_width_unit,
        load_height_value=load_height_value,
        load_height_unit=load_height_unit,
        common_scale_gauge=get_common_scale_gauge_pairs(),
        power_type_options=CONSIST_POWER_TYPES,
        form_action=url_for("main.car_new"),
    )


@main_bp.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if query.lower().startswith("c") and query[1:].isdigit():
        car = Car.query.get(int(query[1:]))
        if car:
            return redirect(url_for("main.car_detail", car_id=car.id))
    cars = search_cars(query)
    parts = search_parts(query)
    needle = query.lower()
    if needle:
        car_number_match = any(car.car_number == query for car in cars if car.car_number)
        location_matches = [
            location
            for location in Location.query.all()
            if location.name and location.name.strip().lower() == needle
        ]
        if len(location_matches) == 1 and not car_number_match:
            return redirect(url_for("main.location_detail", location_id=location_matches[0].id))
    return render_template("search.html", cars=cars, parts=parts, query=query)


@main_bp.route("/api/cars")
def api_cars():
    page_value = request.args.get("page", "").strip()
    page_size_value = request.args.get("page_size", "").strip()
    page_size = normalize_page_size(page_size_value) if page_size_value else ""
    page = int(page_value) if page_value.isdigit() else 1
    car_query = Car.query.order_by("id", reverse=True)
    if not page_size and not page_value:
        cars = car_query.all()
        prefetch_car_relations(cars)
        return jsonify([serialize_car(car) for car in cars])
    if not page_size:
        page_size = get_page_size()
    if page_size not in PAGINATION_OPTIONS:
        return jsonify({"error": "Invalid page size."}), 400
    paged_cars, pagination = paginate_query(car_query, page, page_size, "main.api_cars", {})
    prefetch_car_relations(paged_cars)
    return jsonify({"items": [serialize_car(car) for car in paged_cars], "pagination": pagination})


@main_bp.route("/api/cars/<int:car_id>")
def api_car_detail(car_id: int):
    car = Car.query.get_or_404(car_id)
    return jsonify(serialize_car(car))


@main_bp.route("/api/railroads")
def api_railroads():
    railroads = Railroad.query.all()
    return jsonify([
        {
            "id": r.id,
            "reporting_mark": r.reporting_mark,
            "name": r.name,
            "start_date": r.start_date,
            "end_date": r.end_date,
            "merged_into": r.merged_into,
            "merged_from": r.merged_from,
            "notes": r.notes,
        }
        for r in railroads
    ])


@main_bp.route("/api/car-classes")
def api_car_classes():
    classes = CarClass.query.all()
    return jsonify([
        {
            "id": c.id,
            "code": c.code,
            "car_type": c.car_type,
            "is_locomotive": c.is_locomotive,
            "power_type": c.power_type,
            "era": c.era,
            "wheel_arrangement": c.wheel_arrangement,
            "tender_axles": c.tender_axles,
            "capacity": c.capacity,
            "weight": c.weight,
            "load_limit": c.load_limit,
            "aar_plate": c.aar_plate,
            "internal_length": c.internal_length,
            "internal_width": c.internal_width,
            "internal_height": c.internal_height,
            "external_length": c.external_length,
            "external_width": c.external_width,
            "external_height": c.external_height,
            "cubic_feet": c.cubic_feet,
            "notes": c.notes,
        }
        for c in classes
    ])


@main_bp.route("/api/search")
def api_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"cars": [], "parts": []})
    cars = search_cars(query)
    parts = search_parts(query)
    return jsonify(
        {
            "cars": [serialize_car(car) for car in cars],
            "parts": [serialize_part(part) for part in parts],
        }
    )


def search_cars(query: str) -> list[Car]:
    if not query:
        return []
    needle = query.lower()

    def matches(value: str | None) -> bool:
        return bool(value) and needle in value.lower()

    results = []
    for car in Car.query.all():
        values = [
            car.car_number,
            car.reporting_mark_override,
            car.car_type_override,
            car.load,
            car.notes,
            car.actual_weight,
            car.actual_length,
            car.scale,
            car.gauge,
            car.upc,
            str(car.id),
            f"c{car.id}",
        ]
        if car.car_class:
            values.extend([car.car_class.code, car.car_class.car_type])
        if car.railroad:
            values.extend([car.railroad.reporting_mark, car.railroad.name])
        if car.location:
            values.append(car.location.name)
        if any(matches(value) for value in values):
            results.append(car)
    return results


def search_parts(query: str) -> list[PartItem]:
    if not query:
        return []
    needle = query.lower()

    def matches(value: str | None) -> bool:
        return bool(value) and needle in value.lower()

    results = []
    for part in PartItem.query.all():
        values = [
            part.name,
            part.description,
            part.brand,
            part.upc,
            str(part.id),
        ]
        if part.quantity is not None:
            values.append(str(part.quantity))
        if part.location:
            values.append(part.location.name)
        if any(matches(value) for value in values):
            results.append(part)
    return results


def apply_load_form(load: LoadType, form) -> None:
    owner = resolve_owner_from_form(form.get("owner_username"))
    if form.get("owner_username", "").strip() and owner is None:
        raise ValueError("Owner must be an enabled user.")
    load.owner_user_id = owner.id if owner else None
    load.name = form.get("name", "").strip()
    load.era = form.get("era", "").strip()
    load.brand = form.get("brand", "").strip()
    load.lettering = form.get("lettering", "").strip()
    load.msrp = form.get("msrp", "").strip()
    load.price = form.get("price", "").strip()
    load.upc = form.get("upc", "").strip()
    def read_measurement(value_key: str, unit_key: str, fallback_key: str) -> str | None:
        value = form.get(value_key, "").strip()
        unit = form.get(unit_key, "").strip()
        if value and unit:
            return f"{value} {unit}"
        if value:
            return value
        fallback = form.get(fallback_key, "").strip()
        return fallback or None

    load.length = read_measurement("length_value", "length_unit", "length")
    load.width = read_measurement("width_value", "width_unit", "width")
    load.height = read_measurement("height_value", "height_unit", "height")
    load.weight = read_measurement("weight_value", "weight_unit", "weight")
    load.repairs_required = form.get("repairs_required", "").strip()
    load.notes = form.get("notes", "").strip()
    class_id = form.get("car_class_id", "").strip()
    if class_id and class_id.isdigit():
        load.car_class_id = int(class_id)
    else:
        load.car_class_id = None
    railroad_id = form.get("railroad_id", "").strip()
    if railroad_id and railroad_id.isdigit():
        load.railroad_id = int(railroad_id)
    else:
        load.railroad_id = None


def apply_load_placement_form(placement: LoadPlacement, form) -> bool:
    quantity = form.get("quantity", "").strip()
    placement.quantity = int(quantity) if quantity.isdigit() and int(quantity) > 0 else 1
    car_id = form.get("car_id", "").strip()
    location_id = form.get("location_id", "").strip()
    placement.car_id = int(car_id) if car_id.isdigit() else None
    placement.location_id = int(location_id) if location_id.isdigit() else None
    if placement.car_id and placement.location_id:
        placement.location_id = None
    return bool(placement.car_id or placement.location_id)


def apply_car_form(car: Car, form) -> None:
    owner = resolve_owner_from_form(form.get("owner_username"))
    if form.get("owner_username", "").strip() and owner is None:
        raise ValueError("Owner must be an enabled user.")
    car.owner_user_id = owner.id if owner else None
    has_reporting_mark = "reporting_mark" in form
    has_railroad_name = "railroad_name" in form
    reporting_mark = (
        form.get("reporting_mark", car.railroad.reporting_mark if car.railroad else "").strip()
        if has_reporting_mark
        else None
    )
    if reporting_mark and reporting_mark.lower() in {"none", "null"}:
        reporting_mark = ""
    clear_railroad = form.get("clear_railroad") == "1"
    railroad_name = (
        form.get("railroad_name", car.railroad.name if car.railroad else "").strip()
        if has_railroad_name
        else None
    )
    railroad = car.railroad
    if clear_railroad:
        railroad = None
    elif has_reporting_mark or has_railroad_name:
        if reporting_mark:
            railroad = Railroad.query.filter_by(reporting_mark=reporting_mark).first()
            if not railroad:
                railroad = Railroad(reporting_mark=reporting_mark, name=railroad_name or reporting_mark)
                db.session.add(railroad)
        elif railroad_name:
            railroad = Railroad.query.filter_by(name=railroad_name).first()
            if not railroad:
                railroad = Railroad(reporting_mark=None, name=railroad_name)
                db.session.add(railroad)
    car.railroad = railroad
    if clear_railroad or has_reporting_mark or has_railroad_name:
        if railroad is None:
            car.reporting_mark_override = reporting_mark or None
        else:
            car.reporting_mark_override = None

    car_type_value = form.get("car_type", "").strip()
    power_type_value = form.get("power_type", "").strip()
    car.car_number = form.get("car_number", "").strip()
    car.brand = form.get("brand", "").strip()
    car.upc = form.get("upc", "").strip()
    car.dcc_id = form.get("dcc_id", "").strip()
    car.dcc_decoder_type = form.get("dcc_decoder_type", "").strip()
    car.dcc_decoder_connection_type = form.get("dcc_decoder_connection_type", "").strip()
    car.dcc_decoder_keep_alive = form.get("dcc_decoder_keep_alive") == "on"
    car.traction_drivers = form.get("traction_drivers") == "on"
    capacity_value = form.get("capacity", "").strip()
    weight_value = form.get("weight", "").strip()
    load_limit_value = form.get("load_limit", "").strip()
    aar_plate_value = form.get("aar_plate", "").strip()
    actual_weight_value = form.get("actual_weight_value", "").strip()
    actual_weight_unit = form.get("actual_weight_unit", "").strip()
    if actual_weight_value and actual_weight_unit:
        car.actual_weight = f"{actual_weight_value} {actual_weight_unit}"
    else:
        car.actual_weight = actual_weight_value or None
    actual_length_value = form.get("actual_length_value", "").strip()
    actual_length_unit = form.get("actual_length_unit", "").strip()
    if actual_length_value and actual_length_unit:
        car.actual_length = f"{actual_length_value} {actual_length_unit}"
    else:
        car.actual_length = actual_length_value or None
    def read_measurement(value_key: str, unit_key: str) -> str | None:
        value = form.get(value_key, "").strip()
        unit = form.get(unit_key, "").strip()
        if value and unit:
            return f"{value} {unit}"
        return value or None

    car.load_length = read_measurement("load_length_value", "load_length_unit")
    car.load_width = read_measurement("load_width_value", "load_width_unit")
    car.load_height = read_measurement("load_height_value", "load_height_unit")
    scale_value = normalize_scale_input(form.get("scale", ""))
    gauge_value = normalize_gauge_input(form.get("gauge", ""))
    car.scale = scale_value or None
    car.gauge = gauge_value or None
    car.built = form.get("built", "").strip()
    car.alt_date = form.get("alt_date", "").strip()
    car.reweight_date = form.get("reweight_date", "").strip()
    car.repack_bearings_date = form.get("repack_bearings_date", "").strip()
    car.other_lettering = form.get("other_lettering", "").strip()
    car.msrp = form.get("msrp", "").strip()
    car.price = form.get("price", "").strip()
    car.load = form.get("load", "").strip()
    car.repairs_required = form.get("repairs_required", "").strip()
    car.notes = form.get("notes", "").strip()

    class_code = form.get("car_class", "").strip()
    if class_code:
        car_class = CarClass.query.filter_by(code=class_code).first()
        created_class = False
        if not car_class:
            car_class = CarClass(code=class_code)
            db.session.add(car_class)
            created_class = True
        class_wheel = form.get("class_wheel_arrangement", "").strip()
        class_tender = form.get("class_tender_axles", "").strip()
        class_is_locomotive = form.get("is_locomotive") == "on"
        if class_wheel and not car_class.wheel_arrangement:
            car_class.wheel_arrangement = class_wheel
        if class_tender and not car_class.tender_axles:
            car_class.tender_axles = class_tender
        if car_type_value and not car_class.car_type:
            car_class.car_type = car_type_value
        if power_type_value and not car_class.power_type:
            car_class.power_type = power_type_value
        if car_class.is_locomotive is None:
            car_class.is_locomotive = class_is_locomotive

        if capacity_value and (created_class or not car_class.capacity):
            car_class.capacity = capacity_value
        if weight_value and (created_class or not car_class.weight):
            car_class.weight = weight_value
        if load_limit_value and (created_class or not car_class.load_limit):
            car_class.load_limit = load_limit_value
        if aar_plate_value and not car_class.aar_plate:
            car_class.aar_plate = aar_plate_value
        class_internal_length = form.get("internal_length", "").strip()
        class_internal_width = form.get("internal_width", "").strip()
        class_internal_height = form.get("internal_height", "").strip()
        class_external_length = form.get("external_length", "").strip()
        class_external_width = form.get("external_width", "").strip()
        class_external_height = form.get("external_height", "").strip()
        class_cubic_feet = form.get("cubic_feet", "").strip()
        if class_internal_length and not car_class.internal_length:
            car_class.internal_length = class_internal_length
        if class_internal_width and not car_class.internal_width:
            car_class.internal_width = class_internal_width
        if class_internal_height and not car_class.internal_height:
            car_class.internal_height = class_internal_height
        if class_external_length and not car_class.external_length:
            car_class.external_length = class_external_length
        if class_external_width and not car_class.external_width:
            car_class.external_width = class_external_width
        if class_external_height and not car_class.external_height:
            car_class.external_height = class_external_height
        if class_cubic_feet and not car_class.cubic_feet:
            car_class.cubic_feet = class_cubic_feet

        if created_class:
            car.capacity_override = None
            car.weight_override = None
            car.load_limit_override = None
            car.aar_plate_override = None
            car.car_type_override = None
            car.power_type_override = None
            car.wheel_arrangement_override = None
            car.tender_axles_override = None
            car.is_locomotive_override = None
            car.internal_length_override = None
            car.internal_width_override = None
            car.internal_height_override = None
            car.external_length_override = None
            car.external_width_override = None
            car.external_height_override = None
            car.cubic_feet_override = None
        else:
            car.capacity_override = (
                capacity_value if capacity_value and car_class.capacity and capacity_value != car_class.capacity else None
            )
            car.weight_override = (
                weight_value if weight_value and car_class.weight and weight_value != car_class.weight else None
            )
            car.load_limit_override = (
                load_limit_value if load_limit_value and car_class.load_limit and load_limit_value != car_class.load_limit else None
            )
            car.aar_plate_override = (
                aar_plate_value if aar_plate_value and car_class.aar_plate and aar_plate_value != car_class.aar_plate else None
            )
            car.internal_length_override = (
                class_internal_length
                if class_internal_length and car_class.internal_length and class_internal_length != car_class.internal_length
                else None
            )
            car.internal_width_override = (
                class_internal_width
                if class_internal_width and car_class.internal_width and class_internal_width != car_class.internal_width
                else None
            )
            car.internal_height_override = (
                class_internal_height
                if class_internal_height and car_class.internal_height and class_internal_height != car_class.internal_height
                else None
            )
            car.external_length_override = (
                class_external_length
                if class_external_length and car_class.external_length and class_external_length != car_class.external_length
                else None
            )
            car.external_width_override = (
                class_external_width
                if class_external_width and car_class.external_width and class_external_width != car_class.external_width
                else None
            )
            car.external_height_override = (
                class_external_height
                if class_external_height and car_class.external_height and class_external_height != car_class.external_height
                else None
            )
            car.cubic_feet_override = (
                class_cubic_feet
                if class_cubic_feet and car_class.cubic_feet and class_cubic_feet != car_class.cubic_feet
                else None
            )
            car.car_type_override = (
                car_type_value if car_type_value and car_class.car_type and car_type_value != car_class.car_type else None
            )
            car.power_type_override = (
                power_type_value
                if power_type_value and car_class.power_type and power_type_value != car_class.power_type
                else None
            )
            car.wheel_arrangement_override = (
                class_wheel
                if class_wheel and car_class.wheel_arrangement and class_wheel != car_class.wheel_arrangement
                else None
            )
            car.tender_axles_override = (
                class_tender
                if class_tender and car_class.tender_axles and class_tender != car_class.tender_axles
                else None
            )
            if car_class.is_locomotive is not None:
                car.is_locomotive_override = (
                    class_is_locomotive if class_is_locomotive != car_class.is_locomotive else None
                )
        car.car_class = car_class
    else:
        car.car_class = None
        car.capacity_override = capacity_value or None
        car.weight_override = weight_value or None
        car.load_limit_override = load_limit_value or None
        car.aar_plate_override = aar_plate_value or None
        car.car_type_override = car_type_value or None
        car.power_type_override = power_type_value or None
        car.wheel_arrangement_override = form.get("class_wheel_arrangement", "").strip() or None
        car.tender_axles_override = form.get("class_tender_axles", "").strip() or None
        car.is_locomotive_override = True if form.get("is_locomotive") == "on" else None
        car.internal_length_override = form.get("internal_length", "").strip() or None
        car.internal_width_override = form.get("internal_width", "").strip() or None
        car.internal_height_override = form.get("internal_height", "").strip() or None
        car.external_length_override = form.get("external_length", "").strip() or None
        car.external_width_override = form.get("external_width", "").strip() or None
        car.external_height_override = form.get("external_height", "").strip() or None
        car.cubic_feet_override = form.get("cubic_feet", "").strip() or None

    location_name = form.get("location", "").strip()
    if location_name:
        car.location = get_or_create_location(location_name)
    else:
        car.location = None


@main_bp.route("/administration")
def administration():
    return render_template("administration.html", admin=get_administration_summary())


@main_bp.route("/administration/users")
def administration_users():
    return render_template("admin_users.html", users=get_admin_user_rows())


@main_bp.route("/administration/users/new", methods=["GET", "POST"])
def administration_users_new():
    form_values = build_user_form_defaults()
    if request.method == "POST":
        form_values["username"] = request.form.get("username", "").strip()
        form_values["display_name"] = request.form.get("display_name", "").strip()
        form_values["email"] = request.form.get("email", "").strip()
        form_values["permissions"] = request.form.get("permissions", "").strip() or DEFAULT_PERMISSION_TEXT
        try:
            user = create_user_from_form(force_superuser=False)
        except (ValueError, json.JSONDecodeError) as exc:
            flash(str(exc), "danger")
            return render_template(
                "user_form.html",
                form_values=form_values,
                mode="create",
                permission_examples=get_permission_examples(),
            ), 400
        flash(f"Created user {get_user_label(user)}.", "success")
        ensure_db_backup()
        return redirect(url_for("main.administration_users"))
    return render_template(
        "user_form.html",
        form_values=form_values,
        mode="create",
        permission_examples=get_permission_examples(),
    )


@main_bp.route("/administration/users/<int:user_id>/permissions", methods=["GET", "POST"])
def administration_users_permissions(user_id: int):
    user = User.query.get_or_404(user_id)
    form_values = {
        "permissions": format_permissions(user.permissions),
    }
    if request.method == "POST":
        form_values["permissions"] = request.form.get("permissions", "").strip() or DEFAULT_PERMISSION_TEXT
        try:
            user.permissions = parse_permissions(request.form.get("permissions"))
        except (ValueError, json.JSONDecodeError) as exc:
            flash(str(exc), "danger")
            return render_template(
                "user_form.html",
                mode="edit_permissions",
                target_user=user,
                form_values=form_values,
                permission_examples=get_permission_examples(),
            ), 400
        db.session.commit()
        ensure_db_backup()
        flash(f"Updated permissions for {get_user_label(user)}.", "success")
        return redirect(url_for("main.administration_users"))
    return render_template(
        "user_form.html",
        mode="edit_permissions",
        target_user=user,
        form_values=form_values,
        permission_examples=get_permission_examples(),
    )


@main_bp.route("/administration/users/<int:user_id>/reset-password", methods=["GET", "POST"])
def administration_users_reset_password(user_id: int):
    user = User.query.get_or_404(user_id)
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("user_form.html", mode="reset_password", target_user=user), 400
        try:
            validate_password_strength(password)
        except ValueError as exc:
            flash(str(exc), "danger")
            return render_template("user_form.html", mode="reset_password", target_user=user), 400
        bump_session_version(user)
        revoke_user_mfa(user)
        user.password_hash = hash_password(password)
        user.failed_password_attempts = 0
        user.failed_mfa_attempts = 0
        user.locked_until = None
        db.session.commit()
        ensure_db_backup()
        flash(f"Password reset for {get_user_label(user)}. Existing MFA credentials were revoked.", "success")
        return redirect(url_for("main.administration_users"))
    return render_template("user_form.html", mode="reset_password", target_user=user)


@main_bp.route("/administration/users/<int:user_id>/toggle-active", methods=["POST"])
def administration_users_toggle_active(user_id: int):
    user = User.query.get_or_404(user_id)
    if g.current_user and user.id == g.current_user.id:
        flash("You cannot disable your own account from this page.", "danger")
        return redirect(url_for("main.administration_users"))
    if user.is_active and user_count() <= 1:
        flash("The last user account cannot be disabled.", "danger")
        return redirect(url_for("main.administration_users"))
    user.is_active = not user.is_active
    db.session.commit()
    ensure_db_backup()
    flash(f"{'Enabled' if user.is_active else 'Disabled'} {get_user_label(user)}.", "success")
    return redirect(url_for("main.administration_users"))


@main_bp.route("/administration/users/<int:user_id>/delete", methods=["POST"])
def administration_users_delete(user_id: int):
    user = User.query.get_or_404(user_id)
    if g.current_user and user.id == g.current_user.id:
        flash("You cannot delete your own account from this page.", "danger")
        return redirect(url_for("main.administration_users"))
    if user_count() <= 1:
        flash("The last user account cannot be deleted.", "danger")
        return redirect(url_for("main.administration_users"))
    db.session.delete(user)
    db.session.commit()
    ensure_db_backup()
    flash(f"Deleted {get_user_label(user)}.", "success")
    return redirect(url_for("main.administration_users"))


@main_bp.route("/administration/backup")
def administration_backup():
    payload = create_backup_payload(
        db.store.db,
        app_metadata=get_backup_application_metadata(),
        asset_roots=get_managed_asset_roots(),
    )
    response = Response(dump_backup_payload(payload), mimetype="application/json")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    response.headers["Content-Disposition"] = (
        f'attachment; filename="railroad-inventory-backup-{timestamp}.json"'
    )
    return response


@main_bp.route("/administration/restore", methods=["POST"])
def administration_restore():
    uploaded = request.files.get("backup_file")
    if not uploaded or not uploaded.filename:
        flash("Select a backup file to restore.", "danger")
        return redirect(url_for("main.administration"))
    try:
        payload = load_backup_payload(uploaded.read())
        db.store.replace_database(current_app, payload["docs"])
        restore_asset_files(get_managed_asset_roots(), payload.get("assets"))
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("main.administration"))
    except Exception:
        flash("Restore failed while rebuilding the database.", "danger")
        return redirect(url_for("main.administration"))
    backup_created = payload.get("created_at") or "unknown time"
    restored_count = len(payload.get("docs", []))
    asset_count = len(payload.get("assets", []))
    flash(
        f"Restored {restored_count} documents and {asset_count} asset files from backup created {backup_created}.",
        "success",
    )
    return redirect(url_for("main.administration"))


@main_bp.route("/settings")
def settings():
    inspection_types = InspectionType.query.all()
    type_rows = inspection_type_tree(inspection_types)
    page_size = get_page_size()
    scale_options_text = get_scale_options_text()
    gauge_options_text = get_gauge_options_text()
    foam_blocks = get_foam_blocks()
    foam_blocks_text = get_foam_blocks_text()
    default_length_unit = get_default_length_unit()
    default_weight_unit = get_default_weight_unit()
    options = [
        {"value": value, "label": "All" if value == "all" else value} for value in PAGINATION_OPTIONS
    ]
    return render_template(
        "settings.html",
        inspection_types=type_rows,
        page_size=page_size,
        page_size_options=options,
        scale_options_text=scale_options_text,
        gauge_options_text=gauge_options_text,
        foam_blocks=foam_blocks,
        foam_blocks_text=foam_blocks_text,
        default_length_unit=default_length_unit,
        default_weight_unit=default_weight_unit,
        length_units=LENGTH_UNITS,
        weight_units=WEIGHT_UNITS,
    )


@main_bp.route("/settings/pagination", methods=["POST"])
def settings_pagination():
    page_size = request.form.get("page_size", "").strip()
    if page_size not in PAGINATION_OPTIONS:
        return "Invalid pagination size.", 400
    settings = get_app_settings()
    settings.page_size = page_size
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.settings"))


@main_bp.route("/settings/scale-gauge", methods=["POST"])
def settings_scale_gauge():
    scale_text = request.form.get("scale_options", "").strip()
    gauge_text = request.form.get("gauge_options", "").strip()
    scale_options = build_scale_options(scale_text)
    gauge_options = build_gauge_options(gauge_text)
    settings = get_app_settings()
    settings.scale_options = "\n".join(
        option["raw"] for option in scale_options if option.get("raw")
    )
    settings.gauge_options = "\n".join(
        option["raw"] for option in gauge_options if option.get("raw")
    )
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.settings"))


@main_bp.route("/settings/units", methods=["POST"])
def settings_units():
    length_unit = request.form.get("default_length_unit", "").strip().lower()
    weight_unit = request.form.get("default_weight_unit", "").strip().lower()
    if length_unit not in LENGTH_UNIT_TO_IN:
        return "Invalid length unit.", 400
    if weight_unit not in WEIGHT_UNIT_TO_OZ:
        return "Invalid weight unit.", 400
    settings = get_app_settings()
    settings.default_length_unit = length_unit
    settings.default_weight_unit = weight_unit
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.settings"))


@main_bp.route("/settings/foam-blocks", methods=["POST"])
def settings_foam_blocks():
    raw = request.form.get("foam_blocks", "").strip()
    try:
        parsed = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return "Invalid foam block data.", 400
    if not isinstance(parsed, list):
        return "Invalid foam block data.", 400
    cleaned: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        length = str(item.get("length", "")).strip()
        if not length:
            continue
        cleaned.append(
            {
                "length": length,
                "width": str(item.get("width", "")).strip(),
                "height": str(item.get("height", "")).strip(),
                "weight": str(item.get("weight", "")).strip(),
                "compression": str(item.get("compression", "")).strip(),
            }
        )
    settings = get_app_settings()
    settings.foam_blocks = json.dumps(cleaned)
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.settings"))


@main_bp.route("/settings/inspection-types/new", methods=["GET", "POST"])
def inspection_type_new():
    inspection_types = InspectionType.query.all()
    type_rows = inspection_type_tree(inspection_types)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        parent_id = request.form.get("parent_id", "").strip()
        if not name:
            return "Inspection type name is required.", 400
        inspection_type = InspectionType(name=name)
        if parent_id.isdigit():
            inspection_type.parent_id = int(parent_id)
        db.session.add(inspection_type)
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.settings"))
    return render_template("inspection_type_form.html", inspection_type=None, inspection_types=type_rows)


@main_bp.route("/settings/inspection-types/<int:type_id>/edit", methods=["GET", "POST"])
def inspection_type_edit(type_id: int):
    inspection_type = InspectionType.query.get_or_404(type_id)
    inspection_types = InspectionType.query.all()
    type_rows = inspection_type_tree(inspection_types, excluded_id=inspection_type.id)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        parent_id = request.form.get("parent_id", "").strip()
        if not name:
            return "Inspection type name is required.", 400
        if parent_id.isdigit() and int(parent_id) == inspection_type.id:
            return "Inspection type cannot parent itself.", 400
        inspection_type.name = name
        inspection_type.parent_id = int(parent_id) if parent_id.isdigit() else None
        db.session.commit()
        ensure_db_backup()
        return redirect(url_for("main.settings"))
    return render_template(
        "inspection_type_form.html",
        inspection_type=inspection_type,
        inspection_types=type_rows,
    )


@main_bp.route("/settings/inspection-types/<int:type_id>/delete", methods=["POST"])
def inspection_type_delete(type_id: int):
    inspection_type = InspectionType.query.get_or_404(type_id)
    if InspectionType.query.filter_by(parent_id=inspection_type.id).count() > 0:
        return "Cannot delete an inspection type with children.", 400
    if CarInspection.query.filter_by(inspection_type_id=inspection_type.id).count() > 0:
        return "Cannot delete an inspection type used in inspections.", 400
    db.session.delete(inspection_type)
    db.session.commit()
    ensure_db_backup()
    return redirect(url_for("main.settings"))


def serialize_car(car: Car) -> dict:
    class_capacity = car.car_class.capacity if car.car_class else None
    class_weight = car.car_class.weight if car.car_class else None
    class_load_limit = car.car_class.load_limit if car.car_class else None
    class_aar_plate = car.car_class.aar_plate if car.car_class else None
    class_internal_length = car.car_class.internal_length if car.car_class else None
    class_internal_width = car.car_class.internal_width if car.car_class else None
    class_internal_height = car.car_class.internal_height if car.car_class else None
    class_external_length = car.car_class.external_length if car.car_class else None
    class_external_width = car.car_class.external_width if car.car_class else None
    class_external_height = car.car_class.external_height if car.car_class else None
    class_cubic_feet = car.car_class.cubic_feet if car.car_class else None
    class_is_locomotive = car.car_class.is_locomotive if car.car_class else None
    class_power_type = car.car_class.power_type if car.car_class else None
    is_locomotive = (
        car.is_locomotive_override if car.is_locomotive_override is not None else class_is_locomotive
    )
    return {
        "id": car.id,
        "car_type": car.car_type_override or (car.car_class.car_type if car.car_class else None),
        "power_type": car.power_type_override or class_power_type,
        "car_number": car.car_number,
        "reporting_mark": car.railroad.reporting_mark if car.railroad else car.reporting_mark_override,
        "railroad": car.railroad.name if car.railroad else None,
        "car_class": car.car_class.code if car.car_class else None,
        "location": car.location.name if car.location else None,
        "brand": car.brand,
        "upc": car.upc,
        "dcc_id": car.dcc_id,
        "dcc_decoder_type": car.dcc_decoder_type,
        "dcc_decoder_connection_type": car.dcc_decoder_connection_type,
        "dcc_decoder_keep_alive": car.dcc_decoder_keep_alive,
        "wheel_arrangement": car.wheel_arrangement_override
        or (car.car_class.wheel_arrangement if car.car_class else None),
        "tender_axles": car.tender_axles_override or (car.car_class.tender_axles if car.car_class else None),
        "traction_drivers": car.traction_drivers,
        "capacity": car.capacity_override or class_capacity,
        "weight": car.weight_override or class_weight,
        "load_limit": car.load_limit_override or class_load_limit,
        "actual_weight": car.actual_weight,
        "actual_length": car.actual_length,
        "load_length": car.load_length,
        "load_width": car.load_width,
        "load_height": car.load_height,
        "scale": car.scale,
        "gauge": car.gauge,
        "aar_plate": car.aar_plate_override or class_aar_plate,
        "internal_length": car.internal_length_override or class_internal_length,
        "internal_width": car.internal_width_override or class_internal_width,
        "internal_height": car.internal_height_override or class_internal_height,
        "external_length": car.external_length_override or class_external_length,
        "external_width": car.external_width_override or class_external_width,
        "external_height": car.external_height_override or class_external_height,
        "cubic_feet": car.cubic_feet_override or class_cubic_feet,
        "built": car.built,
        "alt_date": car.alt_date,
        "reweight_date": car.reweight_date,
        "repack_bearings_date": car.repack_bearings_date,
        "last_inspection_date": car.last_inspection_date,
        "other_lettering": car.other_lettering,
        "msrp": car.msrp,
        "price": car.price,
        "load": car.load,
        "repairs_required": car.repairs_required,
        "notes": car.notes,
        "capacity_override": car.capacity_override,
        "weight_override": car.weight_override,
        "load_limit_override": car.load_limit_override,
        "aar_plate_override": car.aar_plate_override,
        "internal_length_override": car.internal_length_override,
        "internal_width_override": car.internal_width_override,
        "internal_height_override": car.internal_height_override,
        "external_length_override": car.external_length_override,
        "external_width_override": car.external_width_override,
        "external_height_override": car.external_height_override,
        "cubic_feet_override": car.cubic_feet_override,
        "car_type_override": car.car_type_override,
        "power_type_override": car.power_type_override,
        "wheel_arrangement_override": car.wheel_arrangement_override,
        "tender_axles_override": car.tender_axles_override,
        "is_locomotive_override": car.is_locomotive_override,
        "is_locomotive": is_locomotive,
    }


def serialize_part(part: PartItem) -> dict:
    return {
        "id": part.id,
        "name": part.name,
        "description": part.description,
        "brand": part.brand,
        "upc": part.upc,
        "quantity": part.quantity,
        "location": part.location.name if part.location else None,
    }
