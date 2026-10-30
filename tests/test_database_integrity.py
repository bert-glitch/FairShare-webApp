"""Database integrity: tables, keys, composite foreign keys, constraints, deletion protection."""
import mysql.connector
import pytest

from .helpers import add_expense, call, category_id, count, new_group, new_user, rows, run, scalar, settle

EXPECTED_TABLES = {"users", "expense_groups", "group_members", "expense_categories",
                   "expenses", "expense_splits", "settlements"}


def fails(conn, sql, params=(), errno=None, text=None):
    """Run SQL that must be rejected. Optionally check the MySQL error number and message."""
    with pytest.raises(mysql.connector.Error) as info:
        run(conn, sql, params)
    if errno is not None:
        assert info.value.errno == errno, f"expected {errno}, got {info.value.errno}: {info.value.msg}"
    if text is not None:
        assert text.lower() in info.value.msg.lower(), info.value.msg
    return info.value


# ------------------------------------------------------------------ structure
def test_exactly_seven_core_tables_exist(conn):
    tables = {r["table_name"].lower() for r in rows(conn,
              "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'")}
    assert tables == EXPECTED_TABLES


def test_primary_keys(conn):
    expected = {"users": ["user_id"], "expense_groups": ["group_id"], "group_members": ["group_member_id"],
                "expense_categories": ["category_id"], "expenses": ["expense_id"],
                "expense_splits": ["expense_split_id"], "settlements": ["settlement_id"]}
    for table, cols in expected.items():
        got = [r["column_name"] for r in rows(conn,
               "SELECT column_name FROM information_schema.key_column_usage WHERE table_schema = DATABASE() "
               "AND table_name = %s AND constraint_name = 'PRIMARY' ORDER BY ordinal_position", (table,))]
        assert got == cols, table


def test_foreign_keys_including_the_five_composite_ones(conn):
    expected = {
        "fk_groups_creator": ("expense_groups", ["created_by"]),
        "fk_gm_group": ("group_members", ["group_id"]),
        "fk_gm_user": ("group_members", ["user_id"]),
        "fk_exp_payer": ("expenses", ["group_id", "paid_by"]),                  # composite
        "fk_exp_category": ("expenses", ["category_id"]),
        "fk_exp_creator": ("expenses", ["created_by"]),
        "fk_split_expense": ("expense_splits", ["expense_id", "group_id"]),    # composite
        "fk_split_member": ("expense_splits", ["group_id", "user_id"]),        # composite
        "fk_stl_payer": ("settlements", ["group_id", "payer_id"]),             # composite
        "fk_stl_receiver": ("settlements", ["group_id", "receiver_id"]),       # composite
        "fk_stl_creator": ("settlements", ["created_by"]),
    }
    for name, (table, cols) in expected.items():
        got = [r["column_name"] for r in rows(conn,
               "SELECT column_name FROM information_schema.key_column_usage WHERE table_schema = DATABASE() "
               "AND constraint_name = %s ORDER BY ordinal_position", (name,))]
        assert got == cols, name
    rules = rows(conn, "SELECT constraint_name, delete_rule, update_rule FROM information_schema.referential_constraints "
                       "WHERE constraint_schema = DATABASE()")
    assert len(rules) == len(expected)
    assert all(r["delete_rule"] == "RESTRICT" and r["update_rule"] == "RESTRICT" for r in rules)


def test_unique_keys_and_indexes_exist(conn):
    idx = {(r["table_name"], r["index_name"]): r["non_unique"] for r in rows(conn,
           "SELECT DISTINCT table_name, index_name, non_unique FROM information_schema.statistics WHERE table_schema = DATABASE()")}
    for key in [("users", "uq_users_username"), ("users", "uq_users_email"), ("users", "uq_users_single_admin"),
                ("group_members", "uq_group_members_pair"), ("expense_categories", "uq_category_name"),
                ("expenses", "uq_expenses_id_group"), ("expense_splits", "uq_split_expense_user")]:
        assert idx.get(key) == 0, f"missing unique key {key}"
    for key in [("expenses", "idx_exp_group_status_date"), ("settlements", "idx_stl_group_status"),
                ("settlements", "idx_stl_status_due")]:
        assert key in idx, f"missing index {key}"


