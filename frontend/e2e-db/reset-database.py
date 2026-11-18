"""Reset the dedicated browser-test database so repeated local runs are deterministic.

Quota counters, sessions, threads and checkpoints all persist between runs otherwise, and the
fixed test client addresses would already have spent their daily allowance on the second run.

Only ever runs against a database whose name ends in `_e2e`, mirroring the safety rule the
backend's PostgreSQL test fixtures enforce.
"""

import os
import sys

import psycopg

TABLES = (
    "users",
    "auth_sessions",
    "oauth_states",
    "principals",
    "threads",
    "runs",
    "rate_limit_counters",
    "quota_usage",
    "budget_reservations",
    "budget_days",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)

url = os.environ.get("DATABASE_URL", "")
if not url:
    sys.exit("DATABASE_URL is required")
name = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
if not name.endswith("_e2e"):
    sys.exit(f"Refusing to reset a database that is not named *_e2e (got {name!r})")

with psycopg.connect(url, autocommit=True) as connection:
    connection.execute(f"TRUNCATE {', '.join(TABLES)} CASCADE")
print("browser-test database reset")
