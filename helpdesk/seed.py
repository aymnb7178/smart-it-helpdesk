"""Seed a database with SYNTHETIC users, tickets, comments and audit history.

Ticket text comes from :mod:`helpdesk.synthetic`; lifecycle times are simulated from the SLA
targets with a configurable chance of missing each deadline. None of it is real data and the
resulting analytics only demonstrate the code paths, not real-world performance.
"""

from __future__ import annotations

import random
from datetime import timedelta

from helpdesk.config import Config, DEFAULT_SLA
from helpdesk.db import AuditRepository, CommentRepository, Database, TicketRepository, UserRepository
from helpdesk.security import hash_password
from helpdesk.synthetic import generate_tickets
from helpdesk.timeutil import to_iso, utcnow

DEMO_PASSWORD = "demo-password-123"  # synthetic demo accounts only; never use in a real deployment
STAFF = [("admin", "admin"), ("agent_sara", "agent"), ("agent_omar", "agent"), ("agent_lina", "agent")]
BREACH_PROB = {"critical": 0.30, "high": 0.22, "medium": 0.15, "low": 0.08}


def seed_database(db: Database, n_tickets: int = 300, days: int = 60, seed: int = 7,
                  now=None) -> dict:
    """Create demo users and ``n_tickets`` tickets spread over the last ``days`` days."""
    rng = random.Random(seed)
    now = now or utcnow()
    db.init()
    pw = hash_password(DEMO_PASSWORD)  # one hash reused: demo accounts share one password
    with db.session() as conn:
        users = UserRepository(conn)
        staff_ids = [users.create(f"{u}", f"{u}@example.test", role, pw, to_iso(now - timedelta(days=days + 1)))
                     for u, role in STAFF]
        requester_ids = [users.create(f"user{i:02d}", f"user{i:02d}@example.test", "requester", pw,
                                      to_iso(now - timedelta(days=days + 1))) for i in range(1, 13)]
        agents = staff_ids[1:]
        tickets, comments, audit = TicketRepository(conn), CommentRepository(conn), AuditRepository(conn)
        for rec in generate_tickets(n_tickets, seed):
            created = now - timedelta(minutes=rng.uniform(5, days * 24 * 60))
            target = DEFAULT_SLA[rec["priority"]]
            p = BREACH_PROB[rec["priority"]]
            fr_min = target.first_response_minutes * (rng.uniform(1.1, 3.0) if rng.random() < p else rng.uniform(0.1, 0.9))
            res_min = target.resolution_minutes * (rng.uniform(1.1, 2.5) if rng.random() < p else rng.uniform(0.2, 0.95))
            res_min = max(res_min, fr_min + 5)
            fr_at, res_at = created + timedelta(minutes=fr_min), created + timedelta(minutes=res_min)
            assignee = rng.choice(agents)
            requester = rng.choice(requester_ids)
            # Unanswered tickets exist so that live breaches show up on the dashboard.
            if fr_at > now:
                status, first, resolved, closed = "open", None, None, None
            elif res_at > now:
                status = rng.choice(["in_progress", "in_progress", "waiting"])
                first, resolved, closed = fr_at, None, None
            else:
                first, resolved = fr_at, res_at
                if now - res_at > timedelta(days=2) or rng.random() < 0.5:
                    status, closed = "closed", res_at + timedelta(hours=rng.uniform(1, 40))
                    if closed > now:
                        status, closed = "resolved", None
                else:
                    status, closed = "resolved", None
            tid = tickets.create(
                title=rec["title"], description=rec["description"], category=rec["category"],
                priority=rec["priority"], status=status, requester_id=requester,
                assignee_id=None if status == "open" else assignee,
                suggested_category=rec["category"], suggested_priority=rec["priority"],
                suggestion_source="seed", created_at=to_iso(created),
                updated_at=to_iso(closed or resolved or first or created),
            )
            tickets.update(tid, first_response_at=to_iso(first) if first else None,
                           resolved_at=to_iso(resolved) if resolved else None,
                           closed_at=to_iso(closed) if closed else None)
            audit.log(tid, requester, "created", to_iso(created), "status", None, "open")
            if first:
                comments.create(tid, assignee, "Looking into this now (synthetic comment).", False, to_iso(first))
                audit.log(tid, assignee, "transition", to_iso(first), "status", "open", "in_progress")
            if resolved:
                audit.log(tid, assignee, "transition", to_iso(resolved), "status", "in_progress", "resolved")
            if closed:
                audit.log(tid, assignee, "transition", to_iso(closed), "status", "resolved", "closed")
    return {"users": len(STAFF) + len(requester_ids), "tickets": n_tickets, "demo_password": DEMO_PASSWORD}
