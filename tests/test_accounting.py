"""Accounting rules: exact decimals, rounding, balances, double-counting, and concurrency."""
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import mysql.connector
import pytest

from .helpers import (add_expense, call, category_id, count, expected_shares, net, new_group, new_user,
                      rows, run, scalar, settle)

D = Decimal


def members(conn, n):
    users = [new_user(conn) for _ in range(n)]
    return users, new_group(conn, users[0], *users[1:])


# ------------------------------------------------------------------ the example from the project brief
def test_the_900_peso_example_from_the_brief(conn):
    ana, ben, carla = (new_user(conn, t) for t in ("ana", "ben", "carla"))
    g = new_group(conn, ana, ben, carla)
    add_expense(conn, g, ana, "900.00", [ana, ben, carla])
    assert (net(conn, g, ana), net(conn, g, ben), net(conn, g, carla)) == (D("600.00"), D("-300.00"), D("-300.00"))
    settle(conn, g, ben, ana, "300.00", completed=True)
    assert (net(conn, g, ana), net(conn, g, ben), net(conn, g, carla)) == (D("300.00"), D("0.00"), D("-300.00"))
    assert scalar(conn, "SELECT SUM(net_balance) FROM vw_member_balances WHERE group_id = %s", (g,)) == D("0.00")


# ------------------------------------------------------------------ rounding
@pytest.mark.parametrize("amount,n", [
    ("100.00", 3), ("0.01", 3), ("0.05", 2), ("0.01", 1), ("10.00", 7), ("99.99", 4), ("1000.01", 6),
    ("1.00", 3), ("0.02", 5), ("33.33", 3), ("500.00", 8), ("12345678.91", 7), ("99999999.99", 3),
])
def test_equal_split_is_exact_and_gives_leftover_centavos_to_the_lowest_user_ids(conn, amount, n):
    users, g = members(conn, n)
    eid = add_expense(conn, g, users[0], amount, list(reversed(users)))      # order given must not matter
    got = [r["share_amount"] for r in rows(conn, "SELECT share_amount FROM expense_splits WHERE expense_id = %s ORDER BY user_id", (eid,))]
    assert got == expected_shares(amount, n)
    assert sum(got) == D(amount)
    assert scalar(conn, "SELECT fn_expense_split_total(%s)", (eid,)) == D(amount)


def test_custom_split_with_odd_centavos_is_exact(conn):
    (a, b, c), g = members(conn, 3)
    eid = add_expense(conn, g, a, "100.00", [(a, "33.33"), (b, "33.33"), (c, "33.34")], mode="custom")
    assert scalar(conn, "SELECT SUM(share_amount) FROM expense_splits WHERE expense_id = %s", (eid,)) == D("100.00")


def test_money_arithmetic_has_no_floating_point_error(conn):
    (a, b), g = members(conn, 2)
    for _ in range(10):
        add_expense(conn, g, a, "0.10", [a, b])             # ten times 0.10 must be exactly 1.00, not 0.9999999
    assert scalar(conn, "SELECT total_paid FROM vw_member_balances WHERE group_id = %s AND user_id = %s", (g, a["id"])) == D("1.00")
    assert net(conn, g, a) == D("0.50") and net(conn, g, b) == D("-0.50")


# ------------------------------------------------------------------ status rules for balances
def test_only_completed_settlements_change_balances(conn):
    (a, b, c), g = members(conn, 3)
    add_expense(conn, g, a, "600.00", [a, b, c])              # b owes 200
    from datetime import date, timedelta
    pending = settle(conn, g, b, a, "50.00", due=date.today() + timedelta(days=3))
    cancelled = settle(conn, g, b, a, "40.00")
    call(conn, "sp_cancel_settlement", [cancelled])
    run(conn, "SET TIMESTAMP = UNIX_TIMESTAMP(NOW() - INTERVAL 10 DAY)")
    late = settle(conn, g, b, a, "30.00", due=date.today() - timedelta(days=5), by=b)
    run(conn, "SET TIMESTAMP = DEFAULT")
    call(conn, "sp_mark_overdue_settlements", [])
    statuses = {r["settlement_id"]: r["status"] for r in rows(conn, "SELECT settlement_id, status FROM settlements WHERE group_id = %s", (g,))}
    assert statuses == {pending: "pending", cancelled: "cancelled", late: "overdue"}
    assert net(conn, g, b) == D("-200.00") and net(conn, g, a) == D("400.00")      # unchanged by pending, cancelled, overdue
    call(conn, "sp_complete_settlement", [pending])
    call(conn, "sp_complete_settlement", [late])
    assert net(conn, g, b) == D("-120.00") and net(conn, g, a) == D("320.00")      # 50 + 30 now count, once each


