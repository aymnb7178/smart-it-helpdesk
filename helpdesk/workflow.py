"""Ticket status state machine: open -> in_progress -> waiting -> resolved -> closed."""

from __future__ import annotations

from helpdesk.config import STATUSES
from helpdesk.errors import InvalidTransition

TRANSITIONS: dict[str, frozenset[str]] = {
    "open": frozenset({"in_progress"}),
    "in_progress": frozenset({"waiting", "resolved"}),
    "waiting": frozenset({"in_progress", "resolved"}),
    "resolved": frozenset({"closed", "in_progress"}),  # in_progress == reopen
    "closed": frozenset(),
}


def allowed_transitions(status: str) -> frozenset[str]:
    return TRANSITIONS.get(status, frozenset())


def validate_transition(current: str, new: str) -> None:
    """Raise :class:`InvalidTransition` unless ``current -> new`` is permitted."""
    if new not in STATUSES:
        raise InvalidTransition(f"Unknown status '{new}'")
    if new not in allowed_transitions(current):
        allowed = ", ".join(sorted(allowed_transitions(current))) or "none (terminal state)"
        raise InvalidTransition(f"Cannot move '{current}' -> '{new}'. Allowed: {allowed}")
