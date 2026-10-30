"""Dashboard and the user's own profile."""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .access import is_admin, login_required
from .db import UserError, execute, fetch_all, fetch_one
from .validators import ValidationError, clean_email, clean_text, validate_password

bp = Blueprint("main", __name__)


@bp.route("/")
def home():
    return redirect(url_for("main.dashboard") if g.user else url_for("auth.login"))


@bp.route("/dashboard")
@login_required
def dashboard():
    uid = g.user["user_id"]
    groups = fetch_all(
        """SELECT b.group_id, b.group_name, b.net_balance, b.amount_owed_by_member,
                  b.amount_owed_to_member, s.active_expense_total, s.active_member_count,
                  s.open_settlement_count
           FROM vw_member_balances b
           JOIN vw_group_expense_summary s ON s.group_id = b.group_id
           WHERE b.user_id = %s AND b.membership_status = 'active' AND s.group_status = 'active'
           ORDER BY b.group_name""",
        (uid,),
    )
    totals = {
        "groups": len(groups),
        "owed_by_me": sum(g_["amount_owed_by_member"] for g_ in groups),
        "owed_to_me": sum(g_["amount_owed_to_member"] for g_ in groups),
    }
    open_payments = fetch_all(
        """SELECT st.settlement_id, st.group_id, eg.group_name, st.amount, st.status, st.due_date,
                  p.full_name AS payer_name, r.full_name AS receiver_name
           FROM settlements st
           JOIN expense_groups eg ON eg.group_id = st.group_id
           JOIN users p ON p.user_id = st.payer_id
           JOIN users r ON r.user_id = st.receiver_id
           WHERE st.status IN ('pending', 'overdue') AND (st.payer_id = %s OR st.receiver_id = %s)
           ORDER BY st.due_date IS NULL, st.due_date, st.settlement_id""",
        (uid, uid),
    )
    return render_template("dashboard.html", groups=groups, totals=totals, open_payments=open_payments)


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "details":
                full_name = clean_text(request.form.get("full_name"), "Full name", 100)
                email = clean_email(request.form.get("email"))
                execute("UPDATE users SET full_name = %s, email = %s WHERE user_id = %s",
                        (full_name, email, g.user["user_id"]))
                flash("Your details were updated.", "success")
            elif action == "password":
                row = fetch_one("SELECT password_hash FROM users WHERE user_id = %s", (g.user["user_id"],))
                try:
                    current_ok = check_password_hash(row["password_hash"], request.form.get("current_password") or "")
                except ValueError:
                    current_ok = False
                if not current_ok:
                    raise ValidationError("Your current password is not correct.")
                new = validate_password(request.form.get("new_password"))
                if new != request.form.get("confirm_password"):
                    raise ValidationError("The two new passwords do not match.")
                execute("UPDATE users SET password_hash = %s WHERE user_id = %s",
                        (generate_password_hash(new), g.user["user_id"]))
                flash("Your password was changed.", "success")
            # NOTE: there is deliberately no way to change a role from this page.
            return redirect(url_for("main.profile"))
        except (ValidationError, UserError) as err:
            flash(str(err), "danger")
            return redirect(url_for("main.profile"))
    me = fetch_one("SELECT full_name, username, email, role, created_at FROM users WHERE user_id = %s",
                   (g.user["user_id"],))
    return render_template("profile.html", me=me)
