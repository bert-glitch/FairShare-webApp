"""End-to-end workflows through the web pages, checked against the real database."""
from decimal import Decimal

import pytest

from .conftest import ADMIN_PASSWORD, ADMIN_USERNAME
from .helpers import (add_expense, call, category_id, client_for, count, login, net, new_group, new_user, post,
                      rows, run, scalar, settle, text, uniq)

D = Decimal


@pytest.fixture()
def team(app, conn):
    alice, bob, carol = new_user(conn, "alice"), new_user(conn, "bob"), new_user(conn, "carol")
    g = new_group(conn, alice, bob, carol)                 # alice created it, so alice is the manager
    return {"app": app, "conn": conn, "g": g, "alice": alice, "bob": bob, "carol": carol,
            "ca": client_for(app, alice), "cb": client_for(app, bob), "cc": client_for(app, carol)}


def expense_form(team, **over):
    form = {"expense_title": "Dinner", "amount": "300.00", "expense_date": "2024-05-01", "category_id": str(category_id(team["conn"])),
            "paid_by": str(team["alice"]["id"]), "split_mode": "equal", "description": "",
            "participants": [str(team[k]["id"]) for k in ("alice", "bob", "carol")]}
    form.update(over)
    return form


def page_has(response, *needles):
    body = text(response)
    for n in needles:
        assert n in body, f"{n!r} not found in page"


# ================================================================= groups and members
def test_create_group_and_add_members_through_the_ui(app, conn):
    owner, friend = new_user(conn), new_user(conn)
    c = client_for(app, owner)
    name = uniq("Trip ")
    r = post(c, "/groups/new", {"group_name": name, "description": "Holiday costs"})
    page_has(r, "Group created", name)
    gid = scalar(conn, "SELECT group_id FROM expense_groups WHERE group_name = %s", (name,))
    assert scalar(conn, "SELECT status FROM group_members WHERE group_id = %s AND user_id = %s", (gid, owner["id"])) == "active"
    page_has(post(c, f"/groups/{gid}/members/add", {"username": friend["username"]}), "Member added", friend["username"])
    page_has(post(c, f"/groups/{gid}/members/add", {"username": friend["username"]}), "already in this group")
    page_has(post(c, f"/groups/{gid}/members/add", {"username": "nobody_here_1"}), "No active user has that username")
    assert count(conn, "group_members", "group_id = %s", (gid,)) == 2


def test_group_name_validation(app, conn):
    c = client_for(app, new_user(conn))
    before = count(conn, "expense_groups")
    page_has(post(c, "/groups/new", {"group_name": "   "}), "Group name is required")
    page_has(post(c, "/groups/new", {"group_name": "x" * 101}), "at most 100 characters")
    assert count(conn, "expense_groups") == before


# ================================================================= expenses
def test_add_equal_expense_then_see_history_details_and_balances(team):
    t = team
    r = post(t["ca"], f"/groups/{t['g']}/expenses/new", expense_form(t))
    page_has(r, "Expense saved and split", "Dinner", "₱100.00", "(balanced)")
    page_has(t["ca"].get(f"/groups/{t['g']}/expenses"), "Dinner", "₱300.00")
    bal = text(t["ca"].get(f"/groups/{t['g']}/balances"))
    assert "₱200.00" in bal and "-₱100.00" in bal and "(must be 0.00)" in bal and "OK" in bal
    assert net(t["conn"], t["g"], t["alice"]) == D("200.00")
    page_has(t["cb"].get("/dashboard"), "₱100.00")            # bob sees what he owes


def test_expense_with_leftover_centavos_through_the_ui(team):
    t = team
    r = post(t["ca"], f"/groups/{t['g']}/expenses/new", expense_form(t, amount="100.00"))
    page_has(r, "₱33.34", "₱33.33", "(balanced)")


