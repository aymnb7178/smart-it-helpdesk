"""Role-based permission table. Object-level checks (own tickets) live in the service."""

from __future__ import annotations

from helpdesk.errors import PermissionDenied

PERMISSIONS: dict[str, frozenset[str]] = {
    "requester": frozenset({"ticket:create", "ticket:read_own", "comment:create"}),
    "agent": frozenset(
        {
            "ticket:create",
            "ticket:read_all",
            "ticket:update",
            "ticket:transition",
            "comment:create",
            "comment:internal",
            "analytics:read",
        }
    ),
    "admin": frozenset(
        {
            "ticket:create",
            "ticket:read_all",
            "ticket:update",
            "ticket:transition",
            "ticket:delete",
            "comment:create",
            "comment:internal",
            "analytics:read",
            "user:manage",
        }
    ),
}


def can(role: str, permission: str) -> bool:
    return permission in PERMISSIONS.get(role, frozenset())


def require(role: str, permission: str) -> None:
    if not can(role, permission):
        raise PermissionDenied(f"Role '{role}' lacks permission '{permission}'")
