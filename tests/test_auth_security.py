"""Authentication, authorization, the single administrator, and web security."""
import mysql.connector
import pytest
from werkzeug.security import generate_password_hash

from setup_admin import create_admin
from init_db import build_database

from .conftest import ADMIN_PASSWORD, ADMIN_USERNAME
from .helpers import (PASSWORD, add_expense, call, client_for, count, login, new_group, new_user, post, rows,
                      run, scalar, settle, text, token, uniq)


def admin_client(app):
    c = app.test_client()
    assert login(c, ADMIN_USERNAME, ADMIN_PASSWORD).status_code == 302
    return c


# ------------------------------------------------------------------ login / logout
def test_the_administrator_can_log_in(app):
    c = admin_client(app)
    assert c.get("/dashboard").status_code == 200
    assert c.get("/admin").status_code == 200


def test_wrong_password_and_unknown_user_get_the_same_message(app, conn):
    u = new_user(conn)
    c = app.test_client()
    r1 = login(c, u["username"], "wrong-password", follow=True)
    r2 = login(app.test_client(), "no_such_user_xyz", "whatever123", follow=True)
    assert r1.status_code == 401 and r2.status_code == 401
    assert "Invalid username or password" in text(r1) and "Invalid username or password" in text(r2)
    assert c.get("/dashboard").status_code == 302          # still not logged in


def test_a_locked_account_and_an_inactive_account_cannot_log_in(app, conn):
    locked = new_user(conn)
    run(conn, "UPDATE users SET password_hash = '!' WHERE user_id = %s", (locked["id"],))
    assert login(app.test_client(), locked["username"], "anything-123").status_code == 401
    inactive = new_user(conn)
    run(conn, "UPDATE users SET is_active = 0 WHERE user_id = %s", (inactive["id"],))
    assert login(app.test_client(), inactive["username"], inactive["password"]).status_code == 401


def test_deactivating_an_account_ends_its_existing_session(app, conn):
    u = new_user(conn)
    c = client_for(app, u)
    assert c.get("/dashboard").status_code == 200
    run(conn, "UPDATE users SET is_active = 0 WHERE user_id = %s", (u["id"],))
    assert c.get("/dashboard").status_code == 302


def test_logout_ends_the_session(app, conn):
    u = new_user(conn)
    c = client_for(app, u)
    assert c.get("/dashboard").status_code == 200
    assert c.get("/logout").status_code == 405              # logging out needs a POST
    assert post(c, "/logout", follow=False).status_code == 302
    assert c.get("/dashboard").status_code == 302           # session is gone
    assert c.get("/groups").status_code == 302


PROTECTED = ["/dashboard", "/groups", "/categories", "/profile", "/admin", "/admin/users", "/groups/1",
             "/groups/1/members", "/groups/1/balances", "/groups/1/expenses", "/groups/1/expenses/new",
             "/groups/1/settlements", "/groups/1/settlements/history", "/groups/1/settlements/new", "/expenses/1"]


@pytest.mark.parametrize("url", PROTECTED)
def test_protected_pages_redirect_to_login_when_logged_out(client, url):
    r = client.get(url)
    assert r.status_code == 302 and "/login" in r.headers["Location"]


# ------------------------------------------------------------------ registration
def test_registration_creates_a_regular_user_with_a_hashed_password(client, conn):
    name = uniq("reg")
    r = post(client, "/register", {"full_name": "New Person", "username": name, "email": f"{name}@example.com",
                                    "password": "A-good-password-1", "confirm_password": "A-good-password-1"})
    assert "You can log in now" in text(r)
    row = rows(conn, "SELECT role, password_hash FROM users WHERE username = %s", (name,))[0]
    assert row["role"] == "user"
    assert row["password_hash"] != "A-good-password-1" and "A-good-password-1" not in row["password_hash"]
    assert row["password_hash"].startswith(("scrypt:", "pbkdf2:"))
    assert login(client, name, "A-good-password-1").status_code == 302


