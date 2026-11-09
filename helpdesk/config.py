"""Configuration constants and the :class:`Config` dataclass."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

PRIORITIES: tuple[str, ...] = ("low", "medium", "high", "critical")
CATEGORIES: tuple[str, ...] = (
    "hardware",
    "software",
    "network",
    "account_access",
    "email",
    "security",
)
STATUSES: tuple[str, ...] = ("open", "in_progress", "waiting", "resolved", "closed")
ROLES: tuple[str, ...] = ("admin", "agent", "requester")

DEV_SECRET = "dev-only-change-me"


@dataclass(frozen=True)
class SlaTarget:
    """SLA targets in minutes (calendar time, not business hours)."""

    first_response_minutes: int
    resolution_minutes: int


DEFAULT_SLA: dict[str, SlaTarget] = {
    "critical": SlaTarget(first_response_minutes=15, resolution_minutes=4 * 60),
    "high": SlaTarget(first_response_minutes=60, resolution_minutes=8 * 60),
    "medium": SlaTarget(first_response_minutes=4 * 60, resolution_minutes=24 * 60),
    "low": SlaTarget(first_response_minutes=8 * 60, resolution_minutes=72 * 60),
}


@dataclass(frozen=True)
class Config:
    """Runtime configuration. Override via ``create_app(Config(...))``."""

    db_path: str = "helpdesk.db"
    secret_key: str = field(default_factory=lambda: os.environ.get("HELPDESK_SECRET_KEY", DEV_SECRET))
    token_ttl_seconds: int = 3600
    model_path: str = "models/classifier.joblib"
    min_model_confidence: float = 0.45
    password_min_length: int = 8
    sla: dict[str, SlaTarget] = field(default_factory=lambda: dict(DEFAULT_SLA))
