"""JSON API blueprint mounted at ``/api/v1``. See docs/API.md for curl examples."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from helpdesk.web.auth import current_user, int_arg, json_body, svc

api_bp = Blueprint("api", __name__)


@api_bp.get("/health")
def health():
    return jsonify({"status": "ok"})


# ----------------------------------------------------------------- auth/users
@api_bp.post("/auth/register")
def register():
    d = json_body()
    user = svc().create_user(d.get("username"), d.get("email"), d.get("password"), "requester")
    return jsonify(user), 201


@api_bp.post("/auth/login")
def login():
    d = json_body()
    return jsonify(svc().login(d.get("username"), d.get("password")))


@api_bp.get("/me")
def me():
    u = current_user()
    return jsonify({k: u[k] for k in ("id", "username", "email", "role")})


@api_bp.get("/users")
def users_list():
    return jsonify(svc().list_users(current_user()))


@api_bp.post("/users")
def users_create():
    actor, d = current_user(), json_body()
    user = svc().create_user(d.get("username"), d.get("email"), d.get("password"),
                             d.get("role", "requester"), actor=actor)
    return jsonify(user), 201


@api_bp.patch("/users/<int:user_id>/role")
def users_role(user_id: int):
    actor, d = current_user(), json_body()
    return jsonify(svc().set_user_role(actor, user_id, d.get("role")))


# -------------------------------------------------------------------- triage
@api_bp.post("/triage")
def triage():
    current_user()
    return jsonify(svc().triage(json_body().get("text")).to_dict())


# ------------------------------------------------------------------- tickets
@api_bp.post("/tickets")
def tickets_create():
    actor, d = current_user(), json_body()
    t = svc().create_ticket(actor, d.get("title"), d.get("description"), d.get("category"),
                            d.get("priority"), d.get("requester_id"))
    return jsonify(t), 201


@api_bp.get("/tickets")
def tickets_list():
    actor = current_user()
    filters = {k: request.args.get(k) for k in ("status", "category", "priority")}
    for k in ("assignee_id", "requester_id"):
        if request.args.get(k) is not None:
            filters[k] = int_arg(k, 0)
    items = svc().list_tickets(actor, limit=int_arg("limit", 50), offset=int_arg("offset", 0), **filters)
    return jsonify({"count": len(items), "items": items})


@api_bp.get("/tickets/<int:ticket_id>")
def tickets_get(ticket_id: int):
    return jsonify(svc().get_ticket(current_user(), ticket_id))


@api_bp.patch("/tickets/<int:ticket_id>")
def tickets_update(ticket_id: int):
    actor, d = current_user(), json_body()
    return jsonify(svc().update_ticket(actor, ticket_id, **d))


@api_bp.delete("/tickets/<int:ticket_id>")
def tickets_delete(ticket_id: int):
    svc().delete_ticket(current_user(), ticket_id)
    return "", 204


@api_bp.post("/tickets/<int:ticket_id>/transition")
def tickets_transition(ticket_id: int):
    actor, d = current_user(), json_body()
    return jsonify(svc().transition(actor, ticket_id, d.get("status")))


@api_bp.post("/tickets/<int:ticket_id>/comments")
def comments_create(ticket_id: int):
    actor, d = current_user(), json_body()
    internal = d.get("internal", False)
    if not isinstance(internal, bool):
        from helpdesk.errors import ValidationError
        raise ValidationError("internal must be a boolean")
    return jsonify(svc().add_comment(actor, ticket_id, d.get("body"), internal)), 201


@api_bp.get("/tickets/<int:ticket_id>/comments")
def comments_list(ticket_id: int):
    return jsonify(svc().list_comments(current_user(), ticket_id))


@api_bp.get("/tickets/<int:ticket_id>/audit")
def tickets_audit(ticket_id: int):
    return jsonify(svc().audit_trail(current_user(), ticket_id))


# ----------------------------------------------------------------- analytics
@api_bp.get("/analytics/summary")
def analytics_summary():
    return jsonify(svc().analytics_summary(current_user()))


@api_bp.get("/analytics/sla-breaches")
def analytics_breaches():
    return jsonify(svc().sla_breaches(current_user()))
