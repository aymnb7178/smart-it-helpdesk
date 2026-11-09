import random

from helpdesk.analytics import summary
from helpdesk.classifier import TicketClassifier, rule_based_suggest
from helpdesk.config import Config
from helpdesk.db import Database
from helpdesk.seed import DEMO_PASSWORD, seed_database
from helpdesk.synthetic import generate_ticket, generate_tickets
from helpdesk.timeutil import utcnow
from helpdesk.web import create_app


def test_synthetic_generator_is_deterministic_and_labelled():
    a, b = generate_tickets(50, seed=3), generate_tickets(50, seed=3)
    assert a == b and a != generate_tickets(50, seed=4)
    assert {"title", "description", "category", "priority"} <= set(generate_ticket(random.Random(0)))


def test_rule_fallback_when_model_missing(tmp_path):
    clf = TicketClassifier.load_or_rules(tmp_path / "nope.joblib")
    assert not clf.is_trained
    s = clf.predict("Antivirus found a virus on my laptop. URGENT outage")
    assert (s.category, s.priority, s.source) == ("security", "critical", "rules")
    assert rule_based_suggest("zzz qqq").category == "software"


def test_model_beats_chance_on_heldout_synthetic(trained):
    test = generate_tickets(300, seed=999)
    ok = sum(trained.predict(f"{r['title']}. {r['description']}").category == r["category"] for r in test)
    assert ok / len(test) > 0.85  # synthetic templated data: easy by construction


def test_model_save_load_roundtrip(trained, tmp_path):
    p = tmp_path / "m.joblib"
    trained.save(p)
    loaded = TicketClassifier.load(p)
    txt = "The wifi in the warehouse keeps disconnecting"
    assert loaded.predict(txt) == trained.predict(txt)


def test_low_confidence_falls_back_to_rules(trained):
    strict = TicketClassifier(trained.category_model, trained.priority_model, min_confidence=1.01)
    assert strict.predict("The wifi keeps disconnecting").source == "rules"


def test_seed_and_analytics(tmp_path):
    db = Database(str(tmp_path / "s.db"))
    now = utcnow()
    info = seed_database(db, n_tickets=120, days=30, seed=5, now=now)
    assert info["tickets"] == 120
    from helpdesk.db import TicketRepository
    with db.session() as conn:
        rows = TicketRepository(conn).all()
    s = summary(rows, now)
    assert s["total_tickets"] == 120 and sum(s["by_status"].values()) == 120
    assert sum(c["tickets"] for c in s["by_category"]) == 120
    assert 0 <= s["sla_compliance_pct"] <= 100 and s["mean_first_response_minutes"] > 0


def test_dashboard_requires_staff_and_renders(tmp_path, trained):
    db_path = str(tmp_path / "d.db")
    seed_database(Database(db_path), n_tickets=80, days=20, seed=2)
    app = create_app(Config(db_path=db_path, secret_key="k", model_path="none"), classifier=trained)
    c = app.test_client()
    r = c.get("/admin/")
    assert r.status_code == 302 and "/admin/login" in r.headers["Location"]
    assert c.post("/admin/login", data={"username": "user01", "password": DEMO_PASSWORD}).status_code == 401
    assert c.post("/admin/login", data={"username": "agent_sara", "password": DEMO_PASSWORD}).status_code == 302
    page = c.get("/admin/")
    assert page.status_code == 200 and b"SLA compliance" in page.data
    assert b"<script" not in page.data
    assert c.get("/admin/queue?status=open").status_code == 200
    png = c.get("/admin/charts/volume.png")
    assert png.status_code == 200 and png.data[:8] == b"\x89PNG\r\n\x1a\n"
    assert c.get("/admin/charts/nope.png").status_code == 404
    assert c.get("/admin/tickets/1").status_code == 200
