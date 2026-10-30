"""Each required database object: view, stored function, stored procedures, triggers, event."""
import json
import time
from decimal import Decimal

import mysql.connector
import pytest

from .helpers import (add_expense, call, category_id, count, net, new_group, new_user, rows, run,
                      scalar, settle)

D = Decimal


def rejected(fn, text=None, errno=None):
    with pytest.raises(mysql.connector.Error) as info:
        fn()
    if errno is not None:
        assert info.value.errno == errno, info.value.msg
    if text is not None:
        assert text.lower() in info.value.msg.lower(), info.value.msg
    return info.value


def snapshot(conn, gid):
    """Row counts that must NOT change when an operation fails."""
    return (count(conn, "expenses", "group_id = %s", (gid,)),
            count(conn, "expense_splits", "group_id = %s", (gid,)),
            count(conn, "settlements", "group_id = %s", (gid,)))


def trio(conn):
    a, b, c = new_user(conn), new_user(conn), new_user(conn)
    return a, b, c, new_group(conn, a, b, c)


# ====================================================================== VIEWS
def test_vw_member_balances_columns_and_values(conn):
    a, b, c, g = trio(conn)
    add_expense(conn, g, a, "900.00", [a, b, c])
    r = {x["user_id"]: x for x in rows(conn, "SELECT * FROM vw_member_balances WHERE group_id = %s", (g,))}
    assert r[a["id"]]["total_paid"] == D("900.00") and r[a["id"]]["total_share"] == D("300.00")
    assert r[a["id"]]["net_balance"] == D("600.00") and r[a["id"]]["amount_owed_to_member"] == D("600.00")
    assert r[b["id"]]["net_balance"] == D("-300.00") and r[b["id"]]["amount_owed_by_member"] == D("300.00")
    assert {"settlements_paid", "settlements_received", "membership_status"} <= set(r[a["id"]])


def test_vw_group_expense_summary(conn):
    a, b, c, g = trio(conn)
    e1 = add_expense(conn, g, a, "100.00", [a, b])
    add_expense(conn, g, b, "50.50", [a, b, c])
    call(conn, "sp_archive_expense", [e1])
    settle(conn, g, c, b, "10.00", completed=True)
    settle(conn, g, c, b, "5.00", due=None)
    s = rows(conn, "SELECT * FROM vw_group_expense_summary WHERE group_id = %s", (g,))[0]
    assert s["active_member_count"] == 3
    assert s["active_expense_count"] == 1 and s["active_expense_total"] == D("50.50")
    assert s["archived_expense_count"] == 1
    assert s["completed_settlement_total"] == D("10.00")
    assert s["open_settlement_count"] == 1 and s["open_settlement_total"] == D("5.00")


def test_vw_expense_split_check_flags_unbalanced_drafts_and_passes_good_expenses(conn):
    a, b, c, g = trio(conn)
    good = add_expense(conn, g, a, "100.00", [a, b, c])
    run(conn, "INSERT INTO expenses (group_id, paid_by, category_id, created_by, expense_title, amount, expense_date) "
              "VALUES (%s, %s, %s, %s, 'half done', 80.00, CURDATE())", (g, a["id"], category_id(conn), a["id"]))
    bad = scalar(conn, "SELECT MAX(expense_id) FROM expenses WHERE group_id = %s", (g,))
    run(conn, "INSERT INTO expense_splits (expense_id, group_id, user_id, share_amount) VALUES (%s, %s, %s, 30.00)", (bad, g, a["id"]))
    r = {x["expense_id"]: x for x in rows(conn, "SELECT * FROM vw_expense_split_check WHERE group_id = %s", (g,))}
    assert r[good]["is_balanced"] == 1 and r[good]["difference"] == D("0.00") and r[good]["allocated_total"] == D("100.00")
    assert r[bad]["is_balanced"] == 0 and r[bad]["difference"] == D("50.00") and r[bad]["expense_status"] == "draft"


