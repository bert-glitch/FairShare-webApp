"""Shared test setup.

The tests build their OWN database (default name: fairshare_test) from the project's SQL files.
They never touch the real "fairshare" database. Set these environment variables if the defaults
(root, no password, localhost) do not match your MySQL:

    TEST_DB_HOST  TEST_DB_PORT  TEST_DB_USER  TEST_DB_PASSWORD  TEST_DB_NAME
"""
import os
import sys

import mysql.connector
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from fairshare import create_app                     # noqa: E402
from fairshare.security import throttle               # noqa: E402
from init_db import build_database                    # noqa: E402
from setup_admin import create_admin                  # noqa: E402

ADMIN_USERNAME = "test_admin"
ADMIN_PASSWORD = "Admin-Test-Pass-1"


def _cfg(database=None):
    return {
        "host": os.getenv("TEST_DB_HOST", "localhost"),
        "port": int(os.getenv("TEST_DB_PORT", "3306")),
        "user": os.getenv("TEST_DB_USER", "root"),
        "password": os.getenv("TEST_DB_PASSWORD", ""),
        "database": database or os.getenv("TEST_DB_NAME", "fairshare_test"),
    }


@pytest.fixture(scope="session")
def db_cfg():
    cfg = _cfg()
    assert cfg["database"].endswith("_test"), "Tests only run against a database whose name ends with _test"
    return cfg


@pytest.fixture(scope="session", autouse=True)
def database(db_cfg):
    """Rebuild the test database from the SQL files once per test run, then create the one admin."""
    build_database(db_cfg["host"], db_cfg["port"], db_cfg["user"], db_cfg["password"],
                   db_cfg["database"], include_sample=False, reset=True)
    created, msg = create_admin(db_cfg, ADMIN_USERNAME, "Test Administrator", ADMIN_PASSWORD)
    assert created, msg
    return db_cfg


@pytest.fixture()
def conn(db_cfg):
    c = mysql.connector.connect(autocommit=True, **db_cfg)
    yield c
    c.close()


@pytest.fixture()
def app(db_cfg):
    throttle.reset_all()
    return create_app({"DB": db_cfg, "SECRET_KEY": "test-secret-key-" + "x" * 20, "TESTING": True})


@pytest.fixture()
def client(app):
    return app.test_client()
