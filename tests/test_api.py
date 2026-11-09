from datetime import timedelta


def H(h):
    return {"headers": h}


def make_ticket(client, headers, **kw):
    body = {"title": "Cannot log in to Outlook", "description": "Password rejected", **kw}
    r = client.post("/api/v1/tickets", json=body, headers=headers)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_health_and_unauthenticated(client):
    assert client.get("/api/v1/health").get_json() == {"status": "ok"}
    assert client.get("/api/v1/tickets").status_code == 401
    assert client.get("/api/v1/tickets", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_register_login_me(client):
    r = client.post("/api/v1/auth/register", json={"username": "newbie", "email": "n@example.test",
                                                   "password": "longenough1"})
    assert r.status_code == 201 and r.get_json()["role"] == "requester"
    assert "password_hash" not in r.get_json()
    dup = client.post("/api/v1/auth/register", json={"username": "newbie", "email": "n@example.test",
                                                     "password": "longenough1"})
    assert dup.status_code == 409
    bad = client.post("/api/v1/auth/login", json={"username": "newbie", "password": "nope"})
    assert bad.status_code == 401
    tok = client.post("/api/v1/auth/login", json={"username": "newbie", "password": "longenough1"}).get_json()
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok['token']}"})
    assert me.get_json()["username"] == "newbie"


def test_weak_password_rejected(client):
    r = client.post("/api/v1/auth/register", json={"username": "weak", "email": "w@example.test", "password": "short"})
    assert r.status_code == 422


def test_token_expires(client, clock, auth):
    h = auth("alice")
    assert client.get("/api/v1/me", headers=h).status_code == 200
    clock.advance(seconds=43201)
    assert client.get("/api/v1/me", headers=h).status_code == 401


def test_ticket_crud_and_autoclassification(client, auth):
    alice, agent = auth("alice"), auth("agent1")
    t = make_ticket(client, alice, title="Antivirus detected malware on my laptop",
                    description="URGENT: everything is down.")
    assert t["status"] == "open" and t["category"] == "security"
    assert t["suggestion_source"] in ("model", "mixed", "rules")
    assert client.get(f"/api/v1/tickets/{t['id']}", headers=alice).status_code == 200
    upd = client.patch(f"/api/v1/tickets/{t['id']}", json={"priority": "low", "category": "software"}, headers=agent)
    assert upd.get_json()["priority"] == "low"
    # manual values override but the suggestion is kept
    assert upd.get_json()["suggested_category"] == "security"
    assert client.patch(f"/api/v1/tickets/{t['id']}", json={"status": "closed"}, headers=agent).status_code == 422


def test_requester_isolation_and_role_checks(client, auth):
    alice, bob, agent, boss = auth("alice"), auth("bob"), auth("agent1"), auth("boss")
    t = make_ticket(client, alice)
    assert client.get(f"/api/v1/tickets/{t['id']}", headers=bob).status_code == 404
    assert client.get("/api/v1/tickets", headers=bob).get_json()["count"] == 0
    assert client.get("/api/v1/tickets", headers=agent).get_json()["count"] == 1
    assert client.patch(f"/api/v1/tickets/{t['id']}", json={"priority": "low"}, headers=alice).status_code == 403
    assert client.post(f"/api/v1/tickets/{t['id']}/transition", json={"status": "in_progress"},
                       headers=alice).status_code == 403
    assert client.get("/api/v1/analytics/summary", headers=alice).status_code == 403
    assert client.delete(f"/api/v1/tickets/{t['id']}", headers=agent).status_code == 403
    assert client.delete(f"/api/v1/tickets/{t['id']}", headers=boss).status_code == 204
    assert client.get(f"/api/v1/tickets/{t['id']}", headers=boss).status_code == 404


def test_only_admin_manages_users(client, auth):
    assert client.get("/api/v1/users", headers=auth("agent1")).status_code == 403
    r = client.post("/api/v1/users", json={"username": "agent2", "email": "a2@example.test",
                                           "password": "password-123", "role": "agent"}, headers=auth("boss"))
    assert r.status_code == 201 and r.get_json()["role"] == "agent"
    # self-registration can never mint staff
    r = client.post("/api/v1/auth/register", json={"username": "sneaky", "email": "s@example.test",
                                                   "password": "password-123", "role": "admin"})
    assert r.get_json()["role"] == "requester"


