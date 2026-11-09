# API reference

JSON API under `/api/v1`. The HTTP layer is **framework-thin**: Flask blueprints only parse requests and
serialise responses; all rules live in `helpdesk/services.py`. (FastAPI was not available when this was
built, so Flask is used.) All data in the demo database is **synthetic**.

Start a demo server (the demo accounts all use the password `demo-password-123`):

```bash
python -m helpdesk seed --db build/demo.db
python -m helpdesk train                      # optional: without it the rule-based fallback triages tickets
python -m helpdesk serve --db build/demo.db   # http://127.0.0.1:5000
```

```bash
B=http://127.0.0.1:5000/api/v1
TOK=$(curl -s -X POST $B/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"user01","password":"demo-password-123"}' | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
ATOK=$(curl -s -X POST $B/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"agent_sara","password":"demo-password-123"}' | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
```

Authenticated calls send `Authorization: Bearer <token>`. Tokens are HMAC-SHA256 signed, expire after
`token_ttl_seconds` (default 3600) and the user's role is re-read from the database on every request.

## Errors

Every error is `{"error": "<code>", "message": "..."}`.

| HTTP | code | meaning |
|---|---|---|
| 401 | `unauthorized` | missing, malformed, tampered or expired token; bad credentials |
| 403 | `forbidden` | role lacks the permission |
| 404 | `not_found` | no such object (also returned for other users' tickets, to avoid leaking them) |
| 409 | `invalid_transition` / `conflict` | illegal status move; duplicate username; editing a closed ticket |
| 422 | `validation_error` | bad or missing input |

## Endpoints

| Method and path | Who | Purpose |
|---|---|---|
| `GET /health` | anyone | liveness |
| `POST /auth/register` | anyone | self-register (always a `requester`) |
| `POST /auth/login` | anyone | returns `{token, expires_in, user}` |
| `GET /me` | any | current user |
| `GET /users`, `POST /users`, `PATCH /users/<id>/role` | admin | user management |
| `POST /triage` | any | classifier suggestion for a text |
| `POST /tickets` | any | create; category/priority auto-filled if omitted |
| `GET /tickets` | any | list (`status`, `category`, `priority`, `assignee_id`, `requester_id`, `limit`, `offset`); requesters only see their own |
| `GET /tickets/<id>` | owner or staff | one ticket with `sla` and `allowed_transitions` |
| `PATCH /tickets/<id>` | agent, admin | edit title, description, category, priority, assignee_id |
| `DELETE /tickets/<id>` | admin | delete |
| `POST /tickets/<id>/transition` | agent, admin | move through the state machine |
| `GET/POST /tickets/<id>/comments` | owner or staff | comments; `"internal": true` only for staff |
| `GET /tickets/<id>/audit` | agent, admin | audit log |
| `GET /analytics/summary` | agent, admin | KPIs and breakdowns |
| `GET /analytics/sla-breaches` | agent, admin | unresolved tickets already past a deadline |

Status machine: `open -> in_progress -> waiting <-> in_progress -> resolved -> closed`; `resolved -> in_progress`
reopens; `closed` is terminal. Anything else returns 409.

SLA targets (calendar minutes, configurable in `helpdesk/config.py`):

| priority | first response | resolution |
|---|---|---|
| critical | 15 min | 4 h |
| high | 60 min | 8 h |
| medium | 4 h | 24 h |
| low | 8 h | 72 h |

## Examples (real responses from a local run, abbreviated)

Create a ticket as a requester; the classifier fills category and priority and the suggestion is stored:

```bash
curl -s -X POST $B/tickets -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"title":"Cannot connect to the VPN from home","description":"Need this fixed today please."}'
```
```json
{"id":61,"status":"open","category":"network","priority":"high","suggestion_source":"rules",
 "allowed_transitions":["in_progress"],
 "sla":{"first_response":"pending","first_response_due":"2026-11-06T06:12:00+00:00","overall":"pending", "...": "..."}}
```
(`suggestion_source` was `rules` because no trained model file existed in that run.)

Illegal transition:

```bash
curl -s -X POST $B/tickets/61/transition -H "Authorization: Bearer $ATOK" \
  -H 'Content-Type: application/json' -d '{"status":"resolved"}'
# HTTP 409
{"error":"invalid_transition","message":"Cannot move 'open' -> 'resolved'. Allowed: in_progress"}
```

Legal transition, comment, audit trail:

```bash
curl -s -X POST $B/tickets/61/transition -H "Authorization: Bearer $ATOK" -H 'Content-Type: application/json' -d '{"status":"in_progress"}'
curl -s -X POST $B/tickets/61/comments  -H "Authorization: Bearer $ATOK" -H 'Content-Type: application/json' -d '{"body":"Checking VPN logs","internal":true}'
curl -s $B/tickets/61/audit -H "Authorization: Bearer $ATOK"
```

Triage only (no ticket created):

```bash
curl -s -X POST $B/triage -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"text":"Mailbox is full and I cannot send messages"}'
{"category":"email","category_confidence":1.0,"priority":"medium","priority_confidence":0.3,"source":"rules"}
```

Filtering, analytics, and the unauthenticated case:

```bash
curl -s "$B/tickets?status=open&limit=10" -H "Authorization: Bearer $ATOK"
curl -s $B/analytics/summary     -H "Authorization: Bearer $ATOK"
curl -s $B/analytics/sla-breaches -H "Authorization: Bearer $ATOK"
curl -s -o /dev/null -w "%{http_code}\n" $B/tickets        # 401
```

## Admin dashboard (server-rendered)

`/admin/login` (agents and admins; requesters are refused), `/admin/` (KPIs, matplotlib charts, active SLA
breaches), `/admin/queue` (filterable queue), `/admin/tickets/<id>` (comments and audit log). The session is an
`HttpOnly`, `SameSite=Lax` cookie holding the same signed token.
