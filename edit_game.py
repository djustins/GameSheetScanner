#!/usr/bin/env python3
"""
edit_game.py

Corrects a game already stored in the database. Use --list to find a game's
id, then --game-id to open its full record (game info, goals, penalties,
shootout attempts) in a text editor and save your corrections back to the DB.

Usage:
    export DATABASE_URL=postgresql://user:password@host:port/dbname?sslmode=require
    python edit_game.py --list
    python edit_game.py --game-id 5

Requires:
    (only what process_game_sheet.py already needs — no extra installs)
"""

import argparse
import os
import sys
from pathlib import Path

from editor_utils import edit_json_in_editor
from game_sheet_core import init_db, list_divisions, list_games, load_game, update_game
from process_game_sheet import print_summary


def print_games_table(conn):
    divisions = list_divisions(conn)
    any_games = False
    for division in divisions:
        rows = list_games(conn, division["id"])
        if not rows:
            continue
        any_games = True
        print(f"\n--- {division['year']} {division['season']} {division['age_group']} "
              f"({division['category']}) ---")
        print(f"{'id':>4}  {'date':<10} {'matchup':<30} {'score':<9} {'winner':<6} source_file")
        for r in rows:
            matchup = f"{r['home_team']} vs {r['away_team']}"
            score = f"{r['home_final_score']}-{r['away_final_score']}"
            print(f"{r['id']:>4}  {str(r['game_date']):<10} "
                  f"{matchup:<30} {score:<9} {str(r['winner']):<6} {r['source_file']}")
    if not any_games:
        print("No games in database.")


def main():
    parser = argparse.ArgumentParser(description="Edit a game record already stored in the database.")
    parser.add_argument("--list", action="store_true", help="List games and their ids, then exit")
    parser.add_argument("--game-id", type=int, help="id of the game to edit (see --list)")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Error: set the DATABASE_URL environment variable first.")

    conn = init_db(dsn)

    if args.list:
        print_games_table(conn)
        conn.close()
        return

    if args.game_id is None:
        parser.error("pass --game-id <id> to edit a game, or --list to see ids")

    data, source_file = load_game(conn, args.game_id)
    if data is None:
        conn.close()
        sys.exit(f"Error: no game with id={args.game_id}")

    division_row = conn.execute(
        "SELECT division_id FROM games WHERE id = %s", (args.game_id,)
    ).fetchone()
    working_division_id = division_row[0]

    print(f"--- Current data for game_id={args.game_id} ---")
    print_summary(data, Path(source_file or f"game_{args.game_id}"))

    while True:
        edited = edit_json_in_editor(data, file_prefix=f"game_{args.game_id}")
        if edited is not None:
            break
        retry = input("Try editing again? [y/N]: ").strip().lower()
        if retry not in ("y", "yes"):
            print("No changes saved.")
            conn.close()
            return

    update_game(conn, args.game_id, edited, working_division_id)
    print(f"\nUpdated game_id={args.game_id}.")
    print_summary(edited, Path(source_file or f"game_{args.game_id}"))

    conn.close()


if __name__ == "__main__":
    main()