def test_transitions_audit_and_sla_stamps(client, auth, clock):
    alice, agent = auth("alice"), auth("agent1")
    t = make_ticket(client, alice, priority="high")
    tid = t["id"]
    bad = client.post(f"/api/v1/tickets/{tid}/transition", json={"status": "resolved"}, headers=agent)
    assert bad.status_code == 409
    clock.advance(minutes=20)
    r = client.post(f"/api/v1/tickets/{tid}/transition", json={"status": "in_progress"}, headers=agent).get_json()
    assert r["first_response_at"] and r["sla"]["first_response"] == "met" and r["assignee_id"]
    clock.advance(minutes=30)
    for s in ("waiting", "in_progress", "resolved", "closed"):
        assert client.post(f"/api/v1/tickets/{tid}/transition", json={"status": s},
                           headers=agent).status_code == 200
    final = client.get(f"/api/v1/tickets/{tid}", headers=agent).get_json()
    assert final["status"] == "closed" and final["sla"]["overall"] == "met" and final["allowed_transitions"] == []
    audit = client.get(f"/api/v1/tickets/{tid}/audit", headers=agent).get_json()
    assert [a["new_value"] for a in audit if a["action"] == "transition"] == [
        "in_progress", "waiting", "in_progress", "resolved", "closed"]
    assert client.get(f"/api/v1/tickets/{tid}/audit", headers=alice).status_code == 403


def test_sla_breach_detection(client, auth, clock):
    alice, agent = auth("alice"), auth("agent1")
    t = make_ticket(client, alice, priority="critical")  # 15 min first response
    assert client.get("/api/v1/analytics/sla-breaches", headers=agent).get_json() == []
    clock.advance(minutes=45)
    breaches = client.get("/api/v1/analytics/sla-breaches", headers=agent).get_json()
    assert [b["id"] for b in breaches] == [t["id"]] and breaches[0]["stage"] == "first_response"
    assert breaches[0]["overdue_minutes"] == 30.0


def test_comments_internal_visibility_and_first_response(client, auth, clock):
    alice, agent = auth("alice"), auth("agent1")
    tid = make_ticket(client, alice)["id"]
    url = f"/api/v1/tickets/{tid}/comments"
    assert client.post(url, json={"body": "secret", "internal": True}, headers=alice).status_code == 403
    clock.advance(minutes=5)
    assert client.post(url, json={"body": "note to self", "internal": True}, headers=agent).status_code == 201
    assert client.get(f"/api/v1/tickets/{tid}", headers=agent).get_json()["first_response_at"] is None
    assert client.post(url, json={"body": "On it"}, headers=agent).status_code == 201
    assert client.get(f"/api/v1/tickets/{tid}", headers=agent).get_json()["first_response_at"]
    assert len(client.get(url, headers=agent).get_json()) == 2
    assert [c["body"] for c in client.get(url, headers=alice).get_json()] == ["On it"]
    assert client.post(url, json={"body": ""}, headers=alice).status_code == 422


def test_validation_errors(client, auth):
    alice = auth("alice")
    assert client.post("/api/v1/tickets", json={"title": "x"}, headers=alice).status_code == 422
    assert client.post("/api/v1/tickets", json={"title": "x", "description": "y", "priority": "urgent"},
                       headers=alice).status_code == 422
    assert client.post("/api/v1/tickets", data="not json", headers=alice).status_code == 422
    assert client.get("/api/v1/tickets?limit=abc", headers=alice).status_code == 422
    assert client.get("/api/v1/tickets/999", headers=alice).status_code == 404


def test_sql_injection_is_inert(client, auth):
    alice = auth("alice")
    make_ticket(client, alice)
    r = client.get("/api/v1/tickets?status=open' OR '1'='1", headers=auth("agent1"))
    assert r.status_code == 422
    t = make_ticket(client, alice, title="x'); DROP TABLE tickets;--")
    assert client.get(f"/api/v1/tickets/{t['id']}", headers=alice).status_code == 200


def test_triage_endpoint(client, auth):
    r = client.post("/api/v1/triage", json={"text": "Not receiving emails in Outlook. URGENT: everything is down."},
                    headers=auth("alice"))
    d = r.get_json()
    assert r.status_code == 200 and d["category"] == "email" and d["priority"] == "critical"