def test_archived_expenses_and_their_shares_are_ignored_consistently(conn):
    (a, b, c), g = members(conn, 3)
    keep = add_expense(conn, g, a, "300.00", [a, b, c])
    gone = add_expense(conn, g, b, "90.00", [a, b, c])
    call(conn, "sp_archive_expense", [gone])
    r = {x["user_id"]: x for x in rows(conn, "SELECT * FROM vw_member_balances WHERE group_id = %s", (g,))}
    assert r[a["id"]]["total_paid"] == D("300.00") and r[b["id"]]["total_paid"] == D("0.00")
    assert all(r[u["id"]]["total_share"] == D("100.00") for u in (a, b, c))
    assert scalar(conn, "SELECT SUM(net_balance) FROM vw_member_balances WHERE group_id = %s", (g,)) == D("0.00")
    s = rows(conn, "SELECT * FROM vw_group_expense_summary WHERE group_id = %s", (g,))[0]
    assert s["active_expense_total"] == D("300.00") and s["archived_expense_count"] == 1
    assert keep and count(conn, "expense_splits", "expense_id = %s", (gone,)) == 3        # archived shares kept as history


# ------------------------------------------------------------------ no double counting
def test_view_equals_an_independent_calculation_after_many_operations(conn):
    users, g = members(conn, 5)
    a, b, c, d, e = users
    add_expense(conn, g, a, "1000.00", users)
    add_expense(conn, g, b, "333.33", [a, b, c])
    add_expense(conn, g, c, "250.75", [(a, "50.25"), (c, "100.00"), (e, "100.50")], mode="custom")
    add_expense(conn, g, d, "77.77", [b, d, e, a])
    gone = add_expense(conn, g, e, "500.00", users)
    call(conn, "sp_archive_expense", [gone])
    settle(conn, g, c, a, "100.00", completed=True)
    settle(conn, g, e, a, "40.00", completed=True)     # (b is only owed 2.78, so pay a)
    settle(conn, g, d, a, "10.00")                                  # pending: must not count

    # independent calculation straight from the raw tables, in Python
    expected = defaultdict(lambda: D("0.00"))
    for r in rows(conn, "SELECT paid_by, amount FROM expenses WHERE group_id = %s AND status = 'active'", (g,)):
        expected[r["paid_by"]] += r["amount"]
    for r in rows(conn, "SELECT s.user_id, s.share_amount FROM expense_splits s JOIN expenses e ON e.expense_id = s.expense_id "
                        "WHERE s.group_id = %s AND e.status = 'active'", (g,)):
        expected[r["user_id"]] -= r["share_amount"]
    for r in rows(conn, "SELECT payer_id, receiver_id, amount FROM settlements WHERE group_id = %s AND status = 'completed'", (g,)):
        expected[r["payer_id"]] += r["amount"]
        expected[r["receiver_id"]] -= r["amount"]
    for r in rows(conn, "SELECT user_id, net_balance FROM vw_member_balances WHERE group_id = %s", (g,)):
        assert r["net_balance"] == expected[r["user_id"]], r
    assert sum(expected.values()) == D("0.00")


def test_balances_of_different_groups_never_mix(conn):
    (a, b), g1 = members(conn, 2)
    g2 = new_group(conn, a, b)
    add_expense(conn, g1, a, "100.00", [a, b])
    add_expense(conn, g2, b, "40.00", [a, b])
    assert net(conn, g1, a) == D("50.00") and net(conn, g2, a) == D("-20.00")


# ------------------------------------------------------------------ concurrency
def _parallel(db_cfg, jobs, workers):
    """Run `jobs` (callables taking a connection) at the same instant. Returns (successes, errors)."""
    barrier = threading.Barrier(workers)

    def worker(job):
        c = mysql.connector.connect(autocommit=True, **db_cfg)
        try:
            barrier.wait(timeout=10)
            try:
                job(c)
                return None
            except mysql.connector.Error as err:
                return err.msg
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(worker, jobs))
    return [r for r in results if r is None], [r for r in results if r is not None]


def test_simultaneous_completed_payments_cannot_overpay(conn, db_cfg):
    (fay, gus), g = members(conn, 2)
    add_expense(conn, g, fay, "400.00", [fay, gus])                    # gus owes 200
    jobs = [lambda c: settle(c, g, gus, fay, "150.00", completed=True) for _ in range(8)]
    ok, errors = _parallel(db_cfg, jobs, 8)
    assert len(ok) == 1, f"{len(ok)} payments of 150 were accepted for a debt of 200"
    assert all("more than the payer still owes" in e for e in errors), errors
    assert net(conn, g, gus) == D("-50.00") and net(conn, g, fay) == D("50.00")


def test_simultaneous_pending_payments_cannot_reserve_more_than_the_debt(conn, db_cfg):
    (fay, gus), g = members(conn, 2)
    add_expense(conn, g, fay, "400.00", [fay, gus])
    jobs = [lambda c: settle(c, g, gus, fay, "150.00") for _ in range(8)]
    ok, errors = _parallel(db_cfg, jobs, 8)
    assert len(ok) == 1
    assert scalar(conn, "SELECT SUM(amount) FROM settlements WHERE group_id = %s AND status = 'pending'", (g,)) == D("150.00")


