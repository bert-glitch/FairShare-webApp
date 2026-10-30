"""Who is logged in, and who may do what. Checked on the SERVER for every protected action."""
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for

from .db import fetch_one


def load_user():
    """Run before every request: find the logged-in user (or None)."""
    g.user = None
    uid = session.get("user_id")
    if uid:
        user = fetch_one(
            "SELECT user_id, full_name, username, email, role, is_active FROM users WHERE user_id = %s", (uid,)
        )
        if user and user["is_active"]:
            g.user = user
        else:
            session.clear()          # deleted or deactivated account: end the session


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.user is None:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def is_admin():
    return g.user is not None and g.user["role"] == "admin"


def get_group(group_id):
    """The group's summary row, or a 404 page."""
    group = fetch_one("SELECT * FROM vw_group_expense_summary WHERE group_id = %s", (group_id,))
    if group is None:
        abort(404)
    return group


def get_membership(group_id, user_id):
    return fetch_one(
        "SELECT * FROM group_members WHERE group_id = %s AND user_id = %s AND status = 'active'",
        (group_id, user_id),
    )


def group_access(group_id, manage=False):
    """Return the group if the current user may use it, otherwise stop with 403.

    view   : the administrator, or an ACTIVE member of the group
    manage : the administrator, or the person who created the group
    """
    group = get_group(group_id)
    if is_admin():
        return group
    if manage:
        if group["created_by"] != g.user["user_id"]:
            abort(403)
        return group
    if get_membership(group_id, g.user["user_id"]) is None:
        abort(403)
    return group


def can_manage(group):
    return is_admin() or group["created_by"] == g.user["user_id"]
