"""Admin authentication dependency."""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request


def require_admin(request: Request) -> None:
    settings = request.app.state.settings
    expected = settings.require_admin_token()
    supplied = request.headers.get("x-admin-token") or ""
    # Development intentionally permits local UI without a token. Production
    # always fails closed through WeatherSettings.require_admin_token().
    if expected is not None and not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="admin token이 필요합니다.")
