#!/usr/bin/env python3
"""
scripts/migrate_sqlite_to_postgres.py

One-time cutover: copy every row out of the existing SQLite hockey.db into a
fresh Postgres database, then run the app's real (engine-agnostic)
data-quality passes once against the migrated data.

This is a standalone script, not part of the running app — game_sheet_core.py
only speaks Postgres now. It reads the SQLite file directly with the stdlib
sqlite3 module and writes through game_sheet_core's Postgres connection.

Usage:
    export DATABASE_URL=postgresql://user:password@host:port/dbname?sslmode=require
    python scripts/migrate_sqlite_to_postgres.py path/to/hockey.db

Safe to re-run against an empty target database; NOT idempotent against a
partially-loaded one (it always INSERTs — re-running after a partial failure
should be done against a freshly recreated Postgres database).
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import game_sheet_core as core  # noqa: E402

# (table, columns) in FK-safe insert order. Columns are listed explicitly
# (rather than SELECT *) so the SQLite source and Postgres target column
# order always line up even if one schema's column order ever drifts from
# the other's.
TABLES_IN_ORDER = [
    ("app_settings", ["key", "value"]),
    ("divisions", ["id", "year", "season", "age_group", "category", "deleted_at"]),
    ("teams", ["id", "division_id", "name"]),
    ("players", [
        "id", "name", "birth_date", "current_division_id", "contact_first_name",
        "contact_last_name", "contact_phone", "contact_email", "deleted_at", "created_at",
    ]),
    ("roster_entries", ["id", "team_id", "number", "name", "player_id"]),
    ("coaches", ["id", "name", "deleted_at", "created_at"]),
    ("team_coaches", ["team_id", "coach_id"]),
    ("games", [
        "id", "game_date", "division", "division_id", "home_team", "home_color",
        "home_final_score", "away_team", "away_color", "away_final_score",
        "went_to_shootout", "winner", "ot_winner", "ot_loser", "source_file", "created_at",
    ]),
    ("goals", [
        "id", "game_id", "side", "scorer_number", "assist1_number", "assist2_number", "period", "time",
    ]),
    ("penalties", ["id", "game_id", "side", "player_number", "penalty_type", "period", "time"]),
    ("shootout_attempts", ["id", "game_id", "side", "round", "player_number", "scored"]),
    ("evaluations", ["id", "player_id", "division_id", "team_id", "grade", "created_at"]),
    ("player_positions", ["player_id", "division_id", "team_id", "position"]),
    ("schedule_games", [
        "id", "division_id", "order_num", "round", "game_date", "home_team", "away_team",
        "start_time", "end_time", "location", "field",
    ]),
]

# Tables with a SERIAL id column that needs its sequence bumped past the
# highest explicitly-inserted id, so the app's next INSERT (no explicit id)
# doesn't collide with migrated data.
SERIAL_TABLES = [
    "divisions", "teams", "players", "roster_entries", "coaches",
    "games", "goals", "penalties", "shootout_attempts", "evaluations", "schedule_games",
]


def copy_table(sqlite_conn: sqlite3.Connection, pg_conn, table: str, columns: list[str]) -> int:
    placeholders = ", ".join(["%s"] * len(columns))
    col_list = ", ".join(columns)
    rows = sqlite_conn.execute(f"SELECT {col_list} FROM {table}").fetchall()
    for row in rows:
        pg_conn.execute(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})", tuple(row))
    pg_conn.commit()
    return len(rows)


def reset_sequence(pg_conn, table: str):
    pg_conn.execute(
        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
        f"COALESCE((SELECT MAX(id) FROM {table}), 1), (SELECT MAX(id) IS NOT NULL FROM {table}))"
    )
    pg_conn.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sqlite_path", help="Path to the existing SQLite hockey.db")
    args = parser.parse_args()

    if not Path(args.sqlite_path).exists():
        sys.exit(f"No such file: {args.sqlite_path}")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Set DATABASE_URL to the target Postgres connection string first.")

    sqlite_conn = sqlite3.connect(args.sqlite_path)
    pg_conn = core.init_db(dsn)  # applies schema_postgres.sql

    print(f"Copying from {args.sqlite_path} -> {dsn.split('@')[-1]}")
    for table, columns in TABLES_IN_ORDER:
        n = copy_table(sqlite_conn, pg_conn, table, columns)
        print(f"  {table}: {n} rows")

    for table in SERIAL_TABLES:
        reset_sequence(pg_conn, table)

    print("Running one-time data-quality passes (merge duplicate teams, "
          "normalize case, normalize dates, fix division typos)...")
    core._merge_duplicate_teams(pg_conn)
    core._normalize_identity_case(pg_conn)
    core._normalize_dates(pg_conn)
    core._fix_division_typos(pg_conn)
    pg_conn.commit()

    print("\nRow counts (SQLite source vs Postgres target):")
    for table, _columns in TABLES_IN_ORDER:
        sqlite_count = sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        pg_count = pg_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        flag = "" if sqlite_count == pg_count else "  <-- MISMATCH"
        print(f"  {table:20s} sqlite={sqlite_count:6d}  postgres={pg_count:6d}{flag}")

    sqlite_conn.close()
    pg_conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
