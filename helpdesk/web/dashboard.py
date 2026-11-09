"""Server-rendered admin dashboard (Jinja2) at ``/admin``. Staff only (agent/admin)."""

from __future__ import annotations

from flask import Blueprint, Response, abort, make_response, redirect, render_template, request, url_for

from helpdesk import charts
from helpdesk.config import CATEGORIES, PRIORITIES, STATUSES
from helpdesk.errors import AuthError, HelpdeskError, PermissionDenied
from helpdesk.permissions import require
from helpdesk.web.auth import COOKIE, current_user, svc

dashboard_bp = Blueprint("dashboard", __name__, template_folder="templates")


@dashboard_bp.errorhandler(AuthError)
def _need_login(_err):
    return redirect(url_for("dashboard.login"))


@dashboard_bp.errorhandler(HelpdeskError)
def _html_error(err: HelpdeskError):
    return render_template("error.html", err=err), err.status


def _staff() -> dict:
    user = current_user(allow_cookie=True)
    if user["role"] not in ("admin", "agent"):
        raise PermissionDenied("The dashboard is for agents and admins")
    require(user["role"], "analytics:read")
    return user


@dashboard_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        try:
            result = svc().login(request.form.get("username", ""), request.form.get("password", ""))
            if result["user"]["role"] not in ("admin", "agent"):
                raise PermissionDenied("The dashboard is for agents and admins")
            resp = make_response(redirect(url_for("dashboard.index")))
            resp.set_cookie(COOKIE, result["token"], httponly=True, samesite="Lax",
                            max_age=result["expires_in"])
            return resp
        except HelpdeskError as exc:
            error = exc.message
    return render_template("login.html", error=error), (401 if error else 200)


@dashboard_bp.post("/logout")
def logout():
    resp = make_response(redirect(url_for("dashboard.login")))
    resp.delete_cookie(COOKIE)
    return resp


@dashboard_bp.get("/")
def index():
    user = _staff()
    s = svc()
    summary = s.analytics_summary(user)
    breaches = s.sla_breaches(user)[:10]
    return render_template("dashboard.html", user=user, summary=summary, breaches=breaches,
                           names=s.user_names())


@dashboard_bp.get("/queue")
def queue():
    user = _staff()
    s = svc()
    f = {k: (request.args.get(k) or None) for k in ("status", "category", "priority")}
    items = s.list_tickets(user, limit=100, **f)
    return render_template("queue.html", user=user, items=items, filters=f, names=s.user_names(),
                           statuses=STATUSES, categories=CATEGORIES, priorities=PRIORITIES)


@dashboard_bp.get("/tickets/<int:ticket_id>")
def ticket(ticket_id: int):
    user = _staff()
    s = svc()
    return render_template("ticket.html", user=user, t=s.get_ticket(user, ticket_id),
                           comments=s.list_comments(user, ticket_id), audit=s.audit_trail(user, ticket_id),
                           names=s.user_names())


@dashboard_bp.get("/charts/<name>.png")
def chart(name: str):
    user = _staff()
    if name not in charts.CHARTS:
        abort(404)
    png = charts.render(name, svc().analytics_summary(user))
    return Response(png, mimetype="image/png", headers={"Cache-Control": "no-store"})