def test_check_constraints_exist(conn):
    names = {r["constraint_name"] for r in rows(conn,
             "SELECT constraint_name FROM information_schema.check_constraints WHERE constraint_schema = DATABASE()")}
    assert {"ck_users_role", "ck_users_username", "ck_exp_amount", "ck_exp_status", "ck_split_share",
            "ck_stl_status", "ck_stl_due", "ck_gm_status", "ck_groups_status"} <= names


def test_money_columns_are_exact_decimals(conn):
    for table, col in [("expenses", "amount"), ("expense_splits", "share_amount"), ("settlements", "amount")]:
        t = rows(conn, "SELECT column_type FROM information_schema.columns WHERE table_schema = DATABASE() "
                       "AND table_name = %s AND column_name = %s", (table, col))[0]["column_type"]
        assert t == "decimal(10,2)", f"{table}.{col} is {t}"


# ------------------------------------------------------------------ composite foreign keys
def test_composite_fk_expense_splits_rejects_a_group_that_does_not_match_the_expense(conn):
    a, b, c = new_user(conn), new_user(conn), new_user(conn)
    g1, g2 = new_group(conn, a, b), new_group(conn, c)
    _, _ = run(conn, "INSERT INTO expenses (group_id, paid_by, category_id, created_by, expense_title, amount, expense_date) "
                     "VALUES (%s, %s, %s, %s, 'draft', 10.00, CURDATE())", (g1, a["id"], category_id(conn), a["id"]))
    eid = scalar(conn, "SELECT MAX(expense_id) FROM expenses WHERE group_id = %s", (g1,))
    # the share claims group g2 for an expense that belongs to g1: the triggers pass, the composite FK must stop it
    fails(conn, "INSERT INTO expense_splits (expense_id, group_id, user_id, share_amount) VALUES (%s, %s, %s, 10.00)",
          (eid, g2, c["id"]), errno=1452)


def test_composite_fk_rejects_a_payer_from_another_group(conn):
    a, outsider = new_user(conn), new_user(conn)
    g = new_group(conn, a)
    fails(conn, "INSERT INTO expenses (group_id, paid_by, category_id, created_by, expense_title, amount, expense_date) "
                "VALUES (%s, %s, %s, %s, 'x', 10.00, CURDATE())", (g, outsider["id"], category_id(conn), a["id"]))
    assert count(conn, "expenses", "group_id = %s", (g,)) == 0


def test_settlement_between_members_of_different_groups_is_rejected(conn):
    a, b, c = new_user(conn), new_user(conn), new_user(conn)
    g1, g2 = new_group(conn, a, b), new_group(conn, c)
    add_expense(conn, g1, a, "100.00", [a, b])
    fails(conn, "INSERT INTO settlements (group_id, payer_id, receiver_id, created_by, amount, settlement_date, status) "
                "VALUES (%s, %s, %s, %s, 10.00, CURDATE(), 'completed')", (g1, c["id"], a["id"], a["id"]))
    fails(conn, "INSERT INTO settlements (group_id, payer_id, receiver_id, created_by, amount, settlement_date, status) "
                "VALUES (%s, %s, %s, %s, 10.00, CURDATE(), 'completed')", (g1, b["id"], c["id"], a["id"]))


def test_duplicate_membership_is_rejected(conn):
    a, b = new_user(conn), new_user(conn)
    g = new_group(conn, a, b)
    fails(conn, "INSERT INTO group_members (group_id, user_id) VALUES (%s, %s)", (g, b["id"]), errno=1062)


