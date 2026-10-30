"""Login, logout and registration. Registration only ever creates REGULAR users."""
from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .access import login_required
from .db import UserError, execute, fetch_one
from .security import safe_next_url, throttle
from .validators import (ValidationError, clean_email, clean_text, clean_username,
                         validate_password)

bp = Blueprint("auth", __name__)

# Used so a wrong username takes about as long as a wrong password (no user-guessing by timing).
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def _password_ok(stored_hash, password):
    try:
        return check_password_hash(stored_hash, password)
    except ValueError:               # e.g. the locked '!' hash of sample users
        return False


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("main.dashboard"))
    cfg = current_app.config
    has_admin = fetch_one("SELECT COUNT(*) AS n FROM users WHERE role = 'admin'")["n"] > 0

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        key = (request.remote_addr, username.lower())
        if throttle.blocked(key, cfg["LOGIN_MAX_ATTEMPTS"], cfg["LOGIN_WINDOW_SECONDS"]):
            flash("Too many failed attempts. Please wait a few minutes and try again.", "danger")
            return render_template("login.html", has_admin=has_admin), 429

        user = fetch_one(
            "SELECT user_id, password_hash, is_active FROM users WHERE username = %s", (username,)
        )
        ok = _password_ok(user["password_hash"] if user else _DUMMY_HASH, password)
        if user and ok and user["is_active"]:
            throttle.clear(key)
            session.clear()                      # new session on login (prevents session fixation)
            session["user_id"] = user["user_id"]
            session.permanent = True
            flash("You are logged in.", "success")
            return redirect(safe_next_url(request.args.get("next")) or url_for("main.dashboard"))
        throttle.record_failure(key, cfg["LOGIN_WINDOW_SECONDS"])
        flash("Invalid username or password.", "danger")
        return render_template("login.html", has_admin=has_admin), 401
    return render_template("login.html", has_admin=has_admin)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        try:
            full_name = clean_text(request.form.get("full_name"), "Full name", 100)
            username = clean_username(request.form.get("username"))
            email = clean_email(request.form.get("email"))
            password = validate_password(request.form.get("password"))
            if password != request.form.get("confirm_password"):
                raise ValidationError("The two passwords do not match.")
            # NOTE: the role is never read from the form. Everyone who registers is a regular user.
            execute(
                "INSERT INTO users (full_name, username, email, password_hash, role) VALUES (%s, %s, %s, %s, 'user')",
                (full_name, username, email, generate_password_hash(password)),
            )
        except (ValidationError, UserError) as err:
            flash(str(err), "danger")
            return render_template("register.html", form=request.form), 400
        flash("Account created. You can log in now.", "success")
        return redirect(url_for("auth.login"))
    return render_template("register.html", form={})
