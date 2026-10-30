"""Settlements: recording, completing and cancelling payments between members.

All writes go through stored procedures. The database triggers decide whether a payment is
allowed (membership, amount, overpayment guard, status path); this module decides WHO may ask.
"""
from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .access import can_manage, group_access, login_required
from .db import UserError, call_proc, fetch_all, fetch_one
from .validators import ValidationError, parse_date, parse_int, parse_money

bp = Blueprint("settlements", __name__)

_SELECT = """SELECT st.settlement_id, st.group_id, st.payer_id, st.receiver_id, st.amount, st.status,
                    st.settlement_date, st.due_date, st.created_at, st.updated_at,
                    p.full_name AS payer_name, r.full_name AS receiver_name, c.full_name AS created_by_name
             FROM settlements st
             JOIN users p ON p.user_id = st.payer_id
             JOIN users r ON r.user_id = st.receiver_id
             JOIN users c ON c.user_id = st.created_by"""


def _members(group_id):
    return fetch_all(
        """SELECT u.user_id, u.full_name FROM group_members gm JOIN users u ON u.user_id = gm.user_id
           WHERE gm.group_id = %s AND gm.status = 'active' ORDER BY u.full_name""",
        (group_id,),
    )


@bp.route("/groups/<int:group_id>/settlements")
@login_required
def open_list(group_id):
    group = group_access(group_id)
    rows = fetch_all(_SELECT + " WHERE st.group_id = %s AND st.status IN ('pending', 'overdue') "
                               "ORDER BY st.due_date IS NULL, st.due_date, st.settlement_id", (group_id,))
    balances = fetch_all("SELECT * FROM vw_member_balances WHERE group_id = %s AND membership_status = 'active' "
                         "ORDER BY full_name", (group_id,))
    return render_template("settlements.html", group=group, settlements=rows, balances=balances,
                           manager=can_manage(group))


@bp.route("/groups/<int:group_id>/settlements/history")
@login_required
def history(group_id):
    group = group_access(group_id)
    status = request.args.get("status", "all")
    sql, params = _SELECT + " WHERE st.group_id = %s", [group_id]
    if status in ("pending", "completed", "overdue", "cancelled"):
        sql += " AND st.status = %s"
        params.append(status)
    else:
        status = "all"
    rows = fetch_all(sql + " ORDER BY st.settlement_id DESC", params)
    return render_template("settlement_history.html", group=group, settlements=rows, status=status,
                           manager=can_manage(group))


@bp.route("/groups/<int:group_id>/settlements/new", methods=["GET", "POST"])
@login_required
def new(group_id):
    group = group_access(group_id)
    members = _members(group_id)
    manager = can_manage(group)
    form = request.form if request.method == "POST" else {}
    if request.method == "POST":
        try:
            payer_id = parse_int(request.form.get("payer_id"), "Payer")
            receiver_id = parse_int(request.form.get("receiver_id"), "Receiver")
            amount = parse_money(request.form.get("amount"), "Amount")
            when = parse_date(request.form.get("settlement_date"), "Payment date")
            due = parse_date(request.form.get("due_date"), "Due date", required=False)
            completed = request.form.get("mark_completed") == "1"
            uid = g.user["user_id"]
            if not manager and uid not in (payer_id, receiver_id):
                raise UserError("You can only record payments that you make or receive.")
            if completed and not (manager or uid == receiver_id):
                raise UserError("Only the person who receives a payment can record it as already completed.")
            call_proc("sp_record_settlement",
                      [group_id, payer_id, receiver_id, amount, when, due, 1 if completed else 0, uid, 0])
            flash("Payment recorded as completed." if completed else
                  "Payment recorded as pending. It counts once the receiver confirms it.", "success")
            return redirect(url_for("settlements.open_list", group_id=group_id))
        except (ValidationError, UserError) as err:
            flash(str(err), "danger")
    balances = fetch_all("SELECT * FROM vw_member_balances WHERE group_id = %s AND membership_status = 'active' "
                         "ORDER BY full_name", (group_id,))
    return render_template("settlement_new.html", group=group, members=members, balances=balances,
                           form=form, today=date.today().isoformat(), manager=manager)


def _load(settlement_id):
    row = fetch_one(_SELECT + " WHERE st.settlement_id = %s", (settlement_id,))
    if row is None:
        abort(404)
    return row


@bp.route("/settlements/<int:settlement_id>/complete", methods=["POST"])
@login_required
def complete(settlement_id):
    st = _load(settlement_id)
    group = group_access(st["group_id"])
    if not (can_manage(group) or g.user["user_id"] == st["receiver_id"]):
        abort(403)
    try:
        call_proc("sp_complete_settlement", [settlement_id])
        flash("The payment is completed and now counts toward balances.", "success")
    except UserError as err:
        flash(str(err), "danger")
    return redirect(url_for("settlements.open_list", group_id=st["group_id"]))


@bp.route("/settlements/<int:settlement_id>/cancel", methods=["POST"])
@login_required
def cancel(settlement_id):
    st = _load(settlement_id)
    group = group_access(st["group_id"])
    if not (can_manage(group) or g.user["user_id"] in (st["payer_id"], st["receiver_id"])):
        abort(403)
    try:
        call_proc("sp_cancel_settlement", [settlement_id])
        flash("The payment was cancelled. The record is kept.", "success")
    except UserError as err:
        flash(str(err), "danger")
    return redirect(url_for("settlements.open_list", group_id=st["group_id"]))
