"""Small factories used by the tests. Every test makes its own users and groups (unique names),
so tests never interfere with each other even though financial records can never be deleted."""
import itertools
import json
import uuid
from decimal import Decimal

from werkzeug.security import generate_password_hash

PASSWORD = "Passw0rd!test"
_counter = itertools.count(1)


def uniq(prefix="u"):
    return f"{prefix}{next(_counter)}_{uuid.uuid4().hex[:6]}"


def scalar(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    row = cur.fetchone()
    cur.close()
    return None if row is None else row[0]


def rows(conn, sql, params=()):
    cur = conn.cursor(dictionary=True)
    cur.execute(sql, params)
    out = [{k.lower(): v for k, v in r.items()} for r in cur.fetchall()]   # information_schema uses UPPERCASE names
    cur.close()
    return out


def run(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    result = (cur.lastrowid, cur.rowcount)
    cur.close()
    return result


def call(conn, name, args):
    cur = conn.cursor()
    result = cur.callproc(name, list(args))
    try:
        for r in cur.stored_results():
            r.fetchall()
    except Exception:
        pass
    cur.close()
    return list(result)


def new_user(conn, tag="u", password=PASSWORD, email=True):
    username = uniq(tag)
    uid, _ = run(conn,
                 "INSERT INTO users (full_name, username, email, password_hash) VALUES (%s, %s, %s, %s)",
                 (f"Test {username}", username, f"{username}@example.com" if email else None,
                  generate_password_hash(password)))
    return {"id": uid, "username": username, "password": password}


def new_group(conn, creator, *others):
    gid = call(conn, "sp_create_group", [uniq("grp"), None, creator["id"], 0])[3]
    for u in others:
        run(conn, "INSERT INTO group_members (group_id, user_id) VALUES (%s, %s)", (gid, u["id"]))
    return gid


def category_id(conn, name="Food"):
    return scalar(conn, "SELECT category_id FROM expense_categories WHERE category_name = %s", (name,))


def add_expense(conn, gid, payer, amount, shares, mode="equal", title="Test expense", created_by=None,
                date=None, category=None, description=None):
    """shares: list of user dicts (equal) or list of (user, 'amount') pairs (custom). Returns the new expense id."""
    if mode == "equal":
        payload = [u["id"] if isinstance(u, dict) else u for u in shares]
    else:
        payload = [{"user_id": u["id"], "amount": str(a)} for u, a in shares]
    args = [gid, payer["id"], category or category_id(conn), title, description, Decimal(str(amount)),
            date, mode, json.dumps(payload), (created_by or payer)["id"], 0]
    return call(conn, "sp_add_expense", args)[-1]


def settle(conn, gid, payer, receiver, amount, completed=False, due=None, by=None, when=None):
    args = [gid, payer["id"], receiver["id"], Decimal(str(amount)), when, due,
            1 if completed else 0, (by or receiver)["id"], 0]
    return call(conn, "sp_record_settlement", args)[-1]


def net(conn, gid, user):
    return scalar(conn, "SELECT net_balance FROM vw_member_balances WHERE group_id = %s AND user_id = %s",
                  (gid, user["id"]))


def count(conn, table, where="1=1", params=()):
    return scalar(conn, f"SELECT COUNT(*) FROM {table} WHERE {where}", params)


def expected_shares(amount, n):
    """Python reference for the equal-split rule: whole centavos, extras to the first people."""
    cents = int((Decimal(str(amount)) * 100).to_integral_value())
    base, extra = divmod(cents, n)
    return [Decimal(base + (1 if i < extra else 0)) / 100 for i in range(n)]


# ---------------------------------------------------------------- web-application helpers
def token(client):
    """The session's CSRF token (visit a page with a form first if there is none yet)."""
    for url in (None, "/login", "/profile"):
        if url:
            client.get(url)
        with client.session_transaction() as s:
            t = s.get("csrf_token")
        if t:
            return t
    raise AssertionError("no CSRF token could be obtained")


def post(client, url, data=None, follow=True, with_token=True):
    data = dict(data or {})
    if with_token:
        data["csrf_token"] = token(client)
    return client.post(url, data=data, follow_redirects=follow)


def login(client, username, password, follow=False):
    return post(client, "/login", {"username": username, "password": password}, follow=follow)


def client_for(app, user):
    c = app.test_client()
    r = login(c, user["username"], user["password"])
    assert r.status_code == 302, "login failed in test setup"
    return c


def text(response):
    return response.get_data(as_text=True)
