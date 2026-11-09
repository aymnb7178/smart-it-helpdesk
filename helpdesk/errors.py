"""Domain exceptions. Each carries the HTTP status the API layer should use."""

from __future__ import annotations


class HelpdeskError(Exception):
    status = 400
    code = "error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ValidationError(HelpdeskError):
    status = 422
    code = "validation_error"


class AuthError(HelpdeskError):
    status = 401
    code = "unauthorized"


class PermissionDenied(HelpdeskError):
    status = 403
    code = "forbidden"


class NotFound(HelpdeskError):
    status = 404
    code = "not_found"


class InvalidTransition(HelpdeskError):
    status = 409
    code = "invalid_transition"


class Conflict(HelpdeskError):
    status = 409
    code = "conflict"
