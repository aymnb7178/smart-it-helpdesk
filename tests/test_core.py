import pytest

from helpdesk import sla, workflow
from helpdesk.config import SlaTarget
from helpdesk.errors import AuthError, InvalidTransition
from helpdesk.permissions import can
from helpdesk.security import hash_password, issue_token, verify_password, verify_token
from helpdesk.timeutil import to_iso
from tests.conftest import T0


def test_password_hash_is_salted_and_verifies():
    a, b = hash_password("correct horse"), hash_password("correct horse")
    assert a != b and a.startswith("scrypt$")
    assert verify_password("correct horse", a)
    assert not verify_password("wrong", a)
    assert not verify_password("x", "garbage")


def test_token_roundtrip_tamper_and_expiry():
    tok = issue_token("s", 5, "agent", 60, now=1000)
    assert verify_token("s", tok, now=1030)["sub"] == 5
    with pytest.raises(AuthError):
        verify_token("other-secret", tok, now=1030)
    with pytest.raises(AuthError, match="expired"):
        verify_token("s", tok, now=1060)
    body, sig = tok.split(".")
    with pytest.raises(AuthError):
        verify_token("s", body + "x." + sig, now=1030)
    with pytest.raises(AuthError):
        verify_token("s", "not-a-token", now=1030)


def test_state_machine_valid_and_invalid():
    workflow.validate_transition("open", "in_progress")
    workflow.validate_transition("resolved", "closed")
    for cur, new in [("open", "resolved"), ("closed", "open"), ("waiting", "closed"), ("open", "bogus")]:
        with pytest.raises(InvalidTransition):
            workflow.validate_transition(cur, new)


def test_permission_matrix():
    assert can("admin", "ticket:delete") and not can("agent", "ticket:delete")
    assert not can("requester", "analytics:read") and not can("nobody", "ticket:create")


def _ticket(**kw):
    base = {"priority": "high", "created_at": to_iso(T0), "first_response_at": None, "resolved_at": None}
    return {**base, **kw}


def test_sla_pending_met_and_breached():
    from datetime import timedelta
    t = SlaTarget(60, 480)
    targets = {"high": t}
    now = T0 + timedelta(minutes=30)
    assert sla.evaluate(_ticket(), now, targets).overall == "pending"
    late = sla.evaluate(_ticket(), T0 + timedelta(minutes=61), targets)
    assert late.first_response == "breached" and late.overall == "breached"
    ok = _ticket(first_response_at=to_iso(T0 + timedelta(minutes=10)),
                 resolved_at=to_iso(T0 + timedelta(minutes=100)))
    assert sla.evaluate(ok, T0 + timedelta(days=3), targets).overall == "met"
    answered_late = _ticket(first_response_at=to_iso(T0 + timedelta(minutes=90)),
                            resolved_at=to_iso(T0 + timedelta(minutes=100)))
    assert sla.evaluate(answered_late, T0 + timedelta(days=3), targets).first_response == "breached"