def test_custom_split_through_the_ui(team):
    t = team
    form = expense_form(t, amount="100.00", split_mode="custom")
    form.pop("participants")
    form.update({f"share_{t['alice']['id']}": "60.00", f"share_{t['bob']['id']}": "40.00"})
    page_has(post(t["ca"], f"/groups/{t['g']}/expenses/new", form), "Expense saved", "₱60.00", "₱40.00")
    form[f"share_{t['bob']['id']}"] = "30.00"                 # now only 90.00 of 100.00
    before = count(t["conn"], "expenses", "group_id = %s", (t["g"],))
    r = post(t["ca"], f"/groups/{t['g']}/expenses/new", form)
    page_has(r, "add up to 90.00")
    assert count(t["conn"], "expenses", "group_id = %s", (t["g"],)) == before
    assert count(t["conn"], "expenses", "group_id = %s AND status = 'draft'", (t["g"],)) == 0


@pytest.mark.parametrize("over,message", [
    ({"amount": "-5"}, "greater than zero"), ({"amount": "0"}, "greater than zero"),
    ({"amount": "abc"}, "must be a number"), ({"amount": "10.005"}, "at most 2 decimal places"),
    ({"amount": ""}, "Amount is required"), ({"amount": "999999999"}, "too large"),
    ({"expense_title": "  "}, "Title is required"), ({"expense_date": "2999-01-01"}, "cannot be in the future"),
    ({"expense_date": "not-a-date"}, "valid date"), ({"split_mode": "halves"}, "Choose how to split"),
    ({"participants": []}, "at least one member"), ({"category_id": "999999"}, "valid, active category"),
    ({"category_id": "abc"}, "Category is not valid"),
])
def test_invalid_expense_forms_show_clear_messages_and_save_nothing(team, over, message):
    t = team
    before = (count(t["conn"], "expenses"), count(t["conn"], "expense_splits"))
    r = post(t["ca"], f"/groups/{t['g']}/expenses/new", expense_form(t, **over))
    page_has(r, message)
    assert (count(t["conn"], "expenses"), count(t["conn"], "expense_splits")) == before


def test_payer_and_participants_must_belong_to_the_group(team):
    t = team
    outsider = new_user(t["conn"])
    page_has(post(t["ca"], f"/groups/{t['g']}/expenses/new", expense_form(t, paid_by=str(outsider["id"]))), "not an active member")
    page_has(post(t["ca"], f"/groups/{t['g']}/expenses/new", expense_form(t, participants=[str(t["alice"]["id"]), str(outsider["id"])])),
             "active member")


def test_who_may_archive_an_expense(team):
    t = team
    by_alice = add_expense(t["conn"], t["g"], t["alice"], "300.00", [t["alice"], t["bob"], t["carol"]])
    by_bob = add_expense(t["conn"], t["g"], t["bob"], "60.00", [t["alice"], t["bob"]], created_by=t["bob"])
    assert post(t["cb"], f"/expenses/{by_alice}/archive").status_code == 403         # bob did not record it and is no manager
    assert scalar(t["conn"], "SELECT status FROM expenses WHERE expense_id = %s", (by_alice,)) == "active"
    page_has(post(t["cb"], f"/expenses/{by_bob}/archive"), "was archived")           # his own: allowed
    page_has(post(t["ca"], f"/expenses/{by_alice}/archive"), "was archived")         # the manager: allowed
    assert net(t["conn"], t["g"], t["alice"]) == D("0.00")
    page_has(t["ca"].get(f"/groups/{t['g']}/expenses?status=archived"), "Test expense", "archived")
    page_has(post(t["ca"], f"/expenses/{by_alice}/archive"), "already archived")


def test_dashboard_totals_match_the_database(team):
    t = team
    add_expense(t["conn"], t["g"], t["alice"], "300.00", [t["alice"], t["bob"], t["carol"]])
    add_expense(t["conn"], t["g"], t["bob"], "50.00", [t["alice"], t["bob"]])
    for key in ("alice", "bob", "carol"):
        row = rows(t["conn"], "SELECT SUM(amount_owed_by_member) AS owes, SUM(amount_owed_to_member) AS owed "
                              "FROM vw_member_balances WHERE user_id = %s AND membership_status = 'active'", (t[key]["id"],))[0]
        body = text(t["c" + key[0]].get("/dashboard"))
        for value in (row["owes"], row["owed"]):
            assert f"₱{value:,.2f}" in body, (key, value)


