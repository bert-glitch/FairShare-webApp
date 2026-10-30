"""Recording expenses (through sp_add_expense), history, details and archiving."""
import json
from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .access import can_manage, group_access, is_admin, login_required
from .db import UserError, call_proc, fetch_all, fetch_one
from .validators import (ValidationError, clean_text, parse_date, parse_int, parse_money)

bp = Blueprint("expenses", __name__)


def _active_members(group_id):
    return fetch_all(
        """SELECT u.user_id, u.full_name FROM group_members gm JOIN users u ON u.user_id = gm.user_id
           WHERE gm.group_id = %s AND gm.status = 'active' ORDER BY u.full_name""",
        (group_id,),
    )


def _categories():
    return fetch_all("SELECT category_id, category_name FROM expense_categories WHERE status = 'active' ORDER BY category_name")


@bp.route("/groups/<int:group_id>/expenses")
@login_required
def history(group_id):
    group = group_access(group_id)
    status = request.args.get("status", "active")
    if status not in ("active", "archived"):
        status = "active"
    rows = fetch_all(
        """SELECT e.expense_id, e.expense_title, e.amount, e.expense_date, e.status,
                  c.category_name, u.full_name AS paid_by_name
           FROM expenses e
           JOIN expense_categories c ON c.category_id = e.category_id
           JOIN users u ON u.user_id = e.paid_by
           WHERE e.group_id = %s AND e.status = %s
           ORDER BY e.expense_date DESC, e.expense_id DESC""",
        (group_id, status),
    )
    return render_template("expenses.html", group=group, expenses=rows, status=status)


@bp.route("/groups/<int:group_id>/expenses/new", methods=["GET", "POST"])
@login_required
def new(group_id):
    group = group_access(group_id)
    members = _active_members(group_id)
    form = request.form if request.method == "POST" else {}
    if request.method == "POST":
        try:
            title = clean_text(request.form.get("expense_title"), "Title", 100)
            description = clean_text(request.form.get("description"), "Description", 255, required=False) or None
            amount = parse_money(request.form.get("amount"), "Amount")
            when = parse_date(request.form.get("expense_date"), "Expense date")
            category_id = parse_int(request.form.get("category_id"), "Category")
            paid_by = parse_int(request.form.get("paid_by"), "Payer")
            mode = request.form.get("split_mode")
            if mode == "equal":
                ids = [parse_int(v, "Member") for v in request.form.getlist("participants")]
                if not ids:
                    raise ValidationError("Choose at least one member to share this expense.")
                shares = ids
            elif mode == "custom":
                shares = []
                for m in members:
                    raw = (request.form.get(f"share_{m['user_id']}") or "").strip()
                    if raw:
                        shares.append({"user_id": m["user_id"],
                                       "amount": str(parse_money(raw, f"Share for {m['full_name']}", allow_zero=True))})
                if not shares:
                    raise ValidationError("Enter at least one share.")
            else:
                raise ValidationError("Choose how to split the expense.")
            result = call_proc("sp_add_expense", [group_id, paid_by, category_id, title, description, amount,
                                                  when, mode, json.dumps(shares), g.user["user_id"], 0])
            flash("Expense saved and split.", "success")
            return redirect(url_for("expenses.detail", expense_id=result[-1]))
        except (ValidationError, UserError) as err:
            flash(str(err), "danger")
    return render_template("expense_new.html", group=group, members=members, categories=_categories(),
                           form=form, today=date.today().isoformat())


def _load_expense(expense_id):
    row = fetch_one(
        """SELECT e.*, c.category_name, p.full_name AS paid_by_name, cr.full_name AS created_by_name
           FROM expenses e
           JOIN expense_categories c ON c.category_id = e.category_id
           JOIN users p  ON p.user_id  = e.paid_by
           JOIN users cr ON cr.user_id = e.created_by
           WHERE e.expense_id = %s AND e.status <> 'draft'""",
        (expense_id,),
    )
    if row is None:
        abort(404)
    return row


@bp.route("/expenses/<int:expense_id>")
@login_required
def detail(expense_id):
    expense = _load_expense(expense_id)
    group = group_access(expense["group_id"])
    splits = fetch_all(
        """SELECT es.user_id, es.share_amount, u.full_name FROM expense_splits es
           JOIN users u ON u.user_id = es.user_id WHERE es.expense_id = %s ORDER BY u.full_name""",
        (expense_id,),
    )
    check = fetch_one("SELECT allocated_total, difference, is_balanced FROM vw_expense_split_check WHERE expense_id = %s",
                      (expense_id,))
    can_archive = expense["status"] == "active" and (
        can_manage(group) or expense["created_by"] == g.user["user_id"])
    return render_template("expense_detail.html", expense=expense, group=group, splits=splits,
                           check=check, can_archive=can_archive)


@bp.route("/expenses/<int:expense_id>/archive", methods=["POST"])
@login_required
def archive(expense_id):
    expense = _load_expense(expense_id)
    group = group_access(expense["group_id"])
    if not (can_manage(group) or expense["created_by"] == g.user["user_id"]):
        abort(403)
    try:
        call_proc("sp_archive_expense", [expense_id])
        flash("The expense was archived. It no longer counts toward balances.", "success")
    except UserError as err:
        flash(str(err), "danger")
    return redirect(url_for("expenses.detail", expense_id=expense_id))