def test_duplicate_split_for_same_user_and_expense_is_rejected(conn):
    a, b = new_user(conn), new_user(conn)
    g = new_group(conn, a, b)
    run(conn, "INSERT INTO expenses (group_id, paid_by, category_id, created_by, expense_title, amount, expense_date) "
              "VALUES (%s, %s, %s, %s, 'd', 10.00, CURDATE())", (g, a["id"], category_id(conn), a["id"]))
    eid = scalar(conn, "SELECT MAX(expense_id) FROM expenses WHERE group_id = %s", (g,))
    run(conn, "INSERT INTO expense_splits (expense_id, group_id, user_id, share_amount) VALUES (%s, %s, %s, 5.00)", (eid, g, a["id"]))
    fails(conn, "INSERT INTO expense_splits (expense_id, group_id, user_id, share_amount) VALUES (%s, %s, %s, 5.00)",
          (eid, g, a["id"]))


# ------------------------------------------------------------------ shares must add up exactly
def _draft(conn, g, payer, amount="120.00"):
    run(conn, "INSERT INTO expenses (group_id, paid_by, category_id, created_by, expense_title, amount, expense_date) "
              "VALUES (%s, %s, %s, %s, 'manual', %s, CURDATE())", (g, payer["id"], category_id(conn), payer["id"], amount))
    return scalar(conn, "SELECT MAX(expense_id) FROM expenses WHERE group_id = %s", (g,))


def test_direct_insert_as_active_is_rejected(conn):
    a = new_user(conn)
    g = new_group(conn, a)
    fails(conn, "INSERT INTO expenses (group_id, paid_by, category_id, created_by, expense_title, amount, expense_date, status) "
                "VALUES (%s, %s, %s, %s, 'sneaky', 10.00, CURDATE(), 'active')", (g, a["id"], category_id(conn), a["id"]),
          text="must start as a draft")


def test_draft_cannot_be_activated_without_shares_or_with_wrong_total(conn):
    a, b, c = new_user(conn), new_user(conn), new_user(conn)
    g = new_group(conn, a, b, c)
    eid = _draft(conn, g, a, "120.00")
    fails(conn, "UPDATE expenses SET status = 'active' WHERE expense_id = %s", (eid,), text="at least one share")
    run(conn, "INSERT INTO expense_splits (expense_id, group_id, user_id, share_amount) VALUES (%s, %s, %s, 50.00), (%s, %s, %s, 50.00)",
        (eid, g, a["id"], eid, g, b["id"]))
    fails(conn, "UPDATE expenses SET status = 'active' WHERE expense_id = %s", (eid,), text="add up to 100.00")
    run(conn, "INSERT INTO expense_splits (expense_id, group_id, user_id, share_amount) VALUES (%s, %s, %s, 20.00)", (eid, g, c["id"]))
    run(conn, "UPDATE expenses SET status = 'active' WHERE expense_id = %s", (eid,))
    assert scalar(conn, "SELECT status FROM expenses WHERE expense_id = %s", (eid,)) == "active"


def test_shares_of_an_active_expense_can_never_change(conn):
    a, b = new_user(conn), new_user(conn)
    g = new_group(conn, a, b)
    eid = add_expense(conn, g, a, "100.00", [a, b])
    fails(conn, "INSERT INTO expense_splits (expense_id, group_id, user_id, share_amount) VALUES (%s, %s, %s, 1.00)",
          (eid, g, a["id"]), text="draft")
    fails(conn, "UPDATE expense_splits SET share_amount = 1.00 WHERE expense_id = %s", (eid,), text="cannot be changed")
    fails(conn, "DELETE FROM expense_splits WHERE expense_id = %s", (eid,), text="cannot be deleted")
    fails(conn, "UPDATE expenses SET amount = 1.00 WHERE expense_id = %s", (eid,), text="Only the status")


def test_every_active_expense_is_balanced(conn):
    a, b, c = new_user(conn), new_user(conn), new_user(conn)
    g = new_group(conn, a, b, c)
    add_expense(conn, g, a, "100.00", [a, b, c])
    add_expense(conn, g, b, "0.01", [a, b, c])
    add_expense(conn, g, c, "250.50", [(a, "100.00"), (b, "100.50"), (c, "50.00")], mode="custom")
    assert count(conn, "vw_expense_split_check", "expense_status = 'active' AND is_balanced = 0") == 0


