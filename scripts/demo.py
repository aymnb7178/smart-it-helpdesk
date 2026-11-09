"""One-command demo (all data SYNTHETIC): seed DB -> evaluate classifier -> API walkthrough ->
charts in docs/ -> self-contained sample dashboard HTML."""

import base64
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from sklearn.metrics import ConfusionMatrixDisplay  # noqa: E402

from helpdesk import charts  # noqa: E402
from helpdesk.classifier import TicketClassifier, evaluate  # noqa: E402
from helpdesk.config import CATEGORIES, Config  # noqa: E402
from helpdesk.db import Database  # noqa: E402
from helpdesk.seed import DEMO_PASSWORD, seed_database  # noqa: E402
from helpdesk.web import create_app  # noqa: E402

NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)  # fixed so the sample is reproducible
DOCS, BUILD = ROOT / "docs", ROOT / "build"


def main() -> int:
    DOCS.mkdir(exist_ok=True)
    BUILD.mkdir(exist_ok=True)
    db_path = BUILD / "demo.db"
    db_path.unlink(missing_ok=True)
    info = seed_database(Database(str(db_path)), n_tickets=300, days=60, seed=7, now=NOW)
    print(f"Seeded {db_path.relative_to(ROOT)}: users={info['users']}, tickets={info['tickets']} (SYNTHETIC)")

    ev = evaluate(3000, 42)
    labels = ev.pop("_test_labels")
    print("Classifier evaluation (synthetic templated data):")
    print(json.dumps(ev, indent=2))
    fig, ax = plt.subplots(figsize=(6.4, 5), dpi=110)
    ConfusionMatrixDisplay.from_predictions(labels["category"], labels["pred_category"], labels=list(CATEGORIES),
                                            ax=ax, xticks_rotation=40, colorbar=False, cmap="Blues")
    ax.set_title("Category confusion matrix (held-out, SYNTHETIC)", loc="left", fontsize=11)
    fig.tight_layout()
    fig.savefig(DOCS / "classifier_confusion.png")
    plt.close(fig)

    model = TicketClassifier.train(n=3000, seed=42)
    app = create_app(Config(db_path=str(db_path), secret_key="demo-secret-not-for-real-use",
                            model_path=str(BUILD / "none.joblib")), classifier=model, clock=lambda: NOW)
    c = app.test_client()

    def login(user):
        r = c.post("/api/v1/auth/login", json={"username": user, "password": DEMO_PASSWORD}).get_json()
        return {"Authorization": f"Bearer {r['token']}"}

    req, agent = login("user01"), login("agent_sara")
    t = c.post("/api/v1/tickets", headers=req, json={
        "title": "Antivirus detected malware on my laptop",
        "description": "URGENT: everything is down. I already restarted it."}).get_json()
    print(f"\nAPI walkthrough: ticket #{t['id']} auto-triaged -> category={t['category']}, priority={t['priority']}, "
          f"source={t['suggestion_source']}")
    for status in ("in_progress", "resolved", "closed"):
        t = c.post(f"/api/v1/tickets/{t['id']}/transition", headers=agent, json={"status": status}).get_json()
    print(f"  moved through state machine to '{t['status']}', SLA overall = {t['sla']['overall']}")
    bad = c.post(f"/api/v1/tickets/{t['id']}/transition", headers=agent, json={"status": "open"})
    print(f"  closed -> open rejected with HTTP {bad.status_code}: {bad.get_json()['message']}")

    summary = c.get("/api/v1/analytics/summary", headers=agent).get_json()
    print("\nAnalytics summary (SYNTHETIC demo data):")
    for k in ("total_tickets", "active_tickets", "mean_first_response_minutes", "mean_resolution_minutes",
              "sla_compliance_pct", "active_breaches"):
        print(f"  {k}: {summary[k]}")
    for p in summary["by_priority"]:
        print(f"  SLA compliance [{p['priority']:<8}] {p['sla_compliance_pct']}% over {p['tickets']} tickets")

    for name in charts.CHARTS:
        (DOCS / f"{name}.png").write_bytes(charts.render(name, summary))

    # Sample dashboard: render the real admin page, inline the chart images, drop the nav form.
    c.post("/admin/login", data={"username": "agent_sara", "password": DEMO_PASSWORD})
    html = c.get("/admin/").get_data(as_text=True)

    def inline(m):
        png = c.get(m.group(1)).data
        return 'src="data:image/png;base64,' + base64.b64encode(png).decode() + '"'

    html = re.sub(r'src="(/admin/charts/[a-z_]+\.png)"', inline, html)
    html = html.replace("<header>", "<header>\n  <em>Static snapshot of SYNTHETIC demo data</em>", 1)
    html = re.sub(r"<form method=\"post\" action=\"/admin/logout\">.*?</form>", "", html, flags=re.S)
    (DOCS / "sample_dashboard.html").write_text(html, encoding="utf-8")
    print(f"\nWrote {len(charts.CHARTS) + 1} PNGs and docs/sample_dashboard.html ({len(html) // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
