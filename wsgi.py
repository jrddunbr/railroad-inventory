from __future__ import annotations

import os
from typing import Callable

from app import create_app


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes"}


class BlockDevPathsMiddleware:
    def __init__(self, app: Callable) -> None:
        self.app = app

    def __call__(self, environ, start_response):
        path = (environ.get("PATH_INFO") or "").strip()
        if path == "/dev" or path.startswith("/dev/"):
            start_response(
                "404 Not Found",
                [
                    ("Content-Type", "text/plain; charset=utf-8"),
                    ("Cache-Control", "no-store"),
                ],
            )
            return [b"Not Found"]
        return self.app(environ, start_response)


flask_app = create_app()
if _as_bool(
    os.environ.get("BLOCK_DEV_PATHS"),
    default=_as_bool(os.environ.get("BLOCK_DEV_LOCAL_LOGIN_PATHS"), default=True),
):
    app = BlockDevPathsMiddleware(flask_app)
else:
    app = flask_app
