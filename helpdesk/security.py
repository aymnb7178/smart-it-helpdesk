"""Password hashing (scrypt + salt) and HMAC-signed expiring tokens (stdlib only)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from helpdesk.errors import AuthError

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    """Return ``scrypt$n$r$p$salt_hex$hash_hex`` using a random 16-byte salt."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification; returns False for malformed hashes."""
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=32
        )
        return hmac.compare_digest(digest, bytes.fromhex(hash_hex))
    except (ValueError, TypeError):
        return False


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, body: str) -> str:
    return _b64e(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())


def issue_token(secret: str, user_id: int, role: str, ttl_seconds: int, now: float | None = None) -> str:
    """Create ``<payload>.<signature>`` with ``sub``, ``role``, ``iat`` and ``exp`` claims."""
    issued = int(now if now is not None else time.time())
    payload = {"sub": user_id, "role": role, "iat": issued, "exp": issued + ttl_seconds}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_sign(secret, body)}"


def verify_token(secret: str, token: str, now: float | None = None) -> dict:
    """Validate signature and expiry; raise :class:`AuthError` otherwise."""
    try:
        body, signature = token.split(".")
    except (ValueError, AttributeError):
        raise AuthError("Malformed token") from None
    if not hmac.compare_digest(_sign(secret, body), signature):
        raise AuthError("Invalid token signature")
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        raise AuthError("Malformed token payload") from None
    current = now if now is not None else time.time()
    if not isinstance(payload.get("exp"), (int, float)) or payload["exp"] <= current:
        raise AuthError("Token expired")
    return payload