# ====================================================================== FUNCTIONS
def test_fn_member_total_share(conn):
    a, b, c, g = trio(conn)
    assert scalar(conn, "SELECT fn_member_total_share(%s, %s)", (g, a["id"])) == D("0.00")     # nothing yet
    e = add_expense(conn, g, a, "100.00", [a, b, c])                                          # 33.34 / 33.33 / 33.33
    add_expense(conn, g, b, "60.00", [a, b])
    assert scalar(conn, "SELECT fn_member_total_share(%s, %s)", (g, a["id"])) == D("63.34")
    assert scalar(conn, "SELECT fn_member_total_share(%s, %s)", (g, c["id"])) == D("33.33")
    call(conn, "sp_archive_expense", [e])                                                     # archived does not count
    assert scalar(conn, "SELECT fn_member_total_share(%s, %s)", (g, a["id"])) == D("30.00")
    other = new_user(conn)
    assert scalar(conn, "SELECT fn_member_total_share(%s, %s)", (g, other["id"])) == D("0.00")  # not a member
    assert scalar(conn, "SELECT fn_member_total_share(NULL, %s)", (a["id"],)) == D("0.00")      # NULL-safe
    assert scalar(conn, "SELECT fn_member_total_share(%s, NULL)", (g,)) == D("0.00")


def test_function_matches_the_view_for_every_member(conn):
    a, b, c, g = trio(conn)
    add_expense(conn, g, a, "100.00", [a, b, c])
    add_expense(conn, g, b, "77.77", [a, c])
    settle(conn, g, c, a, "20.00", completed=True)
    for r in rows(conn, "SELECT * FROM vw_member_balances WHERE group_id = %s", (g,)):
        assert scalar(conn, "SELECT fn_member_total_share(%s, %s)", (g, r["user_id"])) == r["total_share"]
        assert scalar(conn, "SELECT fn_member_net_balance(%s, %s)", (g, r["user_id"])) == r["net_balance"]


# ====================================================================== sp_create_group / sp_add_expense
def test_sp_create_group_makes_the_creator_a_member(conn):
    a = new_user(conn)
    gid = call(conn, "sp_create_group", ["Study group", "desc", a["id"], 0])[3]
    assert scalar(conn, "SELECT status FROM group_members WHERE group_id = %s AND user_id = %s", (gid, a["id"])) == "active"


def test_sp_create_group_rolls_back_completely_when_the_creator_is_inactive(conn):
    a = new_user(conn)
    run(conn, "UPDATE users SET is_active = 0 WHERE user_id = %s", (a["id"],))
    before = count(conn, "expense_groups")
    rejected(lambda: call(conn, "sp_create_group", ["Ghost group", None, a["id"], 0]), "not active")
    assert count(conn, "expense_groups") == before          # no half-created group


def test_sp_add_expense_equal_and_custom_succeed(conn):
    a, b, c, g = trio(conn)
    e1 = add_expense(conn, g, a, "100.00", [c, a, b])                       # participant order does not matter
    assert [x["share_amount"] for x in rows(conn, "SELECT share_amount FROM expense_splits WHERE expense_id = %s ORDER BY user_id", (e1,))] \
        == [D("33.34"), D("33.33"), D("33.33")]
    e2 = add_expense(conn, g, b, "90.00", [(a, "10.00"), (b, "30.00"), (c, "50.00")], mode="custom")
    assert scalar(conn, "SELECT SUM(share_amount) FROM expense_splits WHERE expense_id = %s", (e2,)) == D("90.00")
    assert scalar(conn, "SELECT status FROM expenses WHERE expense_id = %s", (e2,)) == "active"