# ================================================================= settlements
def test_settlement_workflow_through_the_ui(team):
    t = team
    add_expense(t["conn"], t["g"], t["alice"], "900.00", [t["alice"], t["bob"], t["carol"]])   # bob and carol owe 300 each
    base = f"/groups/{t['g']}/settlements/new"
    form = lambda payer, receiver, amount, **x: dict({"payer_id": str(payer["id"]), "receiver_id": str(receiver["id"]), "amount": amount,
                                                      "settlement_date": "2024-05-02", "due_date": ""}, **x)
    # bob records that he will pay alice: pending, balances do not move
    page_has(post(t["cb"], base, form(t["bob"], t["alice"], "100.00")), "recorded as pending")
    sid = scalar(t["conn"], "SELECT MAX(settlement_id) FROM settlements WHERE group_id = %s", (t["g"],))
    assert scalar(t["conn"], "SELECT status FROM settlements WHERE settlement_id = %s", (sid,)) == "pending"
    assert net(t["conn"], t["g"], t["bob"]) == D("-300.00")
    # the payer cannot mark his own payment as already completed
    before = count(t["conn"], "settlements", "group_id = %s", (t["g"],))
    page_has(post(t["cb"], base, form(t["bob"], t["alice"], "10.00", mark_completed="1")), "Only the person who receives")
    # a member who is neither payer nor receiver cannot record it
    page_has(post(t["cc"], base, form(t["bob"], t["alice"], "10.00")), "payments that you make or receive")
    assert count(t["conn"], "settlements", "group_id = %s", (t["g"],)) == before
    # bob cannot confirm his own payment; alice (the receiver) can
    assert post(t["cb"], f"/settlements/{sid}/complete").status_code == 403
    page_has(post(t["ca"], f"/settlements/{sid}/complete"), "now counts toward balances")
    assert net(t["conn"], t["g"], t["bob"]) == D("-200.00") and net(t["conn"], t["g"], t["alice"]) == D("500.00")
    page_has(post(t["ca"], f"/settlements/{sid}/complete"), "already completed")                      # exactly once
    assert net(t["conn"], t["g"], t["bob"]) == D("-200.00")
    # more than the debt is refused with a clear message
    page_has(post(t["cb"], base, form(t["bob"], t["alice"], "250.00")), "more than the payer still owes")
    # completed payments cannot be cancelled; pending ones can
    page_has(post(t["ca"], f"/settlements/{sid}/cancel"), "completed settlement cannot be cancelled")
    page_has(post(t["cc"], base, form(t["carol"], t["alice"], "75.00")), "recorded as pending")
    sid2 = scalar(t["conn"], "SELECT MAX(settlement_id) FROM settlements WHERE group_id = %s", (t["g"],))
    page_has(post(t["cc"], f"/settlements/{sid2}/cancel"), "was cancelled")
    assert count(t["conn"], "settlements", "settlement_id = %s", (sid2,)) == 1                         # record kept
    # the receiver can record a payment as already completed in one step
    page_has(post(t["ca"], base, form(t["carol"], t["alice"], "50.00", mark_completed="1")), "recorded as completed")
    assert net(t["conn"], t["g"], t["carol"]) == D("-250.00")
    history = text(t["ca"].get(f"/groups/{t['g']}/settlements/history"))
    for status in ("completed", "cancelled", "pending"):
        assert status in history
    page_has(t["ca"].get(f"/groups/{t['g']}/settlements/history?status=cancelled"), "cancelled")
    assert "₱75.00" in text(t["ca"].get(f"/groups/{t['g']}/settlements/history?status=cancelled"))


@pytest.mark.parametrize("over,message", [
    ({"amount": "0"}, "greater than zero"), ({"amount": "-3"}, "greater than zero"), ({"amount": "x"}, "must be a number"),
    ({"amount": "1.234"}, "at most 2 decimal places"), ({"settlement_date": "nope"}, "valid date"),
    ({"due_date": "also-nope"}, "valid date"),
])
def test_invalid_settlement_forms(team, over, message):
    t = team
    add_expense(t["conn"], t["g"], t["alice"], "900.00", [t["alice"], t["bob"], t["carol"]])
    form = {"payer_id": str(t["bob"]["id"]), "receiver_id": str(t["alice"]["id"]), "amount": "10.00", "settlement_date": "2024-05-02", "due_date": ""}
    form.update(over)
    before = count(t["conn"], "settlements")
    page_has(post(t["cb"], f"/groups/{t['g']}/settlements/new", form), message)
    assert count(t["conn"], "settlements") == before


