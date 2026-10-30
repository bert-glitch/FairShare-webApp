"""Database access. Every query is parameterized (%s placeholders), never string-built.

Errors from MySQL are turned into short, safe messages (UserError). Technical detail goes to the
log, never to the user, and never includes passwords or query parameters.
"""
import re

import mysql.connector
from flask import current_app, g


class UserError(Exception):
    """An error whose message is safe and helpful to show to the user."""


class DatabaseUnavailable(UserError):
    """The database cannot be reached."""


UNIQUE_MESSAGES = {
    "uq_users_username": "That username is already taken.",
    "uq_users_email": "That email is already registered.",
    "uq_users_single_admin": "An administrator already exists. There can only be one.",
    "uq_group_members_pair": "That person is already in this group.",
    "uq_category_name": "A category with that name already exists.",
}
CHECK_MESSAGES = {
    "ck_users_username": "Username must be 3-50 characters: letters, numbers, dots or underscores.",
    "ck_users_email": "Enter a valid email address.",
    "ck_users_name": "The name cannot be blank.",
    "ck_groups_name": "The group name cannot be blank.",
    "ck_cat_name": "The category name cannot be blank.",
    "ck_exp_title": "The expense title cannot be blank.",
    "ck_exp_amount": "The expense amount must be greater than zero.",
    "ck_split_share": "A share cannot be negative.",
    "ck_stl_due": "The due date cannot be before the payment date.",
}
CONNECTION_ERRNOS = {1045, 1049, 2002, 2003, 2006, 2013, 2055}


def translate_error(err):
    """Map a mysql.connector error to (message, is_unexpected)."""
    errno = getattr(err, "errno", None)
    msg = getattr(err, "msg", "") or str(err)
    if errno == 1644:                                   # our SIGNAL messages
        return msg, False
    if errno == 1062:
        for key, text in UNIQUE_MESSAGES.items():
            if key in msg:
                return text, False
        return "That record already exists.", False
    if errno == 3819:
        m = re.search(r"'(ck_[a-z_]+)'", msg)
        if m and m.group(1) in CHECK_MESSAGES:
            return CHECK_MESSAGES[m.group(1)], False
        return "One of the values is not allowed.", False
    if errno == 1452:
        return "That does not exist, or the person is not a member of this group.", False
    if errno == 1451:
        return "This record is in use and cannot be removed.", False
    if errno in (1213, 1205):
        return "The system was busy. Please try again.", False
    return "Something went wrong while saving. Nothing was changed. Please try again.", True


def _raise_user_error(err):
    errno = getattr(err, "errno", None)
    if errno in CONNECTION_ERRNOS:
        current_app.logger.error("Database connection problem: errno=%s", errno)
        raise DatabaseUnavailable("The database is not available right now. Please try again later.") from err
    message, unexpected = translate_error(err)
    if unexpected:
        current_app.logger.error("Unexpected database error: errno=%s msg=%s", errno, getattr(err, "msg", ""))
    raise UserError(message) from err


def get_db():
    if "db" not in g:
        try:
            g.db = mysql.connector.connect(
                autocommit=True, charset="utf8mb4", connection_timeout=5, **current_app.config["DB"]
            )
        except mysql.connector.Error as err:
            _raise_user_error(err)
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        try:
            conn.close()
        except mysql.connector.Error:
            pass


def fetch_all(sql, params=()):
    try:
        cur = get_db().cursor(dictionary=True)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows
    except mysql.connector.Error as err:
        _raise_user_error(err)


def fetch_one(sql, params=()):
    rows = fetch_all(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    """Run one INSERT/UPDATE. Returns (last_insert_id, rows_changed)."""
    try:
        cur = get_db().cursor()
        cur.execute(sql, params)
        result = (cur.lastrowid, cur.rowcount)
        cur.close()
        return result
    except mysql.connector.Error as err:
        _raise_user_error(err)


def call_proc(name, args):
    """Call a stored procedure. Returns the argument list (OUT parameters are filled in)."""
    try:
        cur = get_db().cursor()
        result = cur.callproc(name, args)
        try:
            for r in cur.stored_results():
                r.fetchall()
        except mysql.connector.Error:
            pass
        cur.close()
        return list(result)
    except mysql.connector.Error as err:
        _raise_user_error(err)


def call_proc_rows(name, args=()):
    """Call a stored procedure that returns one result set. Returns its rows (dicts)."""
    try:
        cur = get_db().cursor(dictionary=True)
        cur.callproc(name, list(args))
        rows = []
        for r in cur.stored_results():
            rows.extend(r.fetchall())
        cur.close()
        return rows
    except mysql.connector.Error as err:
        _raise_user_error(err)