@pytest.mark.parametrize("label,make", [
    ("payer is not a member", lambda conn, g, a, b, c, out: [g, out["id"], category_id(conn), "t", None, D("10"), None, "equal", json.dumps([a["id"]]), a["id"], 0]),
    ("participant is not a member", lambda conn, g, a, b, c, out: [g, a["id"], category_id(conn), "t", None, D("10"), None, "equal", json.dumps([a["id"], out["id"]]), a["id"], 0]),
    ("duplicate participant", lambda conn, g, a, b, c, out: [g, a["id"], category_id(conn), "t", None, D("10"), None, "equal", json.dumps([a["id"], a["id"]]), a["id"], 0]),
    ("empty share list", lambda conn, g, a, b, c, out: [g, a["id"], category_id(conn), "t", None, D("10"), None, "equal", json.dumps([]), a["id"], 0]),
    ("bad split mode", lambda conn, g, a, b, c, out: [g, a["id"], category_id(conn), "t", None, D("10"), None, "thirds", json.dumps([a["id"]]), a["id"], 0]),
    ("unknown category", lambda conn, g, a, b, c, out: [g, a["id"], 99999, "t", None, D("10"), None, "equal", json.dumps([a["id"]]), a["id"], 0]),
    ("custom shares too small", lambda conn, g, a, b, c, out: [g, a["id"], category_id(conn), "t", None, D("100"), None, "custom", json.dumps([{"user_id": a["id"], "amount": "40.00"}, {"user_id": b["id"], "amount": "50.00"}]), a["id"], 0]),
    ("custom shares too large", lambda conn, g, a, b, c, out: [g, a["id"], category_id(conn), "t", None, D("100"), None, "custom", json.dumps([{"user_id": a["id"], "amount": "60.00"}, {"user_id": b["id"], "amount": "50.00"}]), a["id"], 0]),
    ("custom share missing amount", lambda conn, g, a, b, c, out: [g, a["id"], category_id(conn), "t", None, D("100"), None, "custom", json.dumps([{"user_id": a["id"]}]), a["id"], 0]),
    ("custom negative share", lambda conn, g, a, b, c, out: [g, a["id"], category_id(conn), "t", None, D("100"), None, "custom", json.dumps([{"user_id": a["id"], "amount": "150.00"}, {"user_id": b["id"], "amount": "-50.00"}]), a["id"], 0]),
    ("zero amount", lambda conn, g, a, b, c, out: [g, a["id"], category_id(conn), "t", None, D("0"), None, "equal", json.dumps([a["id"]]), a["id"], 0]),
    ("expense dated in the future", lambda conn, g, a, b, c, out: [g, a["id"], category_id(conn), "t", None, D("10"), "2999-01-01", "equal", json.dumps([a["id"]]), a["id"], 0]),
    ("group does not exist", lambda conn, g, a, b, c, out: [999999, a["id"], category_id(conn), "t", None, D("10"), None, "equal", json.dumps([a["id"]]), a["id"], 0]),
])
def test_sp_add_expense_rejects_invalid_input_and_rolls_back(conn, label, make):
    a, b, c, g = trio(conn)
    outsider = new_user(conn)
    add_expense(conn, g, a, "10.00", [a, b])                     # some existing data to compare against
    before = snapshot(conn, g)
    drafts_before = count(conn, "expenses", "status = 'draft'")
    rejected(lambda: call(conn, "sp_add_expense", make(conn, g, a, b, c, outsider)))
    assert snapshot(conn, g) == before, f"'{label}' left partial records behind"
    assert count(conn, "expenses", "status = 'draft'") == drafts_before


def test_sp_add_expense_into_an_archived_group_is_rejected(conn):
    a, b = new_user(conn), new_user(conn)
    g = new_group(conn, a, b)
    run(conn, "UPDATE expense_groups SET status = 'archived' WHERE group_id = %s", (g,))
    rejected(lambda: add_expense(conn, g, a, "10.00", [a, b]), "archived")


