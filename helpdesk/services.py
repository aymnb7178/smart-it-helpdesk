"""Application service layer: business rules on top of the sqlite3 repositories.

Every public method takes the *actor* (a user row as a dict) explicitly, enforces
role permissions, validates input, writes an audit entry for each change and keeps
the HTTP framework out of the picture (so the rules are unit-testable on their own).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Callable

from helpdesk import analytics, sla
from helpdesk.classifier import Suggestion, TicketClassifier
from helpdesk.config import CATEGORIES, PRIORITIES, ROLES, STATUSES, Config
from helpdesk.db import AuditRepository, CommentRepository, Database, TicketRepository, UserRepository
from helpdesk.errors import AuthError, Conflict, NotFound, PermissionDenied, ValidationError
from helpdesk.permissions import can, require
from helpdesk.security import hash_password, issue_token, verify_password, verify_token
from helpdesk.timeutil import to_iso, utcnow
from helpdesk.workflow import allowed_transitions, validate_transition

log = logging.getLogger(__name__)

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DUMMY_HASH = hash_password("not-a-real-password")  # equalises timing for unknown usernames
STAFF_ROLES = ("admin", "agent")


class HelpdeskService:
    """Facade used by the Flask blueprints, the CLI and the seed script."""

    def __init__(self, db: Database, config: Config, classifier: TicketClassifier | None = None,
                 clock: Callable[[], datetime] = utcnow):
        self.db = db
        self.config = config
        self.classifier = classifier or TicketClassifier(None, None, config.min_model_confidence)
        self.clock = clock

    # ------------------------------------------------------------------ users
    def create_user(self, username: str, email: str, password: str, role: str = "requester",
                    actor: dict | None = None) -> dict:
        """Create a user. Self-registration (``actor is None``) may only create requesters."""
        if actor is None:
            if role != "requester":
                raise PermissionDenied("Self-registration only creates requester accounts")
        else:
            require(actor["role"], "user:manage")
        if role not in ROLES:
            raise ValidationError(f"role must be one of {list(ROLES)}")
        if not isinstance(username, str) or not _USERNAME_RE.match(username):
            raise ValidationError("username must be 3-32 chars: letters, digits, _ . -")
        if not isinstance(email, str) or not _EMAIL_RE.match(email):
            raise ValidationError("email is not valid")
        if not isinstance(password, str) or len(password) < self.config.password_min_length:
            raise ValidationError(f"password must be at least {self.config.password_min_length} characters")
        pw_hash = hash_password(password)
        with self.db.session() as conn:
            users = UserRepository(conn)
            if users.get_by_username(username):
                raise Conflict(f"username '{username}' already exists")
            uid = users.create(username, email, role, pw_hash, to_iso(self.clock()))
            AuditRepository(conn).log(None, actor["id"] if actor else uid, "user_created",
                                      to_iso(self.clock()), "role", None, role)
            return self._public_user(users.get(uid))

    @staticmethod
    def _public_user(user: dict | None) -> dict:
        if user is None:
            raise NotFound("User not found")
        return {k: user[k] for k in ("id", "username", "email", "role", "active", "created_at")}

    def login(self, username: str, password: str) -> dict:
        """Verify credentials and return ``{"token", "expires_in", "user"}``."""
        with self.db.session() as conn:
            user = UserRepository(conn).get_by_username(username) if isinstance(username, str) else None
        stored = user["password_hash"] if user else _DUMMY_HASH
        ok = verify_password(password if isinstance(password, str) else "", stored)
        if not user or not ok or not user["active"]:
            raise AuthError("Invalid username or password")
        token = issue_token(self.config.secret_key, user["id"], user["role"],
                            self.config.token_ttl_seconds, now=self.clock().timestamp())
        return {"token": token, "expires_in": self.config.token_ttl_seconds, "user": self._public_user(user)}

    def authenticate(self, token: str) -> dict:
        """Validate a bearer token and return the *current* user row (role re-read from the DB)."""
        claims = verify_token(self.config.secret_key, token, now=self.clock().timestamp())
        with self.db.session() as conn:
            user = UserRepository(conn).get(int(claims["sub"]))
        if not user or not user["active"]:
            raise AuthError("User no longer active")
        return user

    def list_users(self, actor: dict) -> list[dict]:
        require(actor["role"], "user:manage")
        with self.db.session() as conn:
            return [self._public_user(u) for u in UserRepository(conn).list()]

    def set_user_role(self, actor: dict, user_id: int, role: str) -> dict:
        require(actor["role"], "user:manage")
        if role not in ROLES:
            raise ValidationError(f"role must be one of {list(ROLES)}")
        with self.db.session() as conn:
            users = UserRepository(conn)
            target = users.get(user_id)
            if not target:
                raise NotFound("User not found")
            if target["id"] == actor["id"] and role != "admin":
                raise Conflict("Admins cannot demote themselves")
            users.set_role(user_id, role)
            AuditRepository(conn).log(None, actor["id"], "user_role_changed", to_iso(self.clock()),
                                      "role", target["role"], role)
            return self._public_user(users.get(user_id))

    # ---------------------------------------------------------------- tickets
    def triage(self, text: str) -> Suggestion:
        if not isinstance(text, str) or not text.strip():
            raise ValidationError("text is required")
        return self.classifier.predict(text[:5000])

    def _present(self, ticket: dict, now: datetime) -> dict:
        out = dict(ticket)
        out["sla"] = sla.evaluate(ticket, now, self.config.sla).to_dict()
        out["allowed_transitions"] = sorted(allowed_transitions(ticket["status"]))
        return out

    def _visible_ticket(self, conn, actor: dict, ticket_id: int) -> dict:
        ticket = TicketRepository(conn).get(ticket_id)
        if not ticket:
            raise NotFound("Ticket not found")
        if not can(actor["role"], "ticket:read_all"):
            require(actor["role"], "ticket:read_own")
            if ticket["requester_id"] != actor["id"]:
                raise NotFound("Ticket not found")  # do not reveal other users' tickets
        return ticket

    def create_ticket(self, actor: dict, title: Any, description: Any, category: Any = None,
                      priority: Any = None, requester_id: Any = None) -> dict:
        """Create a ticket. Missing category/priority are filled by the classifier.

        The classifier's suggestion is always stored (``suggested_*``) next to the final value.
        """
        require(actor["role"], "ticket:create")
        title, description = self._text(title, "title", 200), self._text(description, "description", 5000)
        if category is not None and category not in CATEGORIES:
            raise ValidationError(f"category must be one of {list(CATEGORIES)}")
        if priority is not None and priority not in PRIORITIES:
            raise ValidationError(f"priority must be one of {list(PRIORITIES)}")
        suggestion = self.classifier.predict(f"{title}. {description}")
        owner = actor["id"]
        if requester_id is not None:
            if actor["role"] not in STAFF_ROLES:
                raise PermissionDenied("Only staff can file tickets on behalf of others")
            owner = requester_id
        now = self.clock()
        with self.db.session() as conn:
            if UserRepository(conn).get(owner) is None:
                raise ValidationError("requester_id does not exist")
            tickets = TicketRepository(conn)
            tid = tickets.create(
                title=title, description=description,
                category=category or suggestion.category, priority=priority or suggestion.priority,
                status="open", requester_id=owner, suggested_category=suggestion.category,
                suggested_priority=suggestion.priority, suggestion_source=suggestion.source,
                created_at=to_iso(now), updated_at=to_iso(now),
            )
            AuditRepository(conn).log(tid, actor["id"], "created", to_iso(now), "status", None, "open")
            return self._present(tickets.get(tid), now)

    @staticmethod
    def _text(value: Any, name: str, limit: int) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{name} is required")
        value = value.strip()
        if len(value) > limit:
            raise ValidationError(f"{name} must be at most {limit} characters")
        return value

    def get_ticket(self, actor: dict, ticket_id: int) -> dict:
        with self.db.session() as conn:
            return self._present(self._visible_ticket(conn, actor, ticket_id), self.clock())

    def list_tickets(self, actor: dict, limit: int = 50, offset: int = 0, **filters: Any) -> list[dict]:
        limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
        for key, allowed in (("status", None), ("category", CATEGORIES), ("priority", PRIORITIES)):
            val = filters.get(key)
            if val is not None and allowed is not None and val not in allowed:
                raise ValidationError(f"{key} must be one of {list(allowed)}")
        if filters.get("status") is not None and filters["status"] not in STATUSES:
            raise ValidationError("unknown status")
        if not can(actor["role"], "ticket:read_all"):
            require(actor["role"], "ticket:read_own")
            filters["requester_id"] = actor["id"]
        now = self.clock()
        with self.db.session() as conn:
            rows = TicketRepository(conn).list(limit=limit, offset=offset, **filters)
        return [self._present(r, now) for r in rows]

    def update_ticket(self, actor: dict, ticket_id: int, **changes: Any) -> dict:
        """Staff-only edit of title, description, category, priority, assignee_id (status uses transition)."""
        require(actor["role"], "ticket:update")
        allowed = {"title", "description", "category", "priority", "assignee_id"}
        if "status" in changes:
            raise ValidationError("Use the transition endpoint to change status")
        unknown = set(changes) - allowed
        if unknown:
            raise ValidationError(f"Cannot update fields: {sorted(unknown)}")
        if not changes:
            raise ValidationError("No changes supplied")
        if "title" in changes:
            changes["title"] = self._text(changes["title"], "title", 200)
        if "description" in changes:
            changes["description"] = self._text(changes["description"], "description", 5000)
        if "category" in changes and changes["category"] not in CATEGORIES:
            raise ValidationError(f"category must be one of {list(CATEGORIES)}")
        if "priority" in changes and changes["priority"] not in PRIORITIES:
            raise ValidationError(f"priority must be one of {list(PRIORITIES)}")
        now = to_iso(self.clock())
        with self.db.session() as conn:
            tickets, audit = TicketRepository(conn), AuditRepository(conn)
            ticket = tickets.get(ticket_id)
            if not ticket:
                raise NotFound("Ticket not found")
            if ticket["status"] == "closed":
                raise Conflict("Closed tickets cannot be edited")
            if changes.get("assignee_id") is not None:
                assignee = UserRepository(conn).get(changes["assignee_id"])
                if not assignee or not assignee["active"] or assignee["role"] not in STAFF_ROLES:
                    raise ValidationError("assignee must be an active agent or admin")
            actual = {k: v for k, v in changes.items() if ticket[k] != v}
            if actual:
                tickets.update(ticket_id, updated_at=now, **actual)
                for field, new in actual.items():
                    audit.log(ticket_id, actor["id"], "updated", now, field, ticket[field], new)
            return self._present(tickets.get(ticket_id), self.clock())

    def transition(self, actor: dict, ticket_id: int, new_status: str) -> dict:
        """Move a ticket through the state machine, stamping first-response / resolved / closed times."""
        require(actor["role"], "ticket:transition")
        now_dt = self.clock()
        now = to_iso(now_dt)
        with self.db.session() as conn:
            tickets, audit = TicketRepository(conn), AuditRepository(conn)
            ticket = tickets.get(ticket_id)
            if not ticket:
                raise NotFound("Ticket not found")
            validate_transition(ticket["status"], new_status)
            changes: dict[str, Any] = {"status": new_status, "updated_at": now}
            if ticket["status"] == "open" and not ticket["first_response_at"]:
                changes["first_response_at"] = now
            if new_status == "resolved":
                changes["resolved_at"] = now
            if new_status == "closed":
                changes["closed_at"] = now
            if ticket["status"] == "resolved" and new_status == "in_progress":
                changes["resolved_at"] = None  # reopened: the resolution clock is evaluated again
            if ticket["assignee_id"] is None and actor["role"] in STAFF_ROLES and new_status == "in_progress":
                changes["assignee_id"] = actor["id"]
            tickets.update(ticket_id, **changes)
            audit.log(ticket_id, actor["id"], "transition", now, "status", ticket["status"], new_status)
            return self._present(tickets.get(ticket_id), now_dt)

    def delete_ticket(self, actor: dict, ticket_id: int) -> None:
        require(actor["role"], "ticket:delete")
        with self.db.session() as conn:
            if not TicketRepository(conn).get(ticket_id):
                raise NotFound("Ticket not found")
            AuditRepository(conn).log(ticket_id, actor["id"], "deleted", to_iso(self.clock()))
            TicketRepository(conn).delete(ticket_id)

    # --------------------------------------------------------------- comments
    def add_comment(self, actor: dict, ticket_id: int, body: Any, internal: bool = False) -> dict:
        require(actor["role"], "comment:create")
        body = self._text(body, "body", 5000)
        if internal:
            require(actor["role"], "comment:internal")
        now_dt = self.clock()
        now = to_iso(now_dt)
        with self.db.session() as conn:
            ticket = self._visible_ticket(conn, actor, ticket_id)
            if ticket["status"] == "closed":
                raise Conflict("Closed tickets cannot be commented on")
            comments = CommentRepository(conn)
            cid = comments.create(ticket_id, actor["id"], body, bool(internal), now)
            audit = AuditRepository(conn)
            audit.log(ticket_id, actor["id"], "commented", now, "internal", None, int(bool(internal)))
            if actor["role"] in STAFF_ROLES and not internal and not ticket["first_response_at"]:
                TicketRepository(conn).update(ticket_id, first_response_at=now, updated_at=now)
                audit.log(ticket_id, actor["id"], "first_response", now)
            return next(c for c in comments.list(ticket_id, True) if c["id"] == cid)

    def list_comments(self, actor: dict, ticket_id: int) -> list[dict]:
        with self.db.session() as conn:
            self._visible_ticket(conn, actor, ticket_id)
            return CommentRepository(conn).list(ticket_id, can(actor["role"], "comment:internal"))

    def audit_trail(self, actor: dict, ticket_id: int) -> list[dict]:
        require(actor["role"], "ticket:read_all")
        with self.db.session() as conn:
            if not TicketRepository(conn).get(ticket_id):
                raise NotFound("Ticket not found")
            return AuditRepository(conn).for_ticket(ticket_id)

    # -------------------------------------------------------------- analytics
    def _all_tickets(self) -> list[dict]:
        with self.db.session() as conn:
            return TicketRepository(conn).all()

    def analytics_summary(self, actor: dict) -> dict:
        require(actor["role"], "analytics:read")
        return analytics.summary(self._all_tickets(), self.clock(), self.config.sla)

    def sla_breaches(self, actor: dict) -> list[dict]:
        require(actor["role"], "analytics:read")
        return analytics.active_breaches(self._all_tickets(), self.clock(), self.config.sla)

    def user_names(self) -> dict[int, str]:
        with self.db.session() as conn:
            return {u["id"]: u["username"] for u in UserRepository(conn).list()}
