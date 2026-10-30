"""Give the SAMPLE users (ana, ben, carla, dan) a password so you can log in as them.

Only touches sample users whose password is still locked. The password is never stored in
source code: it is asked for (hidden) or read from DEMO_PASSWORD.
"""
import getpass
import os
import sys

import mysql.connector
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config  # noqa: E402
from fairshare.validators import ValidationError, validate_password  # noqa: E402

password = os.getenv("DEMO_PASSWORD") or getpass.getpass("Password to give the sample users (min 8 chars): ")
try:
    validate_password(password)
except ValidationError as err:
    sys.exit(str(err))

conn = mysql.connector.connect(autocommit=True, **Config.DB)
cur = conn.cursor()
cur.execute("UPDATE users SET password_hash = %s WHERE username IN ('ana','ben','carla','dan') "
            "AND password_hash = '!'", (generate_password_hash(password),))
print(f"Updated {cur.rowcount} sample user(s).")
conn.close()
