"""Flask application factory.

The HTTP layer is intentionally framework-thin: blueprints only parse requests and
serialise responses; all rules live in :class:`helpdesk.services.HelpdeskService`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from flask import Flask, jsonify

from helpdesk.classifier import TicketClassifier
from helpdesk.config import DEV_SECRET, Config
from helpdesk.db import Database
from helpdesk.errors import HelpdeskError
from helpdesk.services import HelpdeskService
from helpdesk.timeutil import utcnow

log = logging.getLogger(__name__)


def create_app(config: Config | None = None, classifier: TicketClassifier | None = None,
               clock: Callable[[], datetime] = utcnow) -> Flask:
    """Build the app: init DB, load the classifier (rules fallback) and register blueprints."""
    from helpdesk.web.api import api_bp
    from helpdesk.web.dashboard import dashboard_bp

    config = config or Config()
    if config.secret_key == DEV_SECRET:
        log.warning("Using the built-in development secret; set HELPDESK_SECRET_KEY for anything real")
    db = Database(config.db_path)
    db.init()
    classifier = classifier or TicketClassifier.load_or_rules(config.model_path, config.min_model_confidence)
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    app.extensions["helpdesk"] = HelpdeskService(db, config, classifier, clock)
    app.register_blueprint(api_bp, url_prefix="/api/v1")
    app.register_blueprint(dashboard_bp, url_prefix="/admin")

    @app.errorhandler(HelpdeskError)
    def _domain_error(err: HelpdeskError):
        return jsonify({"error": err.code, "message": err.message}), err.status

    @app.errorhandler(404)
    def _not_found(_):
        return jsonify({"error": "not_found", "message": "No such route"}), 404

    @app.errorhandler(405)
    def _bad_method(_):
        return jsonify({"error": "method_not_allowed", "message": "Method not allowed"}), 405

    @app.get("/")
    def _index():
        return jsonify({"name": "smart-it-helpdesk", "api": "/api/v1", "dashboard": "/admin/"})

    return app


def service_of(app: Flask) -> HelpdeskService:
    return app.extensions["helpdesk"]
