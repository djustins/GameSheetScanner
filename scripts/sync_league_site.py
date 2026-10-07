#!/usr/bin/env python3
"""
scripts/sync_league_site.py

Pulls the schedule and every played game (score, goals, penalties) from the
league's stats site, https://teampgh-statsandstandings.web.app, into this
app's database -- see league_site_sync.py for exactly what lands where.
Standings and player stats then follow on their own, since the app computes
them from the games.

Safe to re-run, e.g. after each game night: a game already imported is
refreshed in place, and a game already entered from a scanned sheet is left
alone.

Usage:
    export DATABASE_URL=postgresql://user:password@host:port/dbname?sslmode=require
    python scripts/sync_league_site.py --dry-run            # show what would change
    python scripts/sync_league_site.py                      # every current league
    python scripts/sync_league_site.py --league Penguin     # just one (repeatable)
    python scripts/sync_league_site.py --refresh-all        # re-read every imported game

The deployed API runs the same sync nightly: see POST /league-site/sync in
api.py and .github/workflows/nightly-league-sync.yml.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import game_sheet_core as core  # noqa: E402
import league_site_sync  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--league", action="append", help="Only this league, e.g. Penguin (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without saving anything")
    parser.add_argument(
        "--refresh-all", action="store_true",
        help=f"Re-read every imported game, not just those from the last {league_site_sync.REFRESH_DAYS} days",
    )
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Error: set the DATABASE_URL environment variable first.")
    conn = core.init_db(dsn)

    reports = league_site_sync.sync(
        conn, league_names=args.league, dry_run=args.dry_run,
        refresh_days=None if args.refresh_all else league_site_sync.REFRESH_DAYS,
    )
    if not reports:
        sys.exit("No matching current leagues on the site.")
    for report in reports:
        print(league_site_sync.format_report(report, dry_run=args.dry_run))
    if args.dry_run:
        print("\nDry run: nothing was saved.")


if __name__ == "__main__":
    main()