# ====================================================================== settlements: procedures
def test_sp_record_settlement_valid_and_invalid(conn):
    a, b, c, g = trio(conn)
    add_expense(conn, g, a, "900.00", [a, b, c])                     # b and c owe 300 each, a is owed 600
    sid = settle(conn, g, b, a, "300.00", completed=True)
    assert scalar(conn, "SELECT status FROM settlements WHERE settlement_id = %s", (sid,)) == "completed"
    assert net(conn, g, b) == D("0.00") and net(conn, g, a) == D("300.00")
    before = snapshot(conn, g)
    outsider = new_user(conn)
    rejected(lambda: settle(conn, g, c, c, "10.00"), "pay themselves")
    rejected(lambda: settle(conn, g, c, a, "0"), "greater than zero")
    rejected(lambda: settle(conn, g, c, a, "-5"), "greater than zero")
    rejected(lambda: settle(conn, g, c, a, "300.01"), "more than the payer still owes")
    rejected(lambda: settle(conn, g, b, a, "1.00"), "more than the payer still owes")      # b is already settled
    rejected(lambda: settle(conn, g, outsider, a, "5.00"), "not an active member")
    rejected(lambda: settle(conn, g, c, outsider, "5.00"), "not an active member")
    rejected(lambda: settle(conn, g, a, c, "5.00"), "more than the payer still owes")      # a is owed, owes nothing
    assert snapshot(conn, g) == before


def test_overpayment_guard_also_checks_what_the_receiver_is_owed(conn):
    a, b, c, g = trio(conn)
    add_expense(conn, g, a, "300.00", [a, b, c])      # a +200, b -100, c -100
    add_expense(conn, g, b, "30.00", [b, c])          # b +15 -15 = 0 net change... c -15, b +15
    # c owes 115, a is owed 200, b owes 85. c may pay at most 115. b->c is impossible (c is owed nothing)
    rejected(lambda: settle(conn, g, b, c, "5.00"), "more than the receiver is still owed")


def test_pending_payments_reserve_the_debt(conn):
    a, b, c, g = trio(conn)
    add_expense(conn, g, a, "600.00", [a, b, c])      # b owes 200, c owes 200
    settle(conn, g, b, a, "150.00", due=None)          # pending: 150 of b's 200 is reserved
    rejected(lambda: settle(conn, g, b, a, "60.00"), "more than the payer still owes")
    settle(conn, g, b, a, "50.00")                     # exactly the rest is fine
    assert net(conn, g, b) == D("-200.00")             # pending payments do not change balances


def test_sp_complete_settlement(conn):
    a, b, c, g = trio(conn)
    add_expense(conn, g, a, "600.00", [a, b, c])
    sid = settle(conn, g, b, a, "200.00")              # pending
    assert net(conn, g, b) == D("-200.00")
    call(conn, "sp_complete_settlement", [sid])
    assert net(conn, g, b) == D("0.00") and net(conn, g, a) == D("200.00")
    rejected(lambda: call(conn, "sp_complete_settlement", [sid]), "already completed")      # exactly once
    assert net(conn, g, b) == D("0.00")                # not counted twice
    rejected(lambda: call(conn, "sp_complete_settlement", [999999]), "does not exist")


def test_sp_cancel_settlement(conn):
    a, b, c, g = trio(conn)
    add_expense(conn, g, a, "600.00", [a, b, c])
    pending = settle(conn, g, b, a, "100.00")
    done = settle(conn, g, c, a, "100.00", completed=True)
    call(conn, "sp_cancel_settlement", [pending])
    assert scalar(conn, "SELECT status FROM settlements WHERE settlement_id = %s", (pending,)) == "cancelled"
    assert count(conn, "settlements", "settlement_id = %s", (pending,)) == 1             # record preserved
    rejected(lambda: call(conn, "sp_cancel_settlement", [pending]), "already cancelled")
    rejected(lambda: call(conn, "sp_cancel_settlement", [done]), "completed settlement cannot be cancelled")
    rejected(lambda: call(conn, "sp_complete_settlement", [pending]), "cancelled settlement cannot be completed")
    assert net(conn, g, b) == D("-200.00")             # cancelled never affected balances
    settle(conn, g, b, a, "200.00")                    # and the reservation was released


# ====================================================================== settlement TRIGGERS (direct SQL)
def _insert_settlement(conn, g, payer, receiver, amount, status="completed", due=None, created_by=None, when="CURDATE()"):
    return run(conn, f"INSERT INTO settlements (group_id, payer_id, receiver_id, created_by, amount, settlement_date, due_date, status) "
                     f"VALUES (%s, %s, %s, %s, %s, {when}, %s, %s)",
               (g, payer["id"], receiver["id"], (created_by or receiver)["id"], amount, due, status))


