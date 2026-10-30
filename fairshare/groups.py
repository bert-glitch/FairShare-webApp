"""Groups, group members, member balances and expense categories."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .access import admin_required, can_manage, group_access, is_admin, login_required
from .db import UserError, call_proc, execute, fetch_all, fetch_one
from .validators import ValidationError, clean_text

bp = Blueprint("groups", __name__)


@bp.route("/groups")
@login_required
def list_groups():
    if is_admin():
        rows = fetch_all("SELECT * FROM vw_group_expense_summary ORDER BY group_status, group_name")
    else:
        rows = fetch_all(
            """SELECT s.* FROM vw_group_expense_summary s
               JOIN group_members gm ON gm.group_id = s.group_id
               WHERE gm.user_id = %s AND gm.status = 'active'
               ORDER BY s.group_status, s.group_name""",
            (g.user["user_id"],),
        )
    return render_template("groups.html", groups=rows)


@bp.route("/groups/new", methods=["POST"])
@login_required
def create_group():
    try:
        name = clean_text(request.form.get("group_name"), "Group name", 100)
        description = clean_text(request.form.get("description"), "Description", 255, required=False) or None
        result = call_proc("sp_create_group", [name, description, g.user["user_id"], 0])
        flash("Group created. You are its first member.", "success")
        return redirect(url_for("groups.detail", group_id=result[3]))
    except (ValidationError, UserError) as err:
        flash(str(err), "danger")
        return redirect(url_for("groups.list_groups"))


@bp.route("/groups/<int:group_id>")
@login_required
def detail(group_id):
    group = group_access(group_id)
    members = fetch_all(
        "SELECT * FROM vw_member_balances WHERE group_id = %s AND membership_status = 'active' ORDER BY full_name",
        (group_id,),
    )
    recent = fetch_all(
        """SELECT e.expense_id, e.expense_title, e.amount, e.expense_date, u.full_name AS paid_by_name
           FROM expenses e JOIN users u ON u.user_id = e.paid_by
           WHERE e.group_id = %s AND e.status = 'active'
           ORDER BY e.expense_date DESC, e.expense_id DESC LIMIT 5""",
        (group_id,),
    )
    open_payments = fetch_all(
        """SELECT st.settlement_id, st.amount, st.status, st.due_date,
                  p.full_name AS payer_name, r.full_name AS receiver_name
           FROM settlements st JOIN users p ON p.user_id = st.payer_id JOIN users r ON r.user_id = st.receiver_id
           WHERE st.group_id = %s AND st.status IN ('pending', 'overdue')
           ORDER BY st.due_date IS NULL, st.due_date""",
        (group_id,),
    )
    return render_template("group_detail.html", group=group, members=members, recent=recent,
                           open_payments=open_payments, manager=can_manage(group))


@bp.route("/groups/<int:group_id>/balances")
@login_required
def balances(group_id):
    group = group_access(group_id)
    rows = fetch_all(
        "SELECT * FROM vw_member_balances WHERE group_id = %s ORDER BY membership_status, full_name", (group_id,)
    )
    total = sum(r["net_balance"] for r in rows)
    return render_template("balances.html", group=group, rows=rows, total=total)


@bp.route("/groups/<int:group_id>/members")
@login_required
def members(group_id):
    group = group_access(group_id)
    rows = fetch_all(
        """SELECT gm.user_id, gm.status, gm.joined_at, u.full_name, u.username
           FROM group_members gm JOIN users u ON u.user_id = gm.user_id
           WHERE gm.group_id = %s ORDER BY gm.status, u.full_name""",
        (group_id,),
    )
    return render_template("group_members.html", group=group, members=rows, manager=can_manage(group))


@bp.route("/groups/<int:group_id>/members/add", methods=["POST"])
@login_required
def add_member(group_id):
    group_access(group_id, manage=True)
    username = (request.form.get("username") or "").strip()
    try:
        target = fetch_one("SELECT user_id, is_active FROM users WHERE username = %s", (username,))
        if target is None or not target["is_active"]:
            raise UserError("No active user has that username.")
        existing = fetch_one("SELECT status FROM group_members WHERE group_id = %s AND user_id = %s",
                             (group_id, target["user_id"]))
        if existing and existing["status"] == "active":
            raise UserError("That person is already in this group.")
        if existing:      # they left earlier: bring the same membership row back
            execute("UPDATE group_members SET status = 'active' WHERE group_id = %s AND user_id = %s",
                    (group_id, target["user_id"]))
        else:
            execute("INSERT INTO group_members (group_id, user_id) VALUES (%s, %s)", (group_id, target["user_id"]))
        flash("Member added.", "success")
    except UserError as err:
        flash(str(err), "danger")
    return redirect(url_for("groups.members", group_id=group_id))


@bp.route("/groups/<int:group_id>/members/<int:user_id>/remove", methods=["POST"])
@login_required
def remove_member(group_id, user_id):
    group = group_access(group_id)
    if not (can_manage(group) or user_id == g.user["user_id"]):
        abort(403)
    try:
        execute("UPDATE group_members SET status = 'left' WHERE group_id = %s AND user_id = %s AND status = 'active'",
                (group_id, user_id))
        flash("The member has left the group.", "success")
    except UserError as err:
        flash(str(err), "danger")
    if user_id == g.user["user_id"] and not is_admin():
        return redirect(url_for("groups.list_groups"))
    return redirect(url_for("groups.members", group_id=group_id))


@bp.route("/groups/<int:group_id>/archive", methods=["POST"])
@login_required
def archive_group(group_id):
    group_access(group_id, manage=True)
    try:
        execute("UPDATE expense_groups SET status = 'archived' WHERE group_id = %s", (group_id,))
        flash("The group was archived.", "success")
    except UserError as err:
        flash(str(err), "danger")
    return redirect(url_for("groups.detail", group_id=group_id))


@bp.route("/groups/<int:group_id>/reactivate", methods=["POST"])
@admin_required
def reactivate_group(group_id):
    try:
        execute("UPDATE expense_groups SET status = 'active' WHERE group_id = %s", (group_id,))
        flash("The group is active again.", "success")
    except UserError as err:
        flash(str(err), "danger")
    return redirect(url_for("groups.detail", group_id=group_id))


# ---------------------------------------------------------------- categories
@bp.route("/categories", methods=["GET", "POST"])
@login_required
def categories():
    if request.method == "POST":
        if not is_admin():
            abort(403)
        try:
            action = request.form.get("action")
            if action == "add":
                name = clean_text(request.form.get("category_name"), "Category name", 50)
                description = clean_text(request.form.get("description"), "Description", 255, required=False) or None
                execute("INSERT INTO expense_categories (category_name, description) VALUES (%s, %s)", (name, description))
                flash("Category added.", "success")
            elif action == "toggle":
                cid = int(request.form.get("category_id", "0"))
                execute("UPDATE expense_categories SET status = IF(status = 'active', 'inactive', 'active') "
                        "WHERE category_id = %s", (cid,))
                flash("Category updated.", "success")
        except (ValidationError, UserError) as err:
            flash(str(err), "danger")
        except ValueError:
            flash("That request was not valid.", "danger")
        return redirect(url_for("groups.categories"))
    rows = fetch_all(
        """SELECT c.*, (SELECT COUNT(*) FROM expenses e WHERE e.category_id = c.category_id) AS expense_count
           FROM expense_categories c ORDER BY c.category_name"""
    )
    return render_template("categories.html", categories=rows)
