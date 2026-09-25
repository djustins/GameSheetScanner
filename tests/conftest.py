"""
tests/conftest.py

Shared pytest fixtures. Every test runs against TEST_DATABASE_URL — a
separate *database* on the same Aiven Postgres node as the app's real one
(see scripts/aiven_ip_filter.py's sibling, aiven_service.create_service_database,
and README's "Running tests" section), never the production database. The
`conn` fixture truncates every table before and after each test, so tests
are isolated from each other without needing SAVEPOINT tricks — the app's
connection wrapper runs in autocommit mode (see _ConnWrapper in
game_sheet_core.py), so there's no open transaction to roll back anyway.

A hard guard below refuses to run against anything whose database name
doesn't contain "test", so a misconfigured or missing TEST_DATABASE_URL
can never accidentally truncate real league data instead of skipping.
"""

import os
import sys
import urllib.parse
from pathlib import Path

import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

import game_sheet_core as core  # noqa: E402


def _test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL not set. Create an isolated test database (see README's "
            "'Running tests' section) and point TEST_DATABASE_URL at it before running tests."
        )
    dbname = urllib.parse.urlparse(url).path.lstrip("/")
    if "test" not in dbname.lower():
        pytest.exit(
            f"TEST_DATABASE_URL's database name ({dbname!r}) doesn't contain 'test' — refusing "
            "to run, since these tests truncate every table. This is a safety check against "
            "an accidentally-prod TEST_DATABASE_URL, not a real naming requirement elsewhere."
        )
    return url


@pytest.fixture(scope="session")
def _session_conn():
    conn = core.init_db(_test_database_url())
    yield conn
    conn.close()


def _truncate_all_tables(conn):
    tables = [row[0] for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()]
    if tables:
        conn.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")


@pytest.fixture
def conn(_session_conn):
    """A connection to the test database, guaranteed empty (every table
    truncated) at the start of the test. Truncated again afterward too, so
    a failed/crashed test doesn't leave state for whatever runs next."""
    _truncate_all_tables(_session_conn)
    yield _session_conn
    _truncate_all_tables(_session_conn)


@pytest.fixture
def division_id(conn) -> int:
    """A ready-to-use division, since almost everything in this app is
    scoped to one. "Penguin" is a real age-group name (see AGE_GROUPS) --
    using one avoids relying on match_age_group()'s fuzzy fallback for an
    unrecognized string."""
    return core.add_division(conn, 2026, "Summer", "Penguin")