def test_registration_ignores_a_role_field_so_nobody_can_register_as_admin(client, conn):
    name = uniq("sneaky")
    post(client, "/register", {"full_name": "Sneaky", "username": name, "password": "A-good-password-1",
                               "confirm_password": "A-good-password-1", "role": "admin", "is_admin": "1"})
    assert scalar(conn, "SELECT role FROM users WHERE username = %s", (name,)) == "user"
    assert count(conn, "users", "role = 'admin'") == 1


@pytest.mark.parametrize("changes,message", [
    ({"password": "short", "confirm_password": "short"}, "at least 8 characters"),
    ({"confirm_password": "does-not-match-1"}, "do not match"),
    ({"username": "a b"}, "Username must be"),
    ({"username": "ab"}, "Username must be"),
    ({"full_name": "   "}, "Full name is required"),
    ({"email": "not-an-email"}, "valid email"),
])
def test_registration_validates_input_on_the_server(client, conn, changes, message):
    form = {"full_name": "Valid Name", "username": uniq("val"), "email": "", "password": "A-good-password-1",
            "confirm_password": "A-good-password-1"}
    form.update(changes)
    before = count(conn, "users")
    r = post(client, "/register", form)
    assert r.status_code == 400 and message in text(r)
    assert count(conn, "users") == before


def test_duplicate_username_and_email_are_rejected_safely(client, conn):
    u = new_user(conn)
    base = {"full_name": "Dup", "password": "A-good-password-1", "confirm_password": "A-good-password-1"}
    r = post(client, "/register", dict(base, username=u["username"].upper()))      # usernames ignore letter case
    assert "already taken" in text(r)
    r = post(client, "/register", dict(base, username=uniq("dup"), email=f"{u['username']}@example.com"))
    assert "already registered" in text(r)
    assert "Traceback" not in text(r) and "IntegrityError" not in text(r)


# ------------------------------------------------------------------ exactly one administrator
def test_a_regular_user_cannot_reach_any_admin_endpoint(app, conn):
    u = new_user(conn)
    c = client_for(app, u)
    assert c.get("/admin").status_code == 403
    assert c.get("/admin/users").status_code == 403
    for url, data in [("/admin/users/new", {"full_name": "x", "username": uniq("x"), "password": "A-good-password-1"}),
                      (f"/admin/users/{u['id']}/toggle", {}), (f"/admin/users/{u['id']}/reset-password", {"new_password": "A-good-password-1"}),
                      ("/admin/run-overdue-check", {}), ("/categories", {"action": "add", "category_name": uniq("cat")})]:
        assert post(c, url, data).status_code == 403, url


def test_a_user_cannot_promote_themselves_through_the_profile_page(app, conn):
    u = new_user(conn)
    c = client_for(app, u)
    post(c, "/profile", {"action": "details", "full_name": "Hacker", "email": "", "role": "admin", "is_admin": "1"})
    assert scalar(conn, "SELECT role FROM users WHERE user_id = %s", (u["id"],)) == "user"
    assert count(conn, "users", "role = 'admin'") == 1


def test_the_admin_cannot_create_a_second_admin_or_deactivate_the_admin(app, conn):
    c = admin_client(app)
    name = uniq("made")
    post(c, "/admin/users/new", {"full_name": "Made By Admin", "username": name, "password": "A-good-password-1", "role": "admin"})
    assert scalar(conn, "SELECT role FROM users WHERE username = %s", (name,)) == "user"
    admin_id = scalar(conn, "SELECT user_id FROM users WHERE role = 'admin'")
    r = post(c, f"/admin/users/{admin_id}/toggle")
    assert "cannot be deactivated" in text(r)
    assert scalar(conn, "SELECT is_active FROM users WHERE user_id = %s", (admin_id,)) == 1
    assert count(conn, "users", "role = 'admin'") == 1


def test_setup_admin_never_creates_a_second_administrator(db_cfg, conn):
    created, message = create_admin(db_cfg, "second_admin", "Second", "A-good-password-1")
    assert created is False and "already exists" in message
    assert count(conn, "users", "role = 'admin'") == 1
    assert count(conn, "users", "username = 'second_admin'") == 0


