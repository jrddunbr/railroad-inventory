from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
from cryptography.fernet import Fernet, InvalidToken
from datetime import UTC, datetime
from urllib.parse import urlsplit

from flask import current_app
from flask import abort, request, session
from werkzeug.security import check_password_hash, generate_password_hash

ACCESS_LEVELS = {
    "READ": 1,
    "WRITE": 2,
}
DEFAULT_PERMISSION_TEXT = "inventory/* READ"
MIN_PASSWORD_LENGTH = 12
TOTP_PERIOD_SECONDS = 30
TOTP_DIGITS = 6


def utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def normalize_username(value: str | None) -> str:
    return (value or "").strip().lower()


def normalize_scope(value: str | None) -> str:
    raw = (value or "").strip().strip("/")
    return raw or "*"


def normalize_access(value: str | None) -> str:
    raw = (value or "").strip().upper()
    if raw not in ACCESS_LEVELS:
        raise ValueError(f"Unsupported access level: {value!r}")
    return raw


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password_hash: str | None, candidate: str) -> bool:
    if not password_hash:
        return False
    return check_password_hash(password_hash, candidate)


def validate_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.")


def bytes_to_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def base64url_to_bytes(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def generate_secret_key() -> str:
    return secrets.token_urlsafe(48)


def encrypt_value(value: str) -> str:
    return _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_value(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _get_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Stored encrypted value could not be decrypted.") from exc


def build_totp_uri(secret: str, username: str, issuer: str) -> str:
    label = f"{issuer}:{username}"
    return (
        "otpauth://totp/"
        f"{label}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD_SECONDS}"
    )


def get_valid_totp_counter(secret: str, code: str, now: int | None = None, window: int = 1) -> int | None:
    cleaned = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(cleaned) != TOTP_DIGITS:
        return None
    timestamp = int(now or datetime.now(UTC).timestamp())
    counter = timestamp // TOTP_PERIOD_SECONDS
    for offset in range(-window, window + 1):
        candidate_counter = counter + offset
        if secrets.compare_digest(_totp_at(secret, candidate_counter), cleaned):
            return candidate_counter
    return None


def verify_totp_code(secret: str, code: str, now: int | None = None, window: int = 1) -> bool:
    return get_valid_totp_counter(secret, code, now=now, window=window) is not None


def parse_permissions(raw: str | None) -> list[dict[str, str]]:
    text = (raw or "").strip()
    if not text:
        raise ValueError("At least one permission is required.")
    if text.startswith("["):
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("Permission JSON must be a list.")
        permissions: list[dict[str, str]] = []
        for item in parsed:
            if not isinstance(item, dict):
                raise ValueError("Each permission must be an object.")
            permissions.append(
                {
                    "scope": normalize_scope(str(item.get("scope", ""))),
                    "access": normalize_access(str(item.get("access", ""))),
                }
            )
        return permissions

    permissions = []
    for line in text.splitlines():
        cleaned = line.strip().rstrip(",")
        if not cleaned:
            continue
        parts = cleaned.split()
        if len(parts) != 2:
            raise ValueError(f"Invalid permission line: {line!r}")
        scope, access = parts
        permissions.append({"scope": normalize_scope(scope), "access": normalize_access(access)})
    if not permissions:
        raise ValueError("At least one permission is required.")
    return permissions


def format_permissions(permissions: list[dict[str, str]] | None) -> str:
    return "\n".join(
        f"{normalize_scope(item.get('scope'))} {normalize_access(item.get('access'))}"
        for item in (permissions or [])
        if item.get("scope") and item.get("access")
    )


def permission_allows(
    permissions: list[dict[str, str]] | None,
    required_scope: str,
    required_access: str,
) -> bool:
    required_scope = normalize_scope(required_scope)
    required_access = normalize_access(required_access)
    required_level = ACCESS_LEVELS[required_access]
    for item in permissions or []:
        try:
            granted_scope = normalize_scope(item.get("scope"))
            granted_access = normalize_access(item.get("access"))
        except ValueError:
            continue
        if ACCESS_LEVELS[granted_access] < required_level:
            continue
        if scope_matches(granted_scope, required_scope):
            return True
    return False


def scope_matches(granted_scope: str, required_scope: str) -> bool:
    granted_scope = normalize_scope(granted_scope)
    required_scope = normalize_scope(required_scope)
    if granted_scope == "*":
        return True
    granted_parts = granted_scope.split("/")
    required_parts = required_scope.split("/")
    for index, granted_part in enumerate(granted_parts):
        if granted_part == "*":
            return True
        if index >= len(required_parts):
            return False
        if granted_part != required_parts[index]:
            return False
    return len(granted_parts) == len(required_parts)


def is_safe_next_url(target: str | None) -> bool:
    if not target:
        return False
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return False
    return target.startswith("/")


def get_csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def validate_csrf() -> None:
    expected = session.get("_csrf_token")
    received = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not expected or not received or not secrets.compare_digest(expected, received):
        abort(400, description="CSRF validation failed.")


def _get_fernet() -> Fernet:
    secret_key = str(current_app.config.get("SECRET_KEY", "dev-secret-key")).encode("utf-8")
    derived = hashlib.sha256(secret_key).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def _totp_at(secret: str, counter: int) -> str:
    normalized = secret.upper()
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    key = base64.b32decode(f"{normalized}{padding}", casefold=True)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**TOTP_DIGITS)).zfill(TOTP_DIGITS)
