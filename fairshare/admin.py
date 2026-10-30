"""Administrator pages: system summary and user management. Admin-only (checked on the server)."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from .access import admin_required
from .db import UserError, call_proc_rows, execute, fetch_all, fetch_one
from .validators import (ValidationError, clean_email, clean_text, clean_username, parse_int,
                         validate_password)

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("")
@admin_required
def dashboard():
    counts = fetch_one(
        """SELECT (SELECT COUNT(*) FROM users)                                   AS users_total,
                  (SELECT COUNT(*) FROM users WHERE is_active = 1)               AS users_active,
                  (SELECT COUNT(*) FROM expense_groups WHERE status = 'active')   AS groups_active,
                  (SELECT COUNT(*) FROM expense_groups WHERE status = 'archived') AS groups_archived,
                  (SELECT COUNT(*) FROM expenses WHERE status = 'active')         AS expenses_active,
                  (SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE status = 'active') AS expenses_total,
                  (SELECT COUNT(*) FROM settlements WHERE status = 'pending')     AS st_pending,
                  (SELECT COUNT(*) FROM settlements WHERE status = 'overdue')     AS st_overdue,
                  (SELECT COUNT(*) FROM settlements WHERE status = 'completed')   AS st_completed,
                  (SELECT COUNT(*) FROM settlements WHERE status = 'cancelled')   AS st_cancelled"""
    )
    # integrity self-checks: every one of these should be 0
    checks = fetch_one(
        """SELECT (SELECT COUNT(*) FROM vw_expense_split_check
                   WHERE expense_status = 'active' AND is_balanced = 0)           AS unbalanced_expenses,
                  (SELECT COUNT(*) FROM vw_expense_split_check
                   WHERE expense_status = 'draft')                                AS stale_drafts,
                  (SELECT COUNT(*) FROM (SELECT group_id FROM vw_member_balances
                                         GROUP BY group_id HAVING SUM(net_balance) <> 0) t) AS groups_not_summing_to_zero"""
    )
    system = fetch_one("SELECT VERSION() AS mysql_version, @@event_scheduler AS event_scheduler")
    event = fetch_one(
        """SELECT event_name, status, last_executed, starts FROM information_schema.events
           WHERE event_schema = DATABASE() AND event_name = 'ev_mark_overdue_settlements'"""
    )
    recent_users = fetch_all("SELECT full_name, username, role, created_at FROM users ORDER BY user_id DESC LIMIT 5")
    return render_template("admin_dashboard.html", counts=counts, checks=checks, system=system,
                           event=event, recent_users=recent_users)


@bp.route("/run-overdue-check", methods=["POST"])
@admin_required
def run_overdue_check():
    try:
        rows = call_proc_rows("sp_mark_overdue_settlements")
        n = rows[0]["settlements_marked_overdue"] if rows else 0
        flash(f"Overdue check finished: {n} settlement(s) marked overdue.", "success")
    except UserError as err:
        flash(str(err), "danger")
    return redirect(url_for("admin.dashboard"))


@bp.route("/users")
@admin_required
def users():
    rows = fetch_all("SELECT user_id, full_name, username, email, role, is_active, created_at FROM users ORDER BY user_id")
    return render_template("admin_users.html", users=rows)


@bp.route("/users/new", methods=["POST"])
@admin_required
def create_user():
    try:
        full_name = clean_text(request.form.get("full_name"), "Full name", 100)
        username = clean_username(request.form.get("username"))
        email = clean_email(request.form.get("email"))
        password = validate_password(request.form.get("password"))
        # there is no way to choose a role here: accounts created by the admin are always regular users
        execute("INSERT INTO users (full_name, username, email, password_hash, role) VALUES (%s, %s, %s, %s, 'user')",
                (full_name, username, email, generate_password_hash(password)))
        flash("User created.", "success")
    except (ValidationError, UserError) as err:
        flash(str(err), "danger")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def toggle_user(user_id):
    target = fetch_one("SELECT role FROM users WHERE user_id = %s", (user_id,))
    if target is None:
        abort(404)
    try:
        execute("UPDATE users SET is_active = IF(is_active = 1, 0, 1) WHERE user_id = %s", (user_id,))
        flash("Account status updated.", "success")
    except UserError as err:
        flash(str(err), "danger")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def reset_password(user_id):
    try:
        new = validate_password(request.form.get("new_password"))
        if fetch_one("SELECT user_id FROM users WHERE user_id = %s", (user_id,)) is None:
            abort(404)
        execute("UPDATE users SET password_hash = %s WHERE user_id = %s", (generate_password_hash(new), user_id))
        flash("Password reset.", "success")
    except (ValidationError, UserError) as err:
        flash(str(err), "danger")
    return redirect(url_for("admin.users"))
