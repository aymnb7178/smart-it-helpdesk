"""Ticket triage: TF-IDF + LogisticRegression with a rule-based fallback.

Two independent models predict ``category`` and ``priority``. When a model is missing
or its confidence is below ``min_confidence`` the keyword rules decide instead.
Training data in this repo is SYNTHETIC and templated, see :mod:`helpdesk.synthetic`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from helpdesk.synthetic import generate_tickets, iter_texts

log = logging.getLogger(__name__)

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "security": ("phishing", "malware", "virus", "ransom", "suspicious", "antivirus", "breach", "stolen", "wipe", "unknown login"),
    "email": ("email", "emails", "mailbox", "outlook", "calendar", "inbox", "spam", "signature", "out of office"),
    "account_access": ("password", "locked", "login", "log in", "permission", "access", "account", "mfa", "multi-factor", "credentials"),
    "network": ("wifi", "wi-fi", "internet", "vpn", "network", "ethernet", "dns", "shared drive", "packet", "connection"),
    "hardware": ("screen", "battery", "keyboard", "mouse", "monitor", "printer", "laptop", "desktop", "overheat", "noise", "turn on"),
    "software": ("install", "crash", "crashes", "update", "upgrade", "license", "freeze", "freezes", "launch", "error"),
}
PRIORITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "critical": ("urgent", "outage", "down", "emergency", "production", "everyone", "nobody", "whole team", "ransom"),
    "high": ("today", "blocking", "deadline", "important", "cannot do my job", "high priority", "client"),
    "low": ("no rush", "whenever", "minor", "nice to have", "low priority", "not urgent"),
}


@dataclass(frozen=True)
class Suggestion:
    category: str
    priority: str
    category_confidence: float
    priority_confidence: float
    source: str  # "model" | "rules" | "mixed"

    def to_dict(self) -> dict:
        return asdict(self)


def _tokens(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9\- ]+", " ", text.lower()) + " "


def rule_category(text: str) -> tuple[str, float]:
    """Keyword vote. Returns (category, confidence); defaults to 'software' with 0.0 if nothing matches."""
    t = _tokens(text)
    scores = {c: sum(1 for k in kws if f" {k} " in t or k in t) for c, kws in CATEGORY_KEYWORDS.items()}
    best, hits = max(scores.items(), key=lambda kv: kv[1])
    if hits == 0:
        return "software", 0.0
    return best, round(hits / sum(scores.values()), 3)


def rule_priority(text: str) -> tuple[str, float]:
    t = _tokens(text)
    for level in ("critical", "high", "low"):
        if any(k in t for k in PRIORITY_KEYWORDS[level]):
            return level, 0.6
    return "medium", 0.3


def rule_based_suggest(text: str) -> Suggestion:
    cat, cc = rule_category(text)
    pri, pc = rule_priority(text)
    return Suggestion(cat, pri, cc, pc, "rules")


def _pipeline() -> Pipeline:
    return Pipeline([
        ("tfidf", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, lowercase=True)),
        ("clf", LogisticRegression(max_iter=2000, C=5.0)),
    ])


class TicketClassifier:
    """Predicts category and priority; degrades gracefully to rules."""

    def __init__(self, category_model: Pipeline | None = None, priority_model: Pipeline | None = None,
                 min_confidence: float = 0.45):
        self.category_model = category_model
        self.priority_model = priority_model
        self.min_confidence = min_confidence

    @property
    def is_trained(self) -> bool:
        return self.category_model is not None and self.priority_model is not None

    @classmethod
    def train(cls, records: list[dict] | None = None, n: int = 3000, seed: int = 42,
              min_confidence: float = 0.45) -> "TicketClassifier":
        records = records if records is not None else generate_tickets(n, seed)
        texts = list(iter_texts(records))
        cat = _pipeline().fit(texts, [r["category"] for r in records])
        pri = _pipeline().fit(texts, [r["priority"] for r in records])
        log.info("Trained classifier on %d synthetic tickets", len(records))
        return cls(cat, pri, min_confidence)

    @staticmethod
    def _top(model: Pipeline, text: str) -> tuple[str, float]:
        proba = model.predict_proba([text])[0]
        i = int(proba.argmax())
        return str(model.classes_[i]), float(proba[i])

    def predict(self, text: str) -> Suggestion:
        rules = rule_based_suggest(text)
        if not self.is_trained:
            return rules
        cat, cc = self._top(self.category_model, text)
        pri, pc = self._top(self.priority_model, text)
        used_rules = 0
        if cc < self.min_confidence:
            cat, cc, used_rules = rules.category, rules.category_confidence, used_rules + 1
        if pc < self.min_confidence:
            pri, pc, used_rules = rules.priority, rules.priority_confidence, used_rules + 1
        source = "model" if used_rules == 0 else ("rules" if used_rules == 2 else "mixed")
        return Suggestion(cat, pri, round(cc, 3), round(pc, 3), source)

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"category": self.category_model, "priority": self.priority_model,
                     "min_confidence": self.min_confidence}, path)

    @classmethod
    def load(cls, path: str | Path) -> "TicketClassifier":
        """Load a model saved by :meth:`save`. Only load files you created (joblib uses pickle)."""
        blob = joblib.load(path)
        return cls(blob["category"], blob["priority"], blob.get("min_confidence", 0.45))

    @classmethod
    def load_or_rules(cls, path: str | Path, min_confidence: float = 0.45) -> "TicketClassifier":
        try:
            return cls.load(path)
        except Exception as exc:  # missing file, corrupt pickle, version mismatch
            log.warning("No usable model at %s (%s); using rule-based fallback", path, exc)
            return cls(None, None, min_confidence)


def evaluate(n: int = 3000, seed: int = 42, holdout: float = 0.2) -> dict:
    """Held-out evaluation on SYNTHETIC data.

    * ``random_split``: stratified-free random 80/20 split (templates are shared, so optimistic).
    * ``template_holdout``: whole templates are withheld from training (tests category generalisation).
    * ``rules``: the keyword fallback on the random-split test set.
    """
    records = generate_tickets(n, seed)
    train, test = train_test_split(records, test_size=holdout, random_state=seed)
    model = TicketClassifier.train(train)
    texts = list(iter_texts(test))
    pred_c = [model.category_model.predict([t])[0] for t in texts]
    pred_p = [model.priority_model.predict([t])[0] for t in texts]
    rules = [rule_based_suggest(t) for t in texts]

    templates = sorted({r["template_id"] for r in records})
    held = set(templates[:: max(1, int(1 / holdout))])
    tr2 = [r for r in records if r["template_id"] not in held]
    te2 = [r for r in records if r["template_id"] in held]
    m2 = TicketClassifier.train(tr2)
    t2 = list(iter_texts(te2))
    pc2 = [m2.category_model.predict([t])[0] for t in t2]
    return {
        "n_total": n, "n_train": len(train), "n_test": len(test),
        "random_split": {
            "category_accuracy": round(accuracy_score([r["category"] for r in test], pred_c), 4),
            "priority_accuracy": round(accuracy_score([r["priority"] for r in test], pred_p), 4),
        },
        "template_holdout": {
            "n_train": len(tr2), "n_test": len(te2), "n_templates_held_out": len(held),
            "category_accuracy": round(accuracy_score([r["category"] for r in te2], pc2), 4),
        },
        "rules": {
            "category_accuracy": round(accuracy_score([r["category"] for r in test], [s.category for s in rules]), 4),
            "priority_accuracy": round(accuracy_score([r["priority"] for r in test], [s.priority for s in rules]), 4),
        },
        "_test_labels": {"category": [r["category"] for r in test], "pred_category": pred_c},
    }
