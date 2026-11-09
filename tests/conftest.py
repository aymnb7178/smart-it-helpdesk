from datetime import datetime, timedelta, timezone

import pytest

from helpdesk.classifier import TicketClassifier
from helpdesk.config import Config
from helpdesk.web import create_app

T0 = datetime(2026, 1, 15, 9, 0, 0, tzinfo=timezone.utc)


class Clock:
    """Mutable fake clock so tests control SLA time."""

    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@pytest.fixture(scope="session")
def trained():
    return TicketClassifier.train(n=1200, seed=1)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(tmp_path, clock, trained):
    cfg = Config(db_path=str(tmp_path / "t.db"), secret_key="test-secret", token_ttl_seconds=43200,
                 model_path=str(tmp_path / "missing.joblib"))
    return create_app(cfg, classifier=trained, clock=clock)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def svc(app):
    return app.extensions["helpdesk"]


def _make(svc, username, role):
    admin = {"id": 0, "role": "admin"}
    if role == "requester":
        svc.create_user(username, f"{username}@example.test", "password-123")
    else:
        svc.create_user(username, f"{username}@example.test", "password-123", role, actor=admin)


@pytest.fixture
def users(svc):
    for name, role in (("alice", "requester"), ("bob", "requester"), ("agent1", "agent"), ("boss", "admin")):
        _make(svc, name, role)
    return None


@pytest.fixture
def auth(client, users):
    """Return a function name -> Authorization header dict."""
    def _auth(name):
        r = client.post("/api/v1/auth/login", json={"username": name, "password": "password-123"})
        assert r.status_code == 200, r.get_json()
        return {"Authorization": f"Bearer {r.get_json()['token']}"}
    return _auth