def test_trigger_settlement_before_insert_rejects_bad_direct_inserts(conn):
    a, b, c, g = trio(conn)
    add_expense(conn, g, a, "900.00", [a, b, c])
    outsider = new_user(conn)
    cases = [
        (lambda: _insert_settlement(conn, g, b, b, "10.00"), "pay themselves"),
        (lambda: _insert_settlement(conn, g, b, a, "0"), "greater than zero"),
        (lambda: _insert_settlement(conn, g, b, a, "-1"), "greater than zero"),
        (lambda: _insert_settlement(conn, g, b, a, "300.01"), "more than the payer still owes"),
        (lambda: _insert_settlement(conn, g, outsider, a, "5"), "not an active member"),
        (lambda: _insert_settlement(conn, g, b, outsider, "5"), "not an active member"),
        (lambda: _insert_settlement(conn, g, b, a, "5", status="overdue", due="2999-01-01"), "must start as pending or completed"),
        (lambda: _insert_settlement(conn, g, b, a, "5", status="cancelled"), "must start as pending or completed"),
        (lambda: _insert_settlement(conn, g, b, a, "5", status="pending", due="2000-01-01"), "cannot be in the past"),
        (lambda: _insert_settlement(conn, g, b, a, "5", status="completed", when="CURDATE() + INTERVAL 3 DAY"), "future"),
    ]
    for fn, text in cases:
        rejected(fn, text)
    assert count(conn, "settlements", "group_id = %s", (g,)) == 0


def test_trigger_settlement_before_update_enforces_status_rules_and_freezes_fields(conn):
    a, b, c, g = trio(conn)
    add_expense(conn, g, a, "900.00", [a, b, c])
    far = "2999-12-31"
    big = _insert_settlement(conn, g, b, a, "300.00", status="pending", due=far)[0]
    # cannot jump to overdue before the due date, cannot change money fields
    rejected(lambda: run(conn, "UPDATE settlements SET status = 'overdue' WHERE settlement_id = %s", (big,)), "past its due date")
    rejected(lambda: run(conn, "UPDATE settlements SET amount = 1 WHERE settlement_id = %s", (big,)), "Only the status")
    rejected(lambda: run(conn, "UPDATE settlements SET payer_id = %s WHERE settlement_id = %s", (c["id"], big)), "Only the status")
    rejected(lambda: run(conn, "UPDATE settlements SET due_date = '2999-01-01' WHERE settlement_id = %s", (big,)), "Only the status")
    # completing is allowed once (it is within the debt) ...
    run(conn, "UPDATE settlements SET status = 'completed' WHERE settlement_id = %s", (big,))
    # ... and then it is final
    for new_status in ("pending", "cancelled", "overdue", "completed"):
        rejected(lambda s=new_status: run(conn, "UPDATE settlements SET status = %s WHERE settlement_id = %s", (s, big)), "final")
    rejected(lambda: run(conn, "UPDATE settlements SET amount = 5 WHERE settlement_id = %s", (big,)), "Only the status")


def test_completing_a_pending_settlement_directly_cannot_bypass_the_guard(conn):
    a, b, c, g = trio(conn)
    add_expense(conn, g, a, "600.00", [a, b, c])               # b owes 200
    first = _insert_settlement(conn, g, b, a, "200.00", status="pending", due="2999-01-01")[0]
    # b pays 200 in another way, so 'first' would now be an overpayment if it were completed
    run(conn, "UPDATE settlements SET status = 'cancelled' WHERE settlement_id = %s", (first,))
    done = _insert_settlement(conn, g, b, a, "200.00", status="completed")[0]
    assert done
    pending = _insert_settlement(conn, g, c, a, "200.00", status="pending", due="2999-01-01")[0]
    # make the pending one invalid by archiving the expense that created the debt
    eid = scalar(conn, "SELECT MAX(expense_id) FROM expenses WHERE group_id = %s", (g,))
    call(conn, "sp_archive_expense", [eid])
    rejected(lambda: run(conn, "UPDATE settlements SET status = 'completed' WHERE settlement_id = %s", (pending,)),
             "more than the payer still owes")
    assert scalar(conn, "SELECT status FROM settlements WHERE settlement_id = %s", (pending,)) == "pending"