def test_initial_admin_setup_on_a_fresh_database_works_once_only(db_cfg):
    fresh = dict(db_cfg, database="fairshare_setup_test")
    build_database(fresh["host"], fresh["port"], fresh["user"], fresh["password"], fresh["database"], reset=True)
    try:
        c = mysql.connector.connect(autocommit=True, **fresh)
        assert scalar(c, "SELECT COUNT(*) FROM users WHERE role = 'admin'") == 0          # starts with none
        assert create_admin(fresh, "first_admin", "First Admin", "A-good-password-1")[0] is True
        again = create_admin(fresh, "another_admin", "Another", "A-good-password-1")
        assert again[0] is False
        assert scalar(c, "SELECT COUNT(*) FROM users WHERE role = 'admin'") == 1
        stored = scalar(c, "SELECT password_hash FROM users WHERE username = 'first_admin'")
        assert "A-good-password-1" not in stored
        c.close()
    finally:
        k = mysql.connector.connect(autocommit=True, **{x: fresh[x] for x in ("host", "port", "user", "password")})
        k.cursor().execute("DROP DATABASE IF EXISTS fairshare_setup_test")
        k.close()


# ------------------------------------------------------------------ authorization on records
def test_users_cannot_see_or_change_groups_they_do_not_belong_to(app, conn):
    owner, member, outsider = new_user(conn), new_user(conn), new_user(conn)
    g = new_group(conn, owner, member)
    eid = add_expense(conn, g, owner, "100.00", [owner, member])
    sid = settle(conn, g, member, owner, "10.00")
    c = client_for(app, outsider)
    for url in [f"/groups/{g}", f"/groups/{g}/members", f"/groups/{g}/balances", f"/groups/{g}/expenses",
                f"/groups/{g}/expenses/new", f"/groups/{g}/settlements", f"/groups/{g}/settlements/history",
                f"/groups/{g}/settlements/new", f"/expenses/{eid}"]:
        assert c.get(url).status_code == 403, url
    before = (count(conn, "expenses", "group_id = %s", (g,)), count(conn, "settlements", "group_id = %s", (g,)))
    assert post(c, f"/groups/{g}/expenses/new", {"expense_title": "Hack", "amount": "10", "expense_date": "2024-01-01",
                "category_id": "1", "paid_by": str(owner["id"]), "split_mode": "equal", "participants": [str(owner["id"])]}).status_code == 403
    assert post(c, f"/groups/{g}/settlements/new", {"payer_id": str(member["id"]), "receiver_id": str(owner["id"]), "amount": "1"}).status_code == 403
    assert post(c, f"/settlements/{sid}/complete").status_code == 403
    assert post(c, f"/settlements/{sid}/cancel").status_code == 403
    assert post(c, f"/expenses/{eid}/archive").status_code == 403
    assert post(c, f"/groups/{g}/members/add", {"username": outsider["username"]}).status_code == 403
    assert (count(conn, "expenses", "group_id = %s", (g,)), count(conn, "settlements", "group_id = %s", (g,))) == before
    assert scalar(conn, "SELECT status FROM settlements WHERE settlement_id = %s", (sid,)) == "pending"
    # the outsider's group list does not even mention it
    assert f"/groups/{g}" not in text(c.get("/groups"))


def test_a_plain_member_cannot_manage_the_group(app, conn):
    owner, member, third = new_user(conn), new_user(conn), new_user(conn)
    g = new_group(conn, owner, member, third)
    c = client_for(app, member)
    assert post(c, f"/groups/{g}/members/add", {"username": third["username"]}).status_code == 403
    assert post(c, f"/groups/{g}/archive").status_code == 403
    assert post(c, f"/groups/{g}/members/{third['id']}/remove").status_code == 403
    assert post(c, f"/groups/{g}/reactivate").status_code == 403
    assert scalar(conn, "SELECT status FROM expense_groups WHERE group_id = %s", (g,)) == "active"


# ------------------------------------------------------------------ CSRF, headers, cookies, redirects
def test_posts_without_a_valid_csrf_token_are_rejected(app, conn):
    u = new_user(conn)
    c = client_for(app, u)
    before = count(conn, "expense_groups")
    assert post(c, "/groups/new", {"group_name": "No token"}, with_token=False).status_code == 400
    r = c.post("/groups/new", data={"group_name": "Bad token", "csrf_token": "forged"})
    assert r.status_code == 400
    assert count(conn, "expense_groups") == before
    assert post(c, "/logout", with_token=False).status_code == 400
    assert c.get("/dashboard").status_code == 200           # the forged logout did nothing
    r = post(c, "/groups/new", {"group_name": uniq("With token")})
    assert r.status_code == 200 and count(conn, "expense_groups") == before + 1