def test_a_settlement_can_only_be_completed_once_even_when_requested_simultaneously(conn, db_cfg):
    (fay, gus), g = members(conn, 2)
    add_expense(conn, g, fay, "400.00", [fay, gus])
    sid = settle(conn, g, gus, fay, "100.00")
    jobs = [lambda c: call(c, "sp_complete_settlement", [sid]) for _ in range(6)]
    ok, errors = _parallel(db_cfg, jobs, 6)
    assert len(ok) == 1
    assert all("already completed" in e for e in errors), errors
    assert net(conn, g, gus) == D("-100.00")                           # counted exactly once


def test_simultaneous_expenses_in_one_group_are_all_saved_and_balanced(conn, db_cfg):
    users, g = members(conn, 3)
    jobs = [lambda c, i=i: add_expense(c, g, users[i % 3], f"{10 + i}.01", users) for i in range(8)]
    ok, errors = _parallel(db_cfg, jobs, 8)
    assert len(ok) == 8, errors
    assert count(conn, "vw_expense_split_check", "group_id = %s AND is_balanced = 1", (g,)) == 8
    assert scalar(conn, "SELECT SUM(net_balance) FROM vw_member_balances WHERE group_id = %s", (g,)) == D("0.00")


def test_a_failed_transaction_in_one_session_does_not_block_or_corrupt_others(conn, db_cfg):
    (a, b), g = members(conn, 2)
    add_expense(conn, g, a, "100.00", [a, b])
    before = (count(conn, "expenses", "group_id = %s", (g,)), count(conn, "expense_splits", "group_id = %s", (g,)))
    jobs = [lambda c: add_expense(c, g, a, "50.00", [a, b])] * 2 + \
           [lambda c: call(c, "sp_add_expense", [g, a["id"], category_id(c), "x", None, D("10"), None, "custom", "[]", a["id"], 0])] * 2
    ok, errors = _parallel(db_cfg, jobs, 4)
    assert len(ok) == 2 and len(errors) == 2
    assert count(conn, "expenses", "group_id = %s", (g,)) == before[0] + 2
    assert count(conn, "expenses", "group_id = %s AND status = 'draft'", (g,)) == 0


# ------------------------------------------------------------------ stale snapshots (long transactions)
def test_a_long_transaction_with_an_old_snapshot_cannot_bypass_the_overpayment_guard(conn, db_cfg):
    """Under MySQL's default isolation a transaction keeps seeing the data as it was when it first read.
    The settlement guard must still use the LATEST committed data."""
    (fay, gus), g = members(conn, 2)
    add_expense(conn, g, fay, "400.00", [fay, gus])                      # gus owes 200
    old = mysql.connector.connect(autocommit=False, **db_cfg)
    try:
        cur = old.cursor()
        cur.execute("SELECT net_balance FROM vw_member_balances WHERE group_id = %s AND user_id = %s", (g, gus["id"]))
        assert cur.fetchone()[0] == D("-200.00")                          # this transaction now holds an old snapshot
        settle(conn, g, gus, fay, "150.00", completed=True)               # another session pays 150 and commits
        cur.execute("SELECT net_balance FROM vw_member_balances WHERE group_id = %s AND user_id = %s", (g, gus["id"]))
        assert cur.fetchone()[0] == D("-200.00")                          # the old snapshot still shows the stale number
        with pytest.raises(mysql.connector.Error) as info:
            cur.execute("INSERT INTO settlements (group_id, payer_id, receiver_id, created_by, amount, settlement_date, status) "
                        "VALUES (%s, %s, %s, %s, 150.00, CURDATE(), 'completed')", (g, gus["id"], fay["id"], fay["id"]))
        assert "more than the payer still owes" in info.value.msg
        old.rollback()
    finally:
        old.close()
    assert net(conn, g, gus) == D("-50.00")                               # the stale request did not overpay


def test_a_long_transaction_with_an_old_snapshot_cannot_pay_someone_who_has_since_left(conn, db_cfg):
    a, b, c = new_user(conn), new_user(conn), new_user(conn)
    g = new_group(conn, a, b, c)
    add_expense(conn, g, a, "200.00", [a, b])                             # c has no balance and may leave
    old = mysql.connector.connect(autocommit=False, **db_cfg)
    try:
        cur = old.cursor()
        cur.execute("SELECT COUNT(*) FROM group_members WHERE group_id = %s AND status = 'active'", (g,))
        assert cur.fetchone()[0] == 3                                     # old snapshot: c is still active
        run(conn, "UPDATE group_members SET status = 'left' WHERE group_id = %s AND user_id = %s", (g, c["id"]))
        with pytest.raises(mysql.connector.Error) as info:
            cur.execute("INSERT INTO settlements (group_id, payer_id, receiver_id, created_by, amount, settlement_date, due_date, status) "
                        "VALUES (%s, %s, %s, %s, 5.00, CURDATE(), CURDATE() + INTERVAL 3 DAY, 'pending')", (g, b["id"], c["id"], a["id"]))
        assert "not an active member" in info.value.msg
        old.rollback()
    finally:
        old.close()
    assert count(conn, "settlements", "group_id = %s", (g,)) == 0