def test_settlement_trigger_before_delete(conn):
    a, b, c, g = trio(conn)
    add_expense(conn, g, a, "600.00", [a, b, c])
    sid = settle(conn, g, b, a, "100.00")
    rejected(lambda: run(conn, "DELETE FROM settlements WHERE settlement_id = %s", (sid,)), "cannot be deleted")


# ====================================================================== other triggers
def test_expense_trigger_before_delete_and_before_update(conn):
    a, b, c, g = trio(conn)
    eid = add_expense(conn, g, a, "100.00", [a, b, c])
    rejected(lambda: run(conn, "DELETE FROM expenses WHERE expense_id = %s", (eid,)), "Archive the expense")
    rejected(lambda: run(conn, "UPDATE expenses SET amount = 5 WHERE expense_id = %s", (eid,)), "Only the status")
    rejected(lambda: run(conn, "UPDATE expenses SET status = 'draft' WHERE expense_id = %s", (eid,)), "not allowed")
    call(conn, "sp_archive_expense", [eid])
    rejected(lambda: run(conn, "UPDATE expenses SET status = 'active' WHERE expense_id = %s", (eid,)), "archived")
    rejected(lambda: call(conn, "sp_archive_expense", [eid]), "already archived")
    rejected(lambda: call(conn, "sp_archive_expense", [999999]), "does not exist")


def test_member_cannot_leave_with_a_balance_or_open_payment(conn):
    a, b, c, g = trio(conn)
    add_expense(conn, g, a, "600.00", [a, b, c])
    rejected(lambda: run(conn, "UPDATE group_members SET status = 'left' WHERE group_id = %s AND user_id = %s", (g, b["id"])),
             "still has a balance")
    settle(conn, g, b, a, "200.00", completed=True)
    settle(conn, g, c, a, "100.00")                      # c has an open payment
    rejected(lambda: run(conn, "UPDATE group_members SET status = 'left' WHERE group_id = %s AND user_id = %s", (g, c["id"])),
             "still has a balance")
    run(conn, "UPDATE group_members SET status = 'left' WHERE group_id = %s AND user_id = %s", (g, b["id"]))     # b is settled: may leave
    rejected(lambda: add_expense(conn, g, a, "30.00", [a, b]), "active member")                              # and gets no new shares
    rejected(lambda: settle(conn, g, c, b, "5.00"), "not an active member")
    run(conn, "UPDATE group_members SET status = 'active' WHERE group_id = %s AND user_id = %s", (g, b["id"]))   # may rejoin
    add_expense(conn, g, a, "30.00", [a, b])


def test_group_cannot_be_archived_until_everyone_is_settled(conn):
    a, b, g = new_user(conn), new_user(conn), None
    g = new_group(conn, a, b)
    add_expense(conn, g, a, "100.00", [a, b])
    rejected(lambda: run(conn, "UPDATE expense_groups SET status = 'archived' WHERE group_id = %s", (g,)), "settled up")
    settle(conn, g, b, a, "50.00", completed=True)
    run(conn, "UPDATE expense_groups SET status = 'archived' WHERE group_id = %s", (g,))
    rejected(lambda: run(conn, "UPDATE expense_groups SET created_by = %s WHERE group_id = %s", (b["id"], g)), "creator")


# ====================================================================== EVENT
def _late_pending(conn, g, payer, receiver, amount):
    """Create a pending settlement that is already past its due date (session clock moved back 10 days)."""
    run(conn, "SET TIMESTAMP = UNIX_TIMESTAMP(NOW() - INTERVAL 10 DAY)")
    try:
        return settle(conn, g, payer, receiver, amount, due=(__import__("datetime").date.today()
                      - __import__("datetime").timedelta(days=7)), when=None, by=payer)
    finally:
        run(conn, "SET TIMESTAMP = DEFAULT")