def test_paying_yourself_is_refused(team):
    t = team
    r = post(t["cb"], f"/groups/{t['g']}/settlements/new", {"payer_id": str(t["bob"]["id"]), "receiver_id": str(t["bob"]["id"]),
             "amount": "5.00", "settlement_date": "2024-05-02"})
    page_has(r, "cannot pay themselves")


def test_member_leave_and_group_archive_rules_through_the_ui(team):
    t = team
    add_expense(t["conn"], t["g"], t["alice"], "300.00", [t["alice"], t["bob"], t["carol"]])
    page_has(post(t["cb"], f"/groups/{t['g']}/members/{t['bob']['id']}/remove"), "still has a balance")
    page_has(post(t["ca"], f"/groups/{t['g']}/archive"), "settled up")
    settle(t["conn"], t["g"], t["bob"], t["alice"], "100.00", completed=True)
    settle(t["conn"], t["g"], t["carol"], t["alice"], "100.00", completed=True)
    post(t["cb"], f"/groups/{t['g']}/members/{t['bob']['id']}/remove")
    assert scalar(t["conn"], "SELECT status FROM group_members WHERE group_id = %s AND user_id = %s", (t["g"], t["bob"]["id"])) == "left"
    assert f"/groups/{t['g']}" not in text(t["cb"].get("/groups"))                   # bob no longer sees the group
    assert t["cb"].get(f"/groups/{t['g']}").status_code == 403
    # alice paid 300 and has a 100 share (net +200); bob and carol paid her 100 each, so she is settled
    assert net(t["conn"], t["g"], t["alice"]) == D("0.00")
    page_has(post(t["ca"], f"/groups/{t['g']}/archive"), "was archived")
    page_has(post(t["ca"], f"/groups/{t['g']}/expenses/new", expense_form(t, participants=[str(t["alice"]["id"])])), "archived")
    assert post(t["ca"], f"/groups/{t['g']}/reactivate").status_code == 403         # only the admin can reactivate


# ================================================================= categories and profile
def test_categories_page_and_admin_management(app, conn):
    user = new_user(conn)
    cu = client_for(app, user)
    page_has(cu.get("/categories"), "Food", "Transportation", "Accommodation", "Groceries", "Utilities", "Other")
    ca = app.test_client()
    login(ca, ADMIN_USERNAME, ADMIN_PASSWORD)
    name = uniq("Snacks")[:20]
    page_has(post(ca, "/categories", {"action": "add", "category_name": name, "description": "Light food"}), "Category added", name)
    page_has(post(ca, "/categories", {"action": "add", "category_name": name.upper()}), "already exists")
    cid = scalar(conn, "SELECT category_id FROM expense_categories WHERE category_name = %s", (name,))
    owner = new_user(conn)
    g = new_group(conn, owner)
    co = client_for(app, owner)
    assert f'value="{cid}"' in text(co.get(f"/groups/{g}/expenses/new"))
    post(ca, "/categories", {"action": "toggle", "category_id": str(cid)})
    assert f'value="{cid}"' not in text(co.get(f"/groups/{g}/expenses/new"))            # inactive: no longer offered
    page_has(post(co, f"/groups/{g}/expenses/new", {"expense_title": "x", "amount": "5", "expense_date": "2024-05-01", "category_id": str(cid),
             "paid_by": str(owner["id"]), "split_mode": "equal", "participants": [str(owner["id"])]}), "valid, active category")


