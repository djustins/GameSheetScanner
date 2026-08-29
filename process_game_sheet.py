#!/usr/bin/env python3
"""
process_game_sheet.py

Scans one or more Team Pittsburgh Ball Hockey game sheet scans (PDF or image),
extracts the handwritten stats using Claude's vision, lets you review/correct
the extraction interactively, and stores the results in a SQLite database.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python process_game_sheet.py sheet1.pdf sheet2.jpg --db hockey.db
    python process_game_sheet.py sheet1.pdf --db hockey.db --no-review   # skip confirmation

    # Batch mode: process every sheet in a folder. Each file is previewed in a
    # window while you review the extraction; on acceptance it's moved into
    # <source-dir>/processed (or --processed-dir).
    python process_game_sheet.py --source-dir incoming --db hockey.db

For multi-page PDFs, run split_pdf.py first to split them into single-page
PDFs, then pass the resulting files to this script:
    python split_pdf.py sheet1.pdf
    python process_game_sheet.py sheet1_pages/*.pdf --db hockey.db

To correct a game already stored in the database, use edit_game.py instead:
    python edit_game.py --list --db hockey.db
    python edit_game.py --game-id 5 --db hockey.db

Requires:
    pip install anthropic pymupdf
"""

import argparse
import os
import shutil
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path

import anthropic

from editor_utils import edit_json_in_editor
from game_sheet_core import (
    SUPPORTED_EXTENSIONS,
    add_division,
    compute_ot_result,
    extract_game_sheet,
    init_db,
    insert_game,
    load_file_as_content_block,
    resolve_winner,
)

try:
    import pymupdf as fitz  # used only to render a local preview image, no API calls
except ImportError:
    fitz = None