def test_event_exists_and_is_configured(conn):
    ev = rows(conn, "SELECT event_name, status, interval_value, interval_field, event_definition "
                    "FROM information_schema.events WHERE event_schema = DATABASE()")
    assert len(ev) == 1 and ev[0]["event_name"] == "ev_mark_overdue_settlements"
    assert ev[0]["status"] == "ENABLED" and ev[0]["interval_value"] == "1" and ev[0]["interval_field"] == "DAY"
    assert "sp_mark_overdue_settlements" in ev[0]["event_definition"]


def test_overdue_logic_only_touches_late_pending_settlements(conn):
    a, b, c, g = trio(conn)
    add_expense(conn, g, a, "900.00", [a, b, c])                 # b and c owe 300
    late = _late_pending(conn, g, b, a, "100.00")
    ontime = settle(conn, g, b, a, "50.00", due=__import__("datetime").date.today() + __import__("datetime").timedelta(days=5))
    nodue = settle(conn, g, b, a, "25.00", due=None)
    done = settle(conn, g, c, a, "100.00", completed=True)
    cancelled = settle(conn, g, c, a, "20.00")
    call(conn, "sp_cancel_settlement", [cancelled])
    assert scalar(conn, "SELECT status FROM settlements WHERE settlement_id = %s", (late,)) == "pending"
    balance_before = net(conn, g, b)

    marked = rows(conn, "SELECT 1")  # placeholder so the manual call below is clearly separate
    cur = conn.cursor(dictionary=True)
    cur.callproc("sp_mark_overdue_settlements")
    result = [r for rs in cur.stored_results() for r in rs.fetchall()]
    cur.close()
    assert result[0]["settlements_marked_overdue"] >= 1

    status = lambda sid: scalar(conn, "SELECT status FROM settlements WHERE settlement_id = %s", (sid,))
    assert status(late) == "overdue"
    assert status(ontime) == "pending" and status(nodue) == "pending"          # not late / no due date
    assert status(done) == "completed" and status(cancelled) == "cancelled"    # never touched
    assert net(conn, g, b) == balance_before                                   # overdue does not change balances
    # running it again changes nothing for this group
    call(conn, "sp_mark_overdue_settlements", [])
    assert status(late) == "overdue" and status(done) == "completed"
    # an overdue settlement can still be completed (and only then counts)
    call(conn, "sp_complete_settlement", [late])
    assert status(late) == "completed" and net(conn, g, b) == balance_before + D("100.00")


def test_the_real_scheduled_event_runs_on_the_server(conn):
    if scalar(conn, "SELECT @@event_scheduler") != "ON":
        pytest.skip("event scheduler is OFF on this server (SET GLOBAL event_scheduler = ON to run this test)")
    a, b, c, g = trio(conn)
    add_expense(conn, g, a, "900.00", [a, b, c])
    late = _late_pending(conn, g, b, a, "100.00")
    done = settle(conn, g, c, a, "100.00", completed=True)
    # make the REAL event fire in 2 seconds (the test database only), then put its schedule back
    run(conn, "ALTER EVENT ev_mark_overdue_settlements ON SCHEDULE AT CURRENT_TIMESTAMP + INTERVAL 2 SECOND ON COMPLETION PRESERVE ENABLE")
    try:
        for _ in range(20):
            time.sleep(1)
            if scalar(conn, "SELECT status FROM settlements WHERE settlement_id = %s", (late,)) == "overdue":
                break
    finally:
        run(conn, "ALTER EVENT ev_mark_overdue_settlements ON SCHEDULE EVERY 1 DAY "
                  "STARTS (CURRENT_DATE + INTERVAL 1 DAY + INTERVAL 5 MINUTE) ON COMPLETION PRESERVE ENABLE")
    assert scalar(conn, "SELECT status FROM settlements WHERE settlement_id = %s", (late,)) == "overdue"
    assert scalar(conn, "SELECT status FROM settlements WHERE settlement_id = %s", (done,)) == "completed"
    assert scalar(conn, "SELECT last_executed IS NOT NULL FROM information_schema.events WHERE event_schema = DATABASE()") == 1