def test_profile_update_and_password_change(app, conn):
    u = new_user(conn)
    c = client_for(app, u)
    page_has(post(c, "/profile", {"action": "details", "full_name": "Renamed Person", "email": ""}), "details were updated", "Renamed Person")
    assert scalar(conn, "SELECT email FROM users WHERE user_id = %s", (u["id"],)) is None          # empty email stored as NULL
    page_has(post(c, "/profile", {"action": "password", "current_password": "wrong", "new_password": "New-password-12", "confirm_password": "New-password-12"}),
             "current password is not correct")
    page_has(post(c, "/profile", {"action": "password", "current_password": u["password"], "new_password": "short", "confirm_password": "short"}),
             "at least 8 characters")
    page_has(post(c, "/profile", {"action": "password", "current_password": u["password"], "new_password": "New-password-12", "confirm_password": "New-password-12"}),
             "password was changed")
    assert login(app.test_client(), u["username"], u["password"]).status_code == 401
    assert login(app.test_client(), u["username"], "New-password-12").status_code == 302


# ================================================================= administrator
def test_admin_dashboard_shows_the_real_numbers(app, conn):
    owner, other = new_user(conn), new_user(conn)
    g = new_group(conn, owner, other)
    add_expense(conn, g, owner, "123.45", [owner, other])
    c = app.test_client()
    login(c, ADMIN_USERNAME, ADMIN_PASSWORD)
    body = text(c.get("/admin"))
    assert f'<div class="stat-value">{count(conn, "users")}</div>' in body
    total = scalar(conn, "SELECT SUM(amount) FROM expenses WHERE status = 'active'")
    assert f"₱{total:,.2f}" in body
    assert f'<div class="stat-value">{count(conn, "settlements", "status = %s", ("completed",))}</div>' in body
    assert 'Active expenses whose shares do not add up</td><td class="text-end">0<' in body
    assert 'do not sum to 0.00</td>' in body or 'Groups whose balances do not sum to 0.00</td><td class="text-end">0<' in body
    page_has(post(c, "/admin/run-overdue-check"), "Overdue check finished")


def test_admin_manages_users(app, conn):
    c = app.test_client()
    login(c, ADMIN_USERNAME, ADMIN_PASSWORD)
    name = uniq("staff")
    page_has(post(c, "/admin/users/new", {"full_name": "New Staff", "username": name, "email": "", "password": "Staff-password-1"}), "User created", name)
    uid = scalar(conn, "SELECT user_id FROM users WHERE username = %s", (name,))
    assert login(app.test_client(), name, "Staff-password-1").status_code == 302
    post(c, f"/admin/users/{uid}/toggle")
    assert login(app.test_client(), name, "Staff-password-1").status_code == 401           # deactivated
    post(c, f"/admin/users/{uid}/toggle")
    page_has(post(c, f"/admin/users/{uid}/reset-password", {"new_password": "Reset-password-2"}), "Password reset")
    assert login(app.test_client(), name, "Reset-password-2").status_code == 302
    page_has(post(c, f"/admin/users/{uid}/reset-password", {"new_password": "short"}), "at least 8 characters")
    assert post(c, "/admin/users/99999999/toggle").status_code == 404


def test_admin_can_manage_any_group_even_without_being_a_member(app, conn):
    owner, other = new_user(conn), new_user(conn)
    g = new_group(conn, owner, other)
    c = app.test_client()
    login(c, ADMIN_USERNAME, ADMIN_PASSWORD)
    page_has(c.get(f"/groups/{g}"), "Overview")
    r = post(c, f"/groups/{g}/expenses/new", {"expense_title": "Admin entry", "amount": "80.00", "expense_date": "2024-05-01",
             "category_id": str(category_id(conn)), "paid_by": str(owner["id"]), "split_mode": "equal",
             "participants": [str(owner["id"]), str(other["id"])]})
    page_has(r, "Expense saved", "Admin entry")
    admin_id = scalar(conn, "SELECT user_id FROM users WHERE role = 'admin'")
    assert scalar(conn, "SELECT created_by FROM expenses WHERE expense_title = 'Admin entry' AND group_id = %s", (g,)) == admin_id
    sid = settle(conn, g, other, owner, "10.00")
    page_has(post(c, f"/settlements/{sid}/complete"), "now counts toward balances")             # admin acts as a manager
    page_has(post(c, f"/groups/{g}/members/add", {"username": new_user(conn)["username"]}), "Member added")
