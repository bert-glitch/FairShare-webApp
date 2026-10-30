"""FairShare: Group Expense Splitting and Settlement Management System (Flask application factory)."""
import logging
import os
from decimal import Decimal
from dotenv import load_dotenv

load_dotenv()

from flask import Flask, g, render_template, request

from config import Config
from . import access, admin, auth, db, expenses, groups, main, security, settlements


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def peso(value):
    """Format money as 1,234.50 with a peso sign (negative numbers get a leading minus)."""
    if value is None:
        return "-"
    value = Decimal(value)
    sign = "-" if value < 0 else ""
    return f"{sign}\u20b1{abs(value):,.2f}"


def create_app(overrides=None):
    app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"),
                static_folder=os.path.join(ROOT, "static"))
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY is not set. Copy .env.example to .env and set a long random SECRET_KEY.")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # ---- request hooks (order matters: CSRF check, then load the user)
    @app.before_request
    def _guard():
        if request.endpoint == "static":
            return None
        security.check_csrf()
        access.load_user()

    app.after_request(security.add_security_headers)
    app.teardown_appcontext(db.close_db)

    # ---- template helpers
    app.jinja_env.filters["peso"] = peso
    app.jinja_env.filters["date"] = lambda d: d.strftime("%b %d, %Y") if d else "-"
    app.jinja_env.filters["datetime"] = lambda d: d.strftime("%b %d, %Y %I:%M %p") if d else "-"

    app.jinja_env.globals["csrf_token"] = security.csrf_token      # visible inside imported macros too

    @app.context_processor
    def _inject():
        return {"current_user": getattr(g, "user", None)}

    # ---- pages
    for module in (auth, main, groups, expenses, settlements, admin):
        app.register_blueprint(module.bp)

    # ---- friendly error pages (never show technical details)
    @app.errorhandler(db.DatabaseUnavailable)
    def _db_down(err):
        return render_template("error.html", code=503, title="Database unavailable", message=str(err)), 503

    @app.errorhandler(db.UserError)
    def _user_error(err):
        return render_template("error.html", code=400, title="Could not complete that", message=str(err)), 400

    @app.errorhandler(400)
    def _bad_request(err):
        return render_template("error.html", code=400, title="Bad request",
                               message=getattr(err, "description", "That request was not valid.")), 400

    @app.errorhandler(403)
    def _forbidden(err):
        return render_template("error.html", code=403, title="Not allowed",
                               message="You do not have permission to do that."), 403

    @app.errorhandler(404)
    def _not_found(err):
        return render_template("error.html", code=404, title="Not found",
                               message="That page or record does not exist."), 404

    @app.errorhandler(413)
    def _too_big(err):
        return render_template("error.html", code=413, title="Too large", message="That request is too large."), 413

    @app.errorhandler(500)
    def _server_error(err):
        app.logger.exception("Unhandled error")
        return render_template("error.html", code=500, title="Something went wrong",
                               message="An unexpected error happened. Nothing was saved. Please try again."), 500

    return app
