"""Ticket analytics computed with pandas from plain ticket dicts.

Definitions (all in calendar minutes, matching :mod:`helpdesk.sla`):

* first response time = ``first_response_at - created_at``
* resolution time = ``resolved_at - created_at``
* SLA compliance % = tickets whose overall SLA is *met* / tickets whose overall SLA is
  decided (*met* or *breached*); tickets still *pending* within their deadlines are excluded.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from helpdesk import sla
from helpdesk.config import CATEGORIES, PRIORITIES, STATUSES, SlaTarget
from helpdesk.timeutil import from_iso

ACTIVE = ("open", "in_progress", "waiting")


def _round(x: Any, nd: int = 1) -> float | None:
    return None if x is None or pd.isna(x) else round(float(x), nd)


def build_frame(tickets: list[dict], now: datetime, targets: dict[str, SlaTarget] | None = None) -> pd.DataFrame:
    """One row per ticket with durations (minutes) and SLA stage states."""
    rows = []
    for t in tickets:
        st = sla.evaluate(t, now, targets)
        created = from_iso(t["created_at"])
        fr = from_iso(t["first_response_at"]) if t.get("first_response_at") else None
        res = from_iso(t["resolved_at"]) if t.get("resolved_at") else None
        rows.append({
            "id": t["id"], "category": t["category"], "priority": t["priority"], "status": t["status"],
            "created": created,
            "first_response_min": (fr - created).total_seconds() / 60 if fr else None,
            "resolution_min": (res - created).total_seconds() / 60 if res else None,
            "sla_first_response": st.first_response, "sla_resolution": st.resolution, "sla_overall": st.overall,
        })
    cols = ["id", "category", "priority", "status", "created", "first_response_min", "resolution_min",
            "sla_first_response", "sla_resolution", "sla_overall"]
    df = pd.DataFrame(rows, columns=cols)
    df["first_response_min"] = pd.to_numeric(df["first_response_min"])
    df["resolution_min"] = pd.to_numeric(df["resolution_min"])
    return df


def compliance_pct(overall: pd.Series) -> float | None:
    decided = overall[overall != sla.PENDING]
    if decided.empty:
        return None
    return round(100.0 * float((decided == sla.MET).sum()) / len(decided), 1)


def summary(tickets: list[dict], now: datetime, targets: dict[str, SlaTarget] | None = None) -> dict:
    """Headline KPIs plus breakdowns by status, priority, category and ISO week."""
    df = build_frame(tickets, now, targets)
    out: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "total_tickets": int(len(df)),
        "active_tickets": int(df["status"].isin(ACTIVE).sum()),
        "mean_first_response_minutes": _round(df["first_response_min"].mean()),
        "mean_resolution_minutes": _round(df["resolution_min"].mean()),
        "sla_compliance_pct": compliance_pct(df["sla_overall"]),
        "active_breaches": int(((df["sla_overall"] == sla.BREACHED) & df["status"].isin(ACTIVE)).sum()),
    }
    out["by_status"] = {s: int((df["status"] == s).sum()) for s in STATUSES}
    out["by_priority"] = []
    for p in PRIORITIES:
        sub = df[df["priority"] == p]
        out["by_priority"].append({
            "priority": p, "tickets": int(len(sub)),
            "sla_compliance_pct": compliance_pct(sub["sla_overall"]),
            "mean_first_response_minutes": _round(sub["first_response_min"].mean()),
            "mean_resolution_minutes": _round(sub["resolution_min"].mean()),
        })
    out["by_category"] = []
    for c in CATEGORIES:
        sub = df[df["category"] == c]
        out["by_category"].append({
            "category": c, "tickets": int(len(sub)),
            "mean_first_response_minutes": _round(sub["first_response_min"].mean()),
            "mean_resolution_minutes": _round(sub["resolution_min"].mean()),
            "sla_compliance_pct": compliance_pct(sub["sla_overall"]),
        })
    out["volume_by_week"] = volume_by_week(df)
    return out


def volume_by_week(df: pd.DataFrame) -> list[dict]:
    """Tickets created per week (week starting Monday, UTC)."""
    if df.empty:
        return []
    weeks = df["created"].dt.tz_convert("UTC").dt.tz_localize(None).dt.to_period("W-SUN").dt.start_time
    counts = weeks.value_counts().sort_index()
    return [{"week": d.date().isoformat(), "tickets": int(n)} for d, n in counts.items()]


def active_breaches(tickets: list[dict], now: datetime, targets: dict[str, SlaTarget] | None = None) -> list[dict]:
    """Unresolved tickets that have already missed a deadline, most overdue first."""
    out = []
    for t in tickets:
        if t["status"] not in ACTIVE:
            continue
        st = sla.evaluate(t, now, targets)
        if st.overall != sla.BREACHED:
            continue
        overdue = 0.0
        for state, due in ((st.first_response, st.first_response_due), (st.resolution, st.resolution_due)):
            if state == sla.BREACHED:
                overdue = max(overdue, (now - from_iso(due)).total_seconds() / 60)
        stage = "first_response" if st.first_response == sla.BREACHED else "resolution"
        out.append({"id": t["id"], "title": t["title"], "priority": t["priority"], "status": t["status"],
                    "category": t["category"], "assignee_id": t["assignee_id"], "stage": stage,
                    "overdue_minutes": round(overdue, 1)})
    return sorted(out, key=lambda r: r["overdue_minutes"], reverse=True)
