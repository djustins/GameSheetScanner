#!/usr/bin/env python3
"""
editor_utils.py

Shared helper for popping a text editor open on a JSON blob and blocking
until it's closed. Used by process_game_sheet.py (initial review) and
edit_game.py (correcting a game already stored in the database).
"""

import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

# Editors that don't block by default (they hand off to a running instance and
# return immediately) need an explicit "wait for close" flag added.
EDITOR_WAIT_FLAGS = {
    "code": "--wait",
    "code-insiders": "--wait",
    "codium": "--wait",
    "subl": "--wait",
    "sublime_text": "--wait",
    "atom": "--wait",
}


def editor_command(target: Path) -> list[str]:
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "notepad"
    parts = shlex.split(editor, posix=(os.name != "nt"))
    basename = Path(parts[0]).stem.lower()
    wait_flag = EDITOR_WAIT_FLAGS.get(basename)
    if wait_flag and wait_flag not in parts:
        parts.append(wait_flag)
    parts.append(str(target))
    return parts


def edit_json_in_editor(data: dict, file_prefix: str = "edit") -> dict | None:
    """Write data to a temp JSON file, open it in a text editor, and block until
    the editor is closed. Returns the parsed result, or None on invalid JSON."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix=f"{file_prefix}_", delete=False, encoding="utf-8"
    ) as f:
        json.dump(data, f, indent=2)
        tmp_path = Path(f.name)

    try:
        cmd = editor_command(tmp_path)
        print(f"  Opening {tmp_path.name} in {cmd[0]} — save and close the editor to continue...")
        subprocess.run(cmd)
        text = tmp_path.read_text(encoding="utf-8")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            print(f"Invalid JSON ({e}); try again.")
            return None
    finally:
        tmp_path.unlink(missing_ok=True)