# ------------------------------------------------------------------ CHECK constraints
def test_check_constraints_reject_bad_values(conn):
    a = new_user(conn)
    g = new_group(conn, a)
    fails(conn, "INSERT INTO expenses (group_id, paid_by, category_id, created_by, expense_title, amount, expense_date) "
                "VALUES (%s, %s, %s, %s, 'x', 0, CURDATE())", (g, a["id"], category_id(conn), a["id"]), errno=3819)
    fails(conn, "INSERT INTO expenses (group_id, paid_by, category_id, created_by, expense_title, amount, expense_date) "
                "VALUES (%s, %s, %s, %s, 'x', -5, CURDATE())", (g, a["id"], category_id(conn), a["id"]), errno=3819)
    fails(conn, "INSERT INTO users (full_name, username, password_hash) VALUES ('x', 'a b', 'h')", errno=3819)
    fails(conn, "INSERT INTO users (full_name, username, email, password_hash) VALUES ('x', 'okname1', 'not-an-email', 'h')", errno=3819)
    fails(conn, "INSERT INTO users (full_name, username, password_hash, role) VALUES ('x', 'okname2', 'h', 'boss')", errno=3819)


# ------------------------------------------------------------------ nothing financial can be deleted
def test_financial_history_cannot_be_physically_deleted(conn):
    a, b = new_user(conn), new_user(conn)
    g = new_group(conn, a, b)
    eid = add_expense(conn, g, a, "100.00", [a, b])
    sid = settle(conn, g, b, a, "20.00", completed=True)
    fails(conn, "DELETE FROM expenses WHERE expense_id = %s", (eid,), text="cannot be deleted")
    fails(conn, "DELETE FROM expense_splits WHERE expense_id = %s", (eid,), text="cannot be deleted")
    fails(conn, "DELETE FROM settlements WHERE settlement_id = %s", (sid,), text="cannot be deleted")
    fails(conn, "DELETE FROM users WHERE user_id = %s", (a["id"],), text="cannot be deleted")
    fails(conn, "DELETE FROM expense_groups WHERE group_id = %s", (g,), errno=1451)       # members still reference it
    fails(conn, "DELETE FROM group_members WHERE group_id = %s AND user_id = %s", (g, a["id"]), errno=1451)
    assert count(conn, "expenses", "expense_id = %s", (eid,)) == 1
    assert count(conn, "settlements", "settlement_id = %s", (sid,)) == 1


def test_expenses_are_archived_not_deleted(conn):
    a, b = new_user(conn), new_user(conn)
    g = new_group(conn, a, b)
    eid = add_expense(conn, g, a, "100.00", [a, b])
    call(conn, "sp_archive_expense", [eid])
    assert scalar(conn, "SELECT status FROM expenses WHERE expense_id = %s", (eid,)) == "archived"
    assert count(conn, "expense_splits", "expense_id = %s", (eid,)) == 2          # history is preserved


# ------------------------------------------------------------------ exactly one administrator
def test_the_database_allows_only_one_admin(conn):
    assert count(conn, "users", "role = 'admin'") == 1
    fails(conn, "INSERT INTO users (full_name, username, password_hash, role) VALUES ('Evil', 'evil_admin', 'h', 'admin')",
          errno=1062, text="uq_users_single_admin")
    assert count(conn, "users", "role = 'admin'") == 1


def test_roles_cannot_change_and_the_admin_cannot_be_deactivated_or_deleted(conn):
    user = new_user(conn)
    admin_id = scalar(conn, "SELECT user_id FROM users WHERE role = 'admin'")
    fails(conn, "UPDATE users SET role = 'admin' WHERE user_id = %s", (user["id"],), text="role of an account cannot be changed")
    fails(conn, "UPDATE users SET role = 'user' WHERE user_id = %s", (admin_id,), text="role of an account cannot be changed")
    fails(conn, "UPDATE users SET is_active = 0 WHERE user_id = %s", (admin_id,), text="cannot be deactivated")
    fails(conn, "DELETE FROM users WHERE user_id = %s", (admin_id,), text="cannot be deleted")
    assert scalar(conn, "SELECT role FROM users WHERE user_id = %s", (user["id"],)) == "user"
