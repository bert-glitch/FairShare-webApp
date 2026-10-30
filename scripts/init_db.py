"""Create (or update) the FairShare database by running the SQL files in order.

Examples
    python scripts/init_db.py                       # files 00-07 into the database named in .env
    python scripts/init_db.py --sample              # also load the optional sample data (08)
    python scripts/init_db.py --database fairshare_test --reset   # wipe + rebuild a TEST database

It NEVER drops a database unless you pass --reset, and --reset only works on a database
whose name ends in "_test".
"""
import argparse
import glob
import os
import sys

import mysql.connector

sys.path.insert(0, os.path.dirname(__file__))
from sqlrunner import run_script  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
    except ImportError:
        pass


def sql_files(include_sample):
    files = sorted(glob.glob(os.path.join(ROOT, "sql", "0[0-7]_*.sql")))
    if include_sample:
        files += sorted(glob.glob(os.path.join(ROOT, "sql", "08_*.sql")))
    return files


def build_database(host, port, user, password, database, include_sample=False, reset=False):
    if reset and not database.endswith("_test"):
        raise SystemExit("Refusing to --reset: the database name must end with _test.")
    conn = mysql.connector.connect(host=host, port=port, user=user, password=password, autocommit=True)
    try:
        if reset:
            conn.cursor().execute(f"DROP DATABASE IF EXISTS `{database}`")
        for path in sql_files(include_sample):
            with open(path, encoding="utf-8") as fh:
                n = run_script(conn, fh.read(), database=database)
            print(f"  ran {os.path.basename(path)} ({n} statements)")
    finally:
        conn.close()


def main():
    load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=os.getenv("DB_HOST", "localhost"))
    ap.add_argument("--port", type=int, default=int(os.getenv("DB_PORT", "3306")))
    ap.add_argument("--user", default=os.getenv("DB_USER", "root"))
    ap.add_argument("--password", default=os.getenv("DB_PASSWORD", ""))
    ap.add_argument("--database", default=os.getenv("DB_NAME", "fairshare"))
    ap.add_argument("--sample", action="store_true", help="also load the optional sample data")
    ap.add_argument("--reset", action="store_true", help="drop and rebuild (TEST databases only)")
    a = ap.parse_args()
    print(f"Building database '{a.database}' on {a.host}:{a.port} ...")
    build_database(a.host, a.port, a.user, a.password, a.database, a.sample, a.reset)
    print("Done.")


if __name__ == "__main__":
    main()