def open_preview_window(path: Path) -> tk.Tk | None:
    """Render the sheet's first page locally (via PyMuPDF) and show it in a plain
    Tk window so it can be visually compared against the extracted data. Returns
    None (and prints a warning once) if PyMuPDF isn't installed."""
    if fitz is None:
        print("  (Install pymupdf for a visual preview window: pip install pymupdf)")
        return None

    doc = fitz.open(str(path))
    page = doc.load_page(0)
    pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
    png_bytes = pix.tobytes("png")
    doc.close()

    root = tk.Tk()
    root.title(f"Preview: {path.name}")
    image = tk.PhotoImage(data=png_bytes)

    max_dim = 1000
    if image.width() > max_dim or image.height() > max_dim:
        factor = max(image.width() // max_dim, image.height() // max_dim) + 1
        image = image.subsample(factor, factor)

    label = tk.Label(root, image=image)
    label.image = image  # keep a reference so it isn't garbage-collected
    label.pack()
    root.update()
    return root


def close_preview_window(window: tk.Tk | None):
    if window is not None:
        window.destroy()


def print_summary(data: dict, path: Path):
    print(f"\n--- Extracted from {path.name} ---")
    print(f"  Date: {data.get('game_date')}   Division: {data.get('division')}")
    print(f"  Home: {data.get('home_team')} ({data.get('home_color')}) — "
          f"Final: {data.get('home_final_score')}")
    print(f"  Away: {data.get('away_team')} ({data.get('away_color')}) — "
          f"Final: {data.get('away_final_score')}")
    winner = resolve_winner(data)
    ot_winner, ot_loser = compute_ot_result(data)
    if winner == "tie":
        print("  Winner: Tie")
    elif winner == "home":
        print(f"  Winner: {data.get('home_team')}" + (" (shootout)" if ot_winner else ""))
    elif winner == "away":
        print(f"  Winner: {data.get('away_team')}" + (" (shootout)" if ot_winner else ""))
    if ot_winner:
        loser_team = data.get("home_team") if ot_loser == "home" else data.get("away_team")
        print(f"  OT Loser (shootout-loss point): {loser_team}")
    print(f"  Goals: {len(data.get('goals', []))}   "
          f"Penalties: {len(data.get('penalties', []))}   "
          f"Shootout attempts: {len(data.get('shootout_attempts', []))}")
    if data.get("goals"):
        print("  Goal detail:")
        for g in data["goals"]:
            print(f"    [{g['side']}] #{g.get('scorer_number')} "
                  f"(assists: {g.get('assist1_number')}, {g.get('assist2_number')}) "
                  f"P{g.get('period')} @ {g.get('time')}")
    if data.get("penalties"):
        print("  Penalty detail:")
        for p in data["penalties"]:
            print(f"    [{p['side']}] #{p.get('player_number')} — {p.get('penalty_type')} "
                  f"P{p.get('period')} @ {p.get('time')}")
    if data.get("shootout_attempts"):
        print("  Shootout detail:")
        for s in data["shootout_attempts"]:
            mark = "GOAL" if s.get("scored") else "miss"
            print(f"    [{s['side']}] Round {s.get('round')} #{s.get('player_number')} — {mark}")


def review_and_edit(data: dict, path: Path) -> dict:
    """Simple interactive review loop: show the JSON, let the user accept or hand-edit."""
    while True:
        print_summary(data, path)
        choice = input("\nAccept this data? [y]es / [e]dit in text editor / [s]kip this file: ").strip().lower()
        if choice in ("y", "yes", ""):
            return data
        elif choice in ("s", "skip"):
            return None
        elif choice in ("e", "edit"):
            edited = edit_json_in_editor(data, file_prefix=path.stem)
            if edited is not None:
                data = edited
        else:
            print("Please enter y, e, or s.")


def gather_files(args) -> list[Path]:
    files = [Path(f) for f in args.files]

    if args.source_dir:
        source_dir = Path(args.source_dir)
        if not source_dir.is_dir():
            sys.exit(f"Error: source directory not found: {source_dir}")
        discovered = sorted(
            p for p in source_dir.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        )
        files.extend(discovered)

    return files


def main():
    parser = argparse.ArgumentParser(description="Extract ball hockey game sheets into a SQLite DB.")
    parser.add_argument("files", nargs="*", help="Game sheet PDF/image files to process")
    parser.add_argument("--source-dir", help="Batch-process every sheet found in this directory")
    parser.add_argument("--processed-dir",
                         help="Where to move successfully submitted files "
                              "(default: <source-dir>/processed, or ./processed)")
    parser.add_argument("--db", default="hockey.db", help="Path to SQLite database file (default: hockey.db)")
    parser.add_argument("--no-review", action="store_true",
                         help="Skip interactive review and insert extracted data directly")
    parser.add_argument("--no-preview", action="store_true",
                         help="Don't open a preview window for each sheet")
    parser.add_argument("--year", type=int, default=datetime.now().year,
                         help="Season year these sheets belong to (default: current year)")
    parser.add_argument("--season", default="Summer",
                         help="Season name these sheets belong to (default: Summer)")
    args = parser.parse_args()

    files = gather_files(args)
    if not files:
        parser.error("no files given — pass file paths and/or --source-dir")

    processed_dir = Path(args.processed_dir) if args.processed_dir else (
        Path(args.source_dir) / "processed" if args.source_dir else Path("processed")
    )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("Error: set the ANTHROPIC_API_KEY environment variable first.")

    client = anthropic.Anthropic(api_key=api_key)
    conn = init_db(args.db)
    # Anchors the year/season for every sheet processed this run; each
    # sheet's own division (age group) resolves to its own division under
    # that same year/season, auto-creating it if needed.
    working_division_id = add_division(conn, args.year, args.season, "Penguin")

    for path in files:
        if not path.exists():
            print(f"Skipping {path}: file not found.")
            continue

        print(f"\nProcessing {path.name} ...")
        preview = None if args.no_preview else open_preview_window(path)
        try:
            try:
                content_block = load_file_as_content_block(path)
                data = extract_game_sheet(client, content_block)
            except Exception as e:
                print(f"  Failed to extract {path.name}: {e}")
                continue

            if not args.no_review:
                data = review_and_edit(data, path)
                if data is None:
                    print(f"  Skipped {path.name}.")
                    continue

            game_id, already_existed = insert_game(
                conn, data, source_file=path.name, working_division_id=working_division_id
            )
            if already_existed:
                print(f"  (Game already existed in DB as id={game_id} — stats were re-inserted;"
                      f" delete old rows first if re-processing.)")
            print(f"  Stored as game_id={game_id} in {args.db}")

            processed_dir.mkdir(parents=True, exist_ok=True)
            dest = processed_dir / path.name
            shutil.move(str(path), str(dest))
            print(f"  Moved to {dest}")
        finally:
            close_preview_window(preview)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
