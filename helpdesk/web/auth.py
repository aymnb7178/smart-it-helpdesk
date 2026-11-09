"""Request authentication helpers shared by the API and dashboard blueprints."""

from __future__ import annotations

from flask import current_app, request

from helpdesk.errors import AuthError, ValidationError
from helpdesk.services import HelpdeskService

COOKIE = "hd_token"


def svc() -> HelpdeskService:
    return current_app.extensions["helpdesk"]


def bearer_token() -> str | None:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def current_user(allow_cookie: bool = False) -> dict:
    token = bearer_token() or (request.cookies.get(COOKIE) if allow_cookie else None)
    if not token:
        raise AuthError("Missing bearer token")
    return svc().authenticate(token)


def json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")
    return data


def int_arg(name: str, default: int) -> int:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValidationError(f"{name} must be an integer") from None
