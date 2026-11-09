"""Command line interface: ``python -m helpdesk <command>``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from helpdesk import __version__
from helpdesk.classifier import TicketClassifier, evaluate
from helpdesk.config import Config
from helpdesk.db import Database
from helpdesk.seed import DEMO_PASSWORD, seed_database

log = logging.getLogger("helpdesk")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="helpdesk", description="Smart IT Helpdesk (synthetic demo data only)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("seed", help="create a database filled with SYNTHETIC demo data")
    s.add_argument("--db", default="build/demo.db")
    s.add_argument("--tickets", type=int, default=300)
    s.add_argument("--days", type=int, default=60)
    s.add_argument("--seed", type=int, default=7)

    t = sub.add_parser("train", help="train the TF-IDF + LogisticRegression triage model on synthetic data")
    t.add_argument("--out", default="models/classifier.joblib")
    t.add_argument("--n", type=int, default=3000)
    t.add_argument("--seed", type=int, default=42)

    e = sub.add_parser("evaluate", help="print held-out accuracy on synthetic data as JSON")
    e.add_argument("--n", type=int, default=3000)
    e.add_argument("--seed", type=int, default=42)

    tr = sub.add_parser("triage", help="suggest category and priority for a text")
    tr.add_argument("text")
    tr.add_argument("--model", default="models/classifier.joblib")

    sv = sub.add_parser("serve", help="run the Flask development server")
    sv.add_argument("--db", default="build/demo.db")
    sv.add_argument("--model", default="models/classifier.joblib")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=5000)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.command == "seed":
        Path(args.db).parent.mkdir(parents=True, exist_ok=True)
        if Path(args.db).exists():
            log.error("%s already exists; delete it first", args.db)
            return 2
        info = seed_database(Database(args.db), args.tickets, args.days, args.seed)
        print(json.dumps({**info, "db": args.db}, indent=2))
    elif args.command == "train":
        model = TicketClassifier.train(n=args.n, seed=args.seed)
        model.save(args.out)
        print(f"Saved model trained on {args.n} synthetic tickets to {args.out}")
    elif args.command == "evaluate":
        result = evaluate(args.n, args.seed)
        result.pop("_test_labels", None)
        print(json.dumps(result, indent=2))
    elif args.command == "triage":
        model = TicketClassifier.load_or_rules(args.model)
        print(json.dumps(model.predict(args.text).to_dict(), indent=2))
    elif args.command == "serve":
        from helpdesk.web import create_app

        if not Path(args.db).exists():
            log.error("%s not found; run `python -m helpdesk seed --db %s` first", args.db, args.db)
            return 2
        app = create_app(Config(db_path=args.db, model_path=args.model))
        print(f"Demo login: admin / {DEMO_PASSWORD}  (synthetic accounts, dev server only)")
        app.run(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
