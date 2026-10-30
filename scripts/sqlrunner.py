"""Tiny SQL script runner that understands DELIMITER (used by init_db.py and the tests).

MySQL Workbench does this for you; this lets you (and the automated tests) run the same
.sql files from the command line.
"""
import re
import mysql.connector


def split_statements(sql_text):
    """Split a SQL script into statements, honouring DELIMITER, quotes and comments."""
    statements, buf = [], []
    delimiter = ";"
    i, n = 0, len(sql_text)
    at_line_start = True
    while i < n:
        ch = sql_text[i]

        # DELIMITER command (only at the start of a line, outside a statement)
        if at_line_start and not "".join(buf).strip():
            m = re.match(r"[ \t]*DELIMITER[ \t]+(\S+)[ \t]*\r?\n?", sql_text[i:], re.IGNORECASE)
            if m:
                delimiter = m.group(1)
                i += m.end()
                buf = []
                continue
        at_line_start = ch == "\n"

        # comments
        if sql_text.startswith("--", i) and (i + 2 >= n or sql_text[i + 2] in " \t\r\n"):
            j = sql_text.find("\n", i)
            i = n if j == -1 else j
            continue
        if ch == "#":
            j = sql_text.find("\n", i)
            i = n if j == -1 else j
            continue
        if sql_text.startswith("/*", i):
            j = sql_text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue

        # quoted text
        if ch in ("'", '"', "`"):
            quote, j = ch, i + 1
            while j < n:
                if sql_text[j] == "\\" and quote != "`":
                    j += 2
                    continue
                if sql_text[j] == quote:
                    if j + 1 < n and sql_text[j + 1] == quote:
                        j += 2
                        continue
                    break
                j += 1
            buf.append(sql_text[i:j + 1])
            i = j + 1
            continue

        # end of statement
        if sql_text.startswith(delimiter, i):
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += len(delimiter)
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def run_script(conn, sql_text, database=None):
    """Run every statement of a script on an open connection. Returns the number run."""
    if database:
        sql_text = re.sub(r"(?im)^(\s*CREATE DATABASE IF NOT EXISTS\s+)fairshare\b", r"\1" + database, sql_text)
        sql_text = re.sub(r"(?im)^(\s*USE\s+)fairshare\s*;", r"\1" + database + ";", sql_text)
    cur = conn.cursor()
    count = 0
    for stmt in split_statements(sql_text):
        cur.execute(stmt)
        try:
            cur.fetchall()
        except mysql.connector.Error:
            pass
        try:
            for result in cur.stored_results():
                result.fetchall()
        except (mysql.connector.Error, AttributeError):
            pass
        count += 1
    cur.close()
    return count
