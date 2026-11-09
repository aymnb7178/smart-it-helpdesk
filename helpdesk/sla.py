"""SLA target calculation and breach detection (calendar minutes, no pause on 'waiting')."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from helpdesk.config import DEFAULT_SLA, SlaTarget
from helpdesk.timeutil import from_iso, to_iso

MET, BREACHED, PENDING = "met", "breached", "pending"


@dataclass(frozen=True)
class SlaStatus:
    first_response_due: str
    resolution_due: str
    first_response: str  # met | breached | pending
    resolution: str
    overall: str

    def to_dict(self) -> dict:
        return asdict(self)


def _state(done_at: datetime | None, due: datetime, now: datetime) -> str:
    if done_at is not None:
        return MET if done_at <= due else BREACHED
    return BREACHED if now > due else PENDING


def evaluate(ticket: dict, now: datetime, targets: dict[str, SlaTarget] | None = None) -> SlaStatus:
    """Evaluate a ticket row (dict) against the SLA for its priority.

    A stage is *met* if completed by its deadline, *breached* if completed late or
    still incomplete past the deadline, and *pending* if incomplete but within time.
    """
    targets = targets or DEFAULT_SLA
    target = targets[ticket["priority"]]
    created = from_iso(ticket["created_at"])
    fr_due = created + timedelta(minutes=target.first_response_minutes)
    res_due = created + timedelta(minutes=target.resolution_minutes)
    fr_at = from_iso(ticket["first_response_at"]) if ticket.get("first_response_at") else None
    res_at = from_iso(ticket["resolved_at"]) if ticket.get("resolved_at") else None
    fr, res = _state(fr_at, fr_due, now), _state(res_at, res_due, now)
    if BREACHED in (fr, res):
        overall = BREACHED
    elif PENDING in (fr, res):
        overall = PENDING
    else:
        overall = MET
    return SlaStatus(to_iso(fr_due), to_iso(res_due), fr, res, overall)