def test_security_headers_and_session_cookie_flags(client):
    r = client.get("/login")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert "no-store" in r.headers["Cache-Control"]
    cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_content_security_policy_allows_every_external_file_the_pages_load(client):
    """A browser blocks anything the policy does not allow, but the test client never does,
    so check the policy against the hosts used by the real page template."""
    import re
    import os
    base = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "base.html"), encoding="utf-8").read()
    csp = client.get("/login").headers["Content-Security-Policy"]
    directives = {d.split()[0]: d.split()[1:] for d in csp.split(";") if d.strip()}
    for tag, directive in (("script", "script-src"), ("link", "style-src")):
        for url in re.findall(rf"<{tag}[^>]+(?:src|href)=\"(https?://[^\"/]+)", base):
            assert url in directives[directive], f"{url} would be blocked by the {directive} policy"
    assert "<script>" not in base and "onclick=" not in base          # no inline scripts: 'unsafe-inline' is not allowed


@pytest.mark.parametrize("target,ok", [("https://evil.example/x", False), ("//evil.example", False),
                                       ("javascript:alert(1)", False), ("/groups", True)])
def test_login_redirect_target_cannot_leave_the_site(app, conn, target, ok):
    u = new_user(conn)
    c = app.test_client()
    r = post(c, f"/login?next={target}", {"username": u["username"], "password": u["password"]}, follow=False)
    assert r.status_code == 302
    location = r.headers["Location"]
    assert ("evil" not in location and "javascript" not in location) and (location.endswith("/groups") == ok)


def test_repeated_failed_logins_are_throttled(app, conn):
    u = new_user(conn)
    c = app.test_client()
    for _ in range(5):
        assert login(c, u["username"], "wrong-password").status_code == 401
    r = login(c, u["username"], u["password"], follow=True)          # even the right password is refused for a while
    assert r.status_code == 429 and "Too many failed attempts" in text(r)


# ------------------------------------------------------------------ robustness
def test_sql_injection_attempts_are_treated_as_plain_text(app, conn):
    c = app.test_client()
    assert login(c, "' OR '1'='1' -- ", "x").status_code == 401
    assert login(c, "admin'--", "x").status_code == 401
    u = new_user(conn)
    c = client_for(app, u)
    name = "Robert'); DROP TABLE users;-- "
    post(c, "/groups/new", {"group_name": name})
    assert scalar(conn, "SELECT COUNT(*) FROM expense_groups WHERE group_name = %s", (name.strip(),)) == 1
    assert count(conn, "users") > 0                                       # the table is still there
    g = scalar(conn, "SELECT group_id FROM expense_groups WHERE created_by = %s", (u["id"],))
    r = post(c, f"/groups/{g}/members/add", {"username": "x' OR '1'='1"})
    assert "No active user has that username" in text(r)


def test_database_outage_shows_a_safe_page_and_leaks_nothing(db_cfg):
    from fairshare import create_app
    broken = create_app({"DB": dict(db_cfg, port=1, password="TOP-SECRET-PW"), "SECRET_KEY": "k" * 32, "TESTING": True})
    r = broken.test_client().get("/login")
    body = text(r)
    assert r.status_code == 503 and "not available" in body
    assert "TOP-SECRET-PW" not in body and "Traceback" not in body and "mysql" not in body.lower().replace("mysql version", "")


def test_unknown_pages_and_ids_show_friendly_errors(app, conn):
    u = new_user(conn)
    c = client_for(app, u)
    assert c.get("/groups/99999999").status_code == 404
    assert c.get("/expenses/99999999").status_code == 404
    assert post(c, "/settlements/99999999/complete").status_code == 404
    r = c.get("/no/such/page")
    assert r.status_code == 404 and "Traceback" not in text(r)


def test_application_refuses_to_start_without_a_secret_key(db_cfg):
    from fairshare import create_app
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app({"DB": db_cfg, "SECRET_KEY": ""})
