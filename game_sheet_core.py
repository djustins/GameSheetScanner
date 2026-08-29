#!/usr/bin/env python3
"""
game_sheet_core.py

Framework-agnostic core for the Team Pittsburgh Ball Hockey game sheet
pipeline: calling Claude for handwriting extraction, computing the game
winner, splitting multi-page PDFs, and reading/writing the SQLite database.

Nothing here depends on a CLI (argparse/input/print), a GUI (tkinter), or a
web framework (Streamlit/Flask) — it's the shared logic that every front end
(terminal scripts today, a Streamlit app now, a Flask app later) builds on.
"""

import base64
import difflib
import functools
import json
import mimetypes
import sqlite3
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import anthropic
from pypdf import PdfReader, PdfWriter

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
MODEL = "claude-sonnet-4-6"
SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif"}

# Canonical age groups and their category, independent of season/year.
AGE_GROUPS = {
    "Chipmunk": "U8",
    "Penguin": "U10",
    "Beaver": "U12",
    "Cadet": "U15",
    "Super Cadet": "U20",
    "Freshman": "U20",
}

EXTRACTION_PROMPT = """You are transcribing a handwritten "Team Pittsburgh Ball Hockey Game Sheet".

Read the image carefully and return ONLY a JSON object (no markdown fences, no commentary)
matching this exact structure:

{
  "game_date": "string as written, e.g. 7/14/26",
  "division": "string",
  "home_team": "string",
  "home_color": "string",
  "home_final_score": integer or null,
  "away_team": "string",
  "away_color": "string",
  "away_final_score": integer or null,
  "goals": [
    {"side": "home"|"away", "scorer_number": "string", "assist1_number": "string or null",
     "assist2_number": "string or null", "period": "string or null", "time": "string or null"}
  ],
  "penalties": [
    {"side": "home"|"away", "player_number": "string", "penalty_type": "string",
     "period": "string or null", "time": "string or null"}
  ],
  "shootout_attempts": [
    {"side": "home"|"away", "round": integer, "player_number": "string", "scored": true|false}
  ]
}

Rules:
- Only include rows that actually have handwritten data. Skip blank rows entirely.
- In the Shootout section, a CIRCLED player number means that attempt SCORED (scored: true).
  An un-circled number in that grid means the attempt did NOT score (scored: false).
- Player numbers, scores, and times should be transcribed exactly as written, digit by digit.
  If a character is truly illegible, use "?" for that field rather than guessing.
- "home_final_score" and "away_final_score" come from the "Final Score" boxes at the top.
- Return valid JSON only.
"""

# Identity fields (team names, player names, division) are stored lowercase so
# handwriting-recognition case variants ("BLUes" vs "Blues") are always the
# same identity instead of silently becoming a second team/player. They're
# title-cased back for display — see normalize_text()/display_text() below.


def normalize_text(s: str | None) -> str | None:
    """Storage form for an identity field: trimmed and lowercased."""
    if s is None:
        return None
    s = s.strip()
    return s.lower() if s else s


def display_text(s: str | None) -> str | None:
    """Presentation form for an identity field: title-cased for display."""
    return s.title() if s else s


# Game dates are stored as ISO 'YYYY-MM-DD' so they sort/compare correctly
# (a plain "7/14/26" string doesn't), and shown back as M/D/YYYY.
_DATE_INPUT_FORMATS = ["%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d", "%m-%d-%y", "%m-%d-%Y"]


def normalize_date(s: str | None) -> str | None:
    """Storage form for game_date: ISO 'YYYY-MM-DD'. Falls back to the
    trimmed original string if it doesn't match a recognized format, rather
    than losing/corrupting unusual handwriting-extraction results."""
    if not s:
        return s
    s = s.strip()
    for fmt in _DATE_INPUT_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def display_date(s: str | None) -> str | None:
    """Presentation form for game_date: zero-padded MM/DD/YY (e.g.
    "08/29/26"). Passes through unchanged if it isn't in the ISO storage
    format (e.g. an unparsed fallback)."""
    if not s:
        return s
    try:
        d = datetime.strptime(s, "%Y-%m-%d")
        return d.strftime("%m/%d/%y")
    except ValueError:
        return s


def _symmetric_ratio(a: str, b: str) -> float:
    """difflib's SequenceMatcher.ratio() is order-dependent in edge cases —
    e.g. "perun" vs "penguin" scores 0.5 one way and 0.67 the other — because
    its matching-block search seeds from whichever string is passed as seq2.
    Taking the max of both orderings gives a stable similarity score."""
    return max(
        difflib.SequenceMatcher(None, a, b).ratio(),
        difflib.SequenceMatcher(None, b, a).ratio(),
    )


def _best_fuzzy_match(value: str, candidates: list[str], cutoff: float = 0.6) -> str | None:
    """The candidate closest to value by symmetric ratio, or None if nothing
    clears cutoff."""
    scored = [(c, _symmetric_ratio(value.lower(), c.lower())) for c in candidates]
    best = max(scored, key=lambda cs: cs[1], default=None)
    return best[0] if best and best[1] >= cutoff else None


def match_age_group(value: str | None) -> str | None:
    """Best-effort match of free text (extraction/typos/case) to a known age
    group name from AGE_GROUPS, e.g. "Pegin" -> "Penguin". Returns None if
    nothing is a close enough match — callers should keep the original text
    in that case rather than lose it."""
    if not value or not value.strip():
        return None
    value = value.strip()
    names = list(AGE_GROUPS)
    exact = next((n for n in names if n.lower() == value.lower()), None)
    if exact:
        return exact
    return _best_fuzzy_match(value, names)


def normalize_division(value: str | None) -> str | None:
    """Storage form for the division field: auto-corrected to the closest
    known age group (e.g. "Pegin" -> "penguin") when there's a confident
    match, else just lowercased/trimmed like any other identity field so
    unrecognized text isn't lost."""
    matched = match_age_group(value)
    return normalize_text(matched) if matched else normalize_text(value)


def match_team_name(conn: sqlite3.Connection, division_id: int, value: str | None) -> str | None:
    """Best-effort match of free text (extraction/typos) to a team already in
    this division, e.g. "Avachale" -> "Avalanche". Returns None if there's no
    confident match (including when this division has no teams yet)."""
    if not value or not value.strip():
        return None
    value = value.strip()
    existing = [
        row[0] for row in conn.execute(
            "SELECT name FROM teams WHERE division_id = ?", (division_id,)
        ).fetchall()
    ]
    if not existing:
        return None
    exact = next((n for n in existing if n.lower() == value.lower()), None)
    if exact:
        return display_text(exact)
    match = _best_fuzzy_match(value, existing)
    return display_text(match) if match else None


def normalize_team_name(conn: sqlite3.Connection, division_id: int, value: str | None) -> str | None:
    """Storage form for a team name field: auto-corrected to an existing team
    in this division (fuzzy-matched) when there's a confident match, else
    just lowercased/trimmed like any other identity field so unrecognized
    text (e.g. a genuinely new team) isn't lost."""
    matched = match_team_name(conn, division_id, value)
    return normalize_text(matched) if matched else normalize_text(value)


# ---------------------------------------------------------------------------
# Claude extraction
# ---------------------------------------------------------------------------

def guess_mime(filename: str) -> str | None:
    mime, _ = mimetypes.guess_type(filename)
    return mime


def build_content_block(data: bytes, mime: str | None) -> dict:
    """Build an Anthropic API content block (image or document) from raw bytes."""
    b64 = base64.standard_b64encode(data).decode("utf-8")
    if mime == "application/pdf":
        return {"type": "document", "source": {"type": "base64", "media_type": mime, "data": b64}}
    elif mime in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}
    else:
        raise ValueError(f"Unsupported file type (mime={mime}). Use PDF, PNG, or JPG.")


def load_file_as_content_block(path: Path) -> dict:
    """Convenience wrapper for on-disk files."""
    return build_content_block(path.read_bytes(), guess_mime(str(path)))


def extract_game_sheet(client: anthropic.Anthropic, content_block: dict) -> dict:
    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        messages=[
            {
                "role": "user",
                "content": [
                    content_block,
                    {"type": "text", "text": EXTRACTION_PROMPT},
                ],
            }
        ],
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    # Strip stray markdown fences if the model adds them despite instructions
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


# ---------------------------------------------------------------------------
# Game logic
# ---------------------------------------------------------------------------

def compute_winner(data: dict) -> str | None:
    """Determine the game winner: 'home', 'away', or 'tie'. If regulation ended
    tied and there are shootout attempts, the shootout decides it. Returns None
    if scores aren't both known yet."""
    home = data.get("home_final_score")
    away = data.get("away_final_score")
    if home is None or away is None:
        return None
    if home > away:
        return "home"
    if away > home:
        return "away"

    shootout = data.get("shootout_attempts") or []
    if not shootout:
        return "tie"
    home_so_goals = sum(1 for s in shootout if s.get("side") == "home" and s.get("scored"))
    away_so_goals = sum(1 for s in shootout if s.get("side") == "away" and s.get("scored"))
    if home_so_goals > away_so_goals:
        return "home"
    if away_so_goals > home_so_goals:
        return "away"
    return "tie"


def resolve_winner(data: dict) -> str | None:
    """The winner to actually store: an explicit override in data["winner"] takes
    precedence (e.g. a manual correction), otherwise it's computed from scores."""
    winner = data.get("winner")
    if winner in ("home", "away", "tie"):
        return winner
    return compute_winner(data)


def compute_ot_result(data: dict) -> tuple[str | None, str | None]:
    """Return (ot_winner, ot_loser) — 'home'/'away' each — if the game was decided
    by a shootout (regulation ended tied and there are shootout attempts), else
    (None, None). Used to award shootout-loss points distinctly from a regulation
    loss."""
    winner = resolve_winner(data)
    if winner not in ("home", "away"):
        return None, None
    if not data.get("shootout_attempts"):
        return None, None
    if data.get("home_final_score") != data.get("away_final_score"):
        return None, None
    loser = "away" if winner == "home" else "home"
    return winner, loser


# ---------------------------------------------------------------------------
# PDF splitting
# ---------------------------------------------------------------------------

def split_pdf_bytes(data: bytes) -> list[bytes]:
    """Split multi-page PDF bytes into single-page PDF byte blobs. Returns
    [data] unchanged if it's already a single page."""
    reader = PdfReader(BytesIO(data))
    if len(reader.pages) <= 1:
        return [data]

    pages = []
    for page in reader.pages:
        writer = PdfWriter()
        writer.add_page(page)
        buf = BytesIO()
        writer.write(buf)
        pages.append(buf.getvalue())
    return pages


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA_PATH.read_text())
    _migrate(conn)
    conn.commit()
    return conn


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    """A durable (survives a restart) app-level preference, e.g. the
    last-selected Working Division — stored in this database file itself
    rather than session state, which resets every new browser session."""
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str):
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def _migrate(conn: sqlite3.Connection):
    """Add columns to a database created before they existed in schema.sql,
    make every game/team belong to a division (fully isolating each
    division's games/teams/rosters from every other), and make sure every
    team seen in games has a row in the teams table.

    Foreign keys are switched off for the duration: several steps here
    rebuild a table (rename-create-copy-drop, since SQLite can't ALTER a
    UNIQUE constraint or add a NOT NULL FK column in place), and SQLite's
    DROP TABLE looks up a to-be-dropped table's own FK targets even with
    nothing depending on it — if an earlier interrupted run (e.g. a
    concurrent process reloading this file mid-edit) left a stale
    intermediate table referenced elsewhere, that lookup fails with a
    misleading "no such table" unless enforcement is off during cleanup."""
    conn.execute("PRAGMA foreign_keys = OFF")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(games)")}
    if "winner" not in cols:
        conn.execute("ALTER TABLE games ADD COLUMN winner TEXT")
    if "ot_winner" not in cols:
        conn.execute("ALTER TABLE games ADD COLUMN ot_winner TEXT")
    if "ot_loser" not in cols:
        conn.execute("ALTER TABLE games ADD COLUMN ot_loser TEXT")
    if "division_id" not in cols:
        conn.execute("ALTER TABLE games ADD COLUMN division_id INTEGER REFERENCES divisions(id)")

    division_cols = {row[1] for row in conn.execute("PRAGMA table_info(divisions)")}
    if "deleted_at" not in division_cols:
        conn.execute("ALTER TABLE divisions ADD COLUMN deleted_at TEXT")

    _migrate_players_split(conn)

    # Seed the division the existing (pre-division-scoping) data belongs to,
    # and use its id to backfill anything that predates division scoping.
    default_division_id = add_division(conn, 2026, "Summer", "Penguin", "U10")

    _migrate_teams_division_id(conn, default_division_id)
    _fix_dangling_fks(conn)
    _merge_duplicate_teams(conn)
    _normalize_identity_case(conn)
    _normalize_dates(conn)
    _fix_division_typos(conn)

    conn.execute("UPDATE games SET division_id = ? WHERE division_id IS NULL", (default_division_id,))

    # Auto-register any team appearing in games but not yet in teams, scoped
    # to that same game's division.
    conn.execute(
        """INSERT OR IGNORE INTO teams (division_id, name)
           SELECT division_id, home_team FROM games
           WHERE division_id IS NOT NULL AND home_team IS NOT NULL AND home_team <> ''
           UNION
           SELECT division_id, away_team FROM games
           WHERE division_id IS NOT NULL AND away_team IS NOT NULL AND away_team <> ''"""
    )

    # Defensive cleanup: roster_entries referencing a team_id that no longer
    # exists (e.g. a team row recreated with a new id after being rebuilt)
    # would otherwise sit invisible to every join and never get cleaned up.
    conn.execute("DELETE FROM roster_entries WHERE team_id NOT IN (SELECT id FROM teams)")

    # Recycle bin: divisions soft-deleted more than 30 days ago are purged
    # for good every time the app connects.
    purge_expired_divisions(conn)

    # Backfill winner/ot_winner/ot_loser for rows inserted before those columns
    # existed — otherwise they'd silently drop out of standings.
    stale = conn.execute(
        "SELECT id, home_final_score, away_final_score FROM games WHERE winner IS NULL"
    ).fetchall()
    for game_id, home_score, away_score in stale:
        shootout = conn.execute(
            "SELECT side, scored FROM shootout_attempts WHERE game_id = ?", (game_id,)
        ).fetchall()
        data = {
            "home_final_score": home_score, "away_final_score": away_score,
            "shootout_attempts": [{"side": s, "scored": bool(sc)} for s, sc in shootout],
        }
        winner = compute_winner(data)
        if winner is None:
            continue
        ot_winner, ot_loser = compute_ot_result(data)
        conn.execute(
            "UPDATE games SET winner = ?, ot_winner = ?, ot_loser = ? WHERE id = ?",
            (winner, ot_winner, ot_loser, game_id),
        )

    # Backfill roster entries for players who appeared in games scanned
    # before auto-registration existed.
    for game_id, division_id in conn.execute("SELECT id, division_id FROM games").fetchall():
        game_data, _ = load_game(conn, game_id)
        if game_data and division_id:
            register_players_from_game(conn, game_data, division_id)

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def _migrate_players_split(conn: sqlite3.Connection):
    """Before division-scoping and the global player-profile table existed,
    "players" WAS the per-team jersey-number roster table (team_id, number,
    name). schema.sql's CREATE TABLE IF NOT EXISTS leaves that old table
    alone (so "players" ends up holding old roster data under a name now
    meant for global profiles) while creating an empty new roster_entries —
    detect the old shape and rename/rebuild so existing rosters land in
    roster_entries and "players" becomes the real (empty, to be populated)
    global profile table."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(players)")}
    if "team_id" not in cols:
        return
    conn.execute("DROP TABLE IF EXISTS roster_entries")
    conn.execute("ALTER TABLE players RENAME TO roster_entries")
    conn.execute(
        """CREATE TABLE players (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            name                 TEXT NOT NULL,
            birth_date           TEXT,
            current_division_id  INTEGER REFERENCES divisions(id),
            contact_first_name   TEXT,
            contact_last_name    TEXT,
            contact_phone        TEXT,
            contact_email        TEXT,
            deleted_at           TEXT,
            created_at           TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    re_cols = {row[1] for row in conn.execute("PRAGMA table_info(roster_entries)")}
    if "player_id" not in re_cols:
        conn.execute("ALTER TABLE roster_entries ADD COLUMN player_id INTEGER REFERENCES players(id)")


def _migrate_teams_division_id(conn: sqlite3.Connection, default_division_id: int):
    """One-time rebuild adding teams.division_id (SQLite can't ALTER a UNIQUE
    constraint in place), for a database created before teams were scoped per
    division. Every existing team is assigned to the division the existing
    data belongs to, *keeping its original id* so roster_entries.team_id
    still points at the right team afterwards.

    Also finishes this same rebuild if it was left half-done: a "teams_old"
    table still present means an earlier run renamed teams away, copied its
    rows into the new teams table, but crashed (or was interrupted by a
    concurrent process reloading mid-refactor) before dropping teams_old —
    in which case teams may already have the new shape, but teams_old's
    original rows (and ids) haven't actually been copied in and dropped yet."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(teams)")}
    has_teams_old = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='teams_old'"
    ).fetchone() is not None

    if "division_id" not in cols:
        conn.execute("ALTER TABLE teams RENAME TO teams_old")
        conn.execute(
            """CREATE TABLE teams (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                division_id INTEGER NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
                name        TEXT NOT NULL,
                UNIQUE(division_id, name)
            )"""
        )
        has_teams_old = True

    if has_teams_old:
        conn.execute(
            "INSERT OR IGNORE INTO teams (id, division_id, name) SELECT id, ?, name FROM teams_old",
            (default_division_id,),
        )
        conn.execute("DROP TABLE teams_old")


def _fix_dangling_fks(conn: sqlite3.Connection):
    """Repair any table whose foreign key is left pointing at a stale
    intermediate table name (e.g. "teams_old") from an earlier table rebuild
    elsewhere in this migration. SQLite's ALTER TABLE RENAME rewrites every
    *other* table's FK text to follow along whenever the table it points at
    is renamed — so renaming teams -> teams_old (mid-rebuild) silently
    rewrites roster_entries/team_coaches/evaluations' FKs to say
    "teams_old" too, and if that rebuild is then interrupted (e.g. a
    concurrent process reloading this file mid-refactor) before the rename
    is undone, those tables are left referencing a name that's since been
    dropped — which then fails every future INSERT into them with a
    misleading "FOREIGN KEY constraint failed". Each affected table is
    rebuilt from its correct (schema.sql) definition, dropping only rows
    whose reference genuinely no longer resolves."""
    repairs = {
        "roster_entries": (
            """CREATE TABLE roster_entries (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id    INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                number     TEXT NOT NULL,
                name       TEXT NOT NULL,
                player_id  INTEGER REFERENCES players(id) ON DELETE SET NULL,
                UNIQUE(team_id, number)
            )""",
            "id, team_id, number, name, player_id",
            "team_id IN (SELECT id FROM teams)",
        ),
        "team_coaches": (
            """CREATE TABLE team_coaches (
                team_id   INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                coach_id  INTEGER NOT NULL REFERENCES coaches(id) ON DELETE CASCADE,
                PRIMARY KEY (team_id, coach_id)
            )""",
            "team_id, coach_id",
            "team_id IN (SELECT id FROM teams) AND coach_id IN (SELECT id FROM coaches)",
        ),
        "evaluations": (
            """CREATE TABLE evaluations (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id    INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                division_id  INTEGER NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
                team_id      INTEGER REFERENCES teams(id) ON DELETE SET NULL,
                grade        TEXT,
                created_at   TEXT DEFAULT CURRENT_TIMESTAMP
            )""",
            "id, player_id, division_id, team_id, grade, created_at",
            "player_id IN (SELECT id FROM players) AND division_id IN (SELECT id FROM divisions)",
        ),
    }
    for table, (create_sql, columns, valid_where) in repairs.items():
        fks = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        existing_tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if all(fk[2] in existing_tables for fk in fks):
            continue
        conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old_fk")
        conn.execute(create_sql)
        conn.execute(
            f"INSERT INTO {table} ({columns}) SELECT {columns} FROM {table}_old_fk WHERE {valid_where}"
        )
        conn.execute(f"DROP TABLE {table}_old_fk")


def _merge_duplicate_teams(conn: sqlite3.Connection):
    """If case variants (e.g. "Blues" and "BLUes") already created separate
    team rows *within the same division* before storage was normalized, merge
    them into one canonical (lowest id) team so lowercasing teams.name below
    doesn't hit its UNIQUE constraint. Players under a merged-away duplicate
    move to the canonical team; a player number that already exists there is
    dropped rather than kept twice. The same name in a *different* division
    is a different team and is left alone."""
    rows = conn.execute("SELECT id, division_id, name FROM teams ORDER BY id").fetchall()
    groups: dict[tuple[int, str], list[int]] = {}
    for team_id, division_id, name in rows:
        groups.setdefault((division_id, (name or "").strip().lower()), []).append(team_id)

    for ids in groups.values():
        if len(ids) <= 1:
            continue
        canonical_id, *duplicate_ids = ids
        for dup_id in duplicate_ids:
            for entry_id, number in conn.execute(
                "SELECT id, number FROM roster_entries WHERE team_id = ?", (dup_id,)
            ).fetchall():
                clash = conn.execute(
                    "SELECT 1 FROM roster_entries WHERE team_id = ? AND number = ?", (canonical_id, number),
                ).fetchone()
                if clash:
                    conn.execute("DELETE FROM roster_entries WHERE id = ?", (entry_id,))
                else:
                    conn.execute("UPDATE roster_entries SET team_id = ? WHERE id = ?", (canonical_id, entry_id))
            conn.execute("DELETE FROM teams WHERE id = ?", (dup_id,))


def _normalize_identity_case(conn: sqlite3.Connection):
    """One-time (idempotent) lowercase normalization of identity fields
    already in the database, for rows written before this existed."""
    conn.execute("UPDATE games SET home_team = LOWER(TRIM(home_team)) WHERE home_team IS NOT NULL")
    conn.execute("UPDATE games SET away_team = LOWER(TRIM(away_team)) WHERE away_team IS NOT NULL")
    conn.execute("UPDATE games SET division = LOWER(TRIM(division)) WHERE division IS NOT NULL")
    conn.execute("UPDATE teams SET name = LOWER(TRIM(name)) WHERE name IS NOT NULL")
    conn.execute("UPDATE roster_entries SET name = LOWER(TRIM(name)) WHERE name IS NOT NULL")


def _normalize_dates(conn: sqlite3.Connection):
    """One-time (idempotent) conversion of existing game_date values to the
    ISO storage format, for rows written before this existed."""
    rows = conn.execute("SELECT id, game_date FROM games WHERE game_date IS NOT NULL").fetchall()
    for game_id, game_date in rows:
        normalized = normalize_date(game_date)
        if normalized != game_date:
            conn.execute("UPDATE games SET game_date = ? WHERE id = ?", (normalized, game_id))


def _fix_division_typos(conn: sqlite3.Connection):
    """One-time (idempotent) auto-correction of existing games.division
    values against the known age group list, for rows written before this
    existed (or before the corresponding form field validated it)."""
    rows = conn.execute("SELECT id, division FROM games WHERE division IS NOT NULL").fetchall()
    for row_id, division in rows:
        fixed = normalize_division(division)
        if fixed != division:
            conn.execute("UPDATE games SET division = ? WHERE id = ?", (fixed, row_id))


def _collect_numbers_by_side(data: dict) -> dict[str, set[str]]:
    """Every jersey number appearing in this game's goals/penalties/shootout
    attempts, grouped by 'home'/'away'."""
    numbers: dict[str, set[str]] = {"home": set(), "away": set()}
    for g in data.get("goals", []):
        side = g.get("side")
        if side not in numbers:
            continue
        for key in ("scorer_number", "assist1_number", "assist2_number"):
            n = (g.get(key) or "").strip()
            if n and n != "?":
                numbers[side].add(n)
    for p in data.get("penalties", []):
        side = p.get("side")
        n = (p.get("player_number") or "").strip()
        if side in numbers and n and n != "?":
            numbers[side].add(n)
    for s in data.get("shootout_attempts", []):
        side = s.get("side")
        n = (s.get("player_number") or "").strip()
        if side in numbers and n and n != "?":
            numbers[side].add(n)
    return numbers


def register_players_from_game(conn: sqlite3.Connection, data: dict, division_id: int):
    """Auto-add a roster entry (placeholder name "#<number>") for any jersey
    number that shows up in this game's stats but isn't on the roster yet.
    Existing roster entries (and their names) are left untouched. This is a
    roster_entries row only — it isn't linked to a global player profile
    until someone identifies who it is (see link_roster_entry_to_player)."""
    numbers = _collect_numbers_by_side(data)
    for side, team_field in (("home", "home_team"), ("away", "away_team")):
        team_name = data.get(team_field)
        if not team_name or not numbers[side]:
            continue
        team_id = add_team(conn, division_id, team_name)
        for number in numbers[side]:
            conn.execute(
                "INSERT OR IGNORE INTO roster_entries (team_id, number, name) VALUES (?, ?, ?)",
                (team_id, number, f"#{number}"),
            )
    conn.commit()


def insert_stat_rows(conn: sqlite3.Connection, game_id: int, data: dict):
    """Insert the goals/penalties/shootout_attempts rows for a game. Does not
    commit or touch the games row itself."""
    for g in data.get("goals", []):
        conn.execute(
            """INSERT INTO goals (game_id, side, scorer_number, assist1_number,
                                   assist2_number, period, time)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (game_id, g.get("side"), g.get("scorer_number"), g.get("assist1_number"),
             g.get("assist2_number"), g.get("period"), g.get("time")),
        )

    for p in data.get("penalties", []):
        conn.execute(
            """INSERT INTO penalties (game_id, side, player_number, penalty_type, period, time)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (game_id, p.get("side"), p.get("player_number"), p.get("penalty_type"),
             p.get("period"), p.get("time")),
        )

    for s in data.get("shootout_attempts", []):
        conn.execute(
            """INSERT INTO shootout_attempts (game_id, side, round, player_number, scored)
               VALUES (?, ?, ?, ?, ?)""",
            (game_id, s.get("side"), s.get("round"), s.get("player_number"),
             1 if s.get("scored") else 0),
        )


def insert_game(conn: sqlite3.Connection, data: dict, source_file: str, working_division_id: int) -> tuple[int, bool]:
    """Insert a game and its stats. Returns (game_id, already_existed).
    Raises ValueError if the game resolves to a tie — this league always
    resolves a regulation tie with a shootout, so a stored game must have a
    winner. working_division_id anchors the game's year/season; its own
    division/age-group text (which may differ, e.g. a Beaver sheet scanned
    while Penguin is the working division) resolves to that year+season's
    matching division, auto-creating it if needed."""
    division_text = normalize_division(data.get("division"))
    division_id = resolve_division_id(conn, working_division_id, division_text)
    data = {
        **data,
        "game_date": normalize_date(data.get("game_date")),
        "home_team": normalize_team_name(conn, division_id, data.get("home_team")),
        "away_team": normalize_team_name(conn, division_id, data.get("away_team")),
        "division": division_text,
    }
    went_to_shootout = 1 if data.get("shootout_attempts") else 0
    winner = resolve_winner(data)
    if winner == "tie":
        raise ValueError("Games can't end in a tie — add shootout results or correct the score.")
    ot_winner, ot_loser = compute_ot_result(data)
    cur = conn.execute(
        """INSERT OR IGNORE INTO games
           (game_date, division, division_id, home_team, home_color, home_final_score,
            away_team, away_color, away_final_score, went_to_shootout, winner,
            ot_winner, ot_loser, source_file)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data.get("game_date"), data.get("division"), division_id,
            data.get("home_team"), data.get("home_color"), data.get("home_final_score"),
            data.get("away_team"), data.get("away_color"), data.get("away_final_score"),
            went_to_shootout, winner, ot_winner, ot_loser, source_file,
        ),
    )
    already_existed = cur.lastrowid == 0 or conn.total_changes == 0
    if already_existed:
        row = conn.execute(
            "SELECT id FROM games WHERE game_date=? AND home_team=? AND away_team=? AND source_file=?",
            (data.get("game_date"), data.get("home_team"), data.get("away_team"), source_file),
        ).fetchone()
        game_id = row[0]
    else:
        game_id = cur.lastrowid

    insert_stat_rows(conn, game_id, data)
    conn.commit()
    register_players_from_game(conn, data, division_id)
    return game_id, already_existed


def list_games(conn: sqlite3.Connection, division_id: int) -> list[dict]:
    cols = ["id", "game_date", "division", "home_team", "home_final_score",
            "away_team", "away_final_score", "winner", "ot_winner", "ot_loser", "source_file"]
    rows = conn.execute(
        f"SELECT {', '.join(cols)} FROM games WHERE division_id = ? ORDER BY id", (division_id,)
    ).fetchall()
    games = [dict(zip(cols, row)) for row in rows]
    for g in games:
        g["game_date"] = display_date(g["game_date"])
        g["division"] = display_text(g["division"])
        g["home_team"] = display_text(g["home_team"])
        g["away_team"] = display_text(g["away_team"])
    return games


def find_game_by_source_file(conn: sqlite3.Connection, source_file: str) -> dict | None:
    """Return the existing game whose source_file matches, or None. Used to
    warn before re-processing a file that's already been imported."""
    cols = ["id", "game_date", "division", "home_team", "away_team",
            "home_final_score", "away_final_score"]
    row = conn.execute(
        f"SELECT {', '.join(cols)} FROM games WHERE source_file = ?", (source_file,)
    ).fetchone()
    if row is None:
        return None
    g = dict(zip(cols, row))
    g["game_date"] = display_date(g["game_date"])
    g["division"] = display_text(g["division"])
    g["home_team"] = display_text(g["home_team"])
    g["away_team"] = display_text(g["away_team"])
    return g


def load_game(conn: sqlite3.Connection, game_id: int) -> tuple[dict | None, str | None]:
    """Return (data, source_file) for the given game id, matching the same
    structure extract_game_sheet() produces (plus the stored "winner"), or
    (None, None) if not found."""
    row = conn.execute(
        """SELECT game_date, division, home_team, home_color, home_final_score,
                  away_team, away_color, away_final_score, winner, source_file
           FROM games WHERE id = ?""",
        (game_id,),
    ).fetchone()
    if row is None:
        return None, None

    (game_date, division, home_team, home_color, home_final_score,
     away_team, away_color, away_final_score, winner, source_file) = row

    goals = [
        dict(zip(("side", "scorer_number", "assist1_number", "assist2_number", "period", "time"), r))
        for r in conn.execute(
            """SELECT side, scorer_number, assist1_number, assist2_number, period, time
               FROM goals WHERE game_id = ? ORDER BY id""",
            (game_id,),
        ).fetchall()
    ]
    penalties = [
        dict(zip(("side", "player_number", "penalty_type", "period", "time"), r))
        for r in conn.execute(
            """SELECT side, player_number, penalty_type, period, time
               FROM penalties WHERE game_id = ? ORDER BY id""",
            (game_id,),
        ).fetchall()
    ]
    shootout_attempts = [
        {"side": side, "round": round_, "player_number": player_number, "scored": bool(scored)}
        for side, round_, player_number, scored in conn.execute(
            """SELECT side, round, player_number, scored
               FROM shootout_attempts WHERE game_id = ? ORDER BY round, id""",
            (game_id,),
        ).fetchall()
    ]

    data = {
        "game_date": display_date(game_date),
        "division": display_text(division),
        "home_team": display_text(home_team),
        "home_color": home_color,
        "home_final_score": home_final_score,
        "away_team": display_text(away_team),
        "away_color": away_color,
        "away_final_score": away_final_score,
        "winner": winner,
        "goals": goals,
        "penalties": penalties,
        "shootout_attempts": shootout_attempts,
    }
    return data, source_file


def update_game(conn: sqlite3.Connection, game_id: int, data: dict, working_division_id: int):
    """Raises ValueError if the game resolves to a tie — see insert_game()."""
    division_text = normalize_division(data.get("division"))
    division_id = resolve_division_id(conn, working_division_id, division_text)
    data = {
        **data,
        "game_date": normalize_date(data.get("game_date")),
        "home_team": normalize_team_name(conn, division_id, data.get("home_team")),
        "away_team": normalize_team_name(conn, division_id, data.get("away_team")),
        "division": division_text,
    }
    went_to_shootout = 1 if data.get("shootout_attempts") else 0
    winner = resolve_winner(data)
    if winner == "tie":
        raise ValueError("Games can't end in a tie — add shootout results or correct the score.")
    ot_winner, ot_loser = compute_ot_result(data)

    conn.execute(
        """UPDATE games SET
               game_date = ?, division = ?, division_id = ?, home_team = ?, home_color = ?,
               home_final_score = ?, away_team = ?, away_color = ?,
               away_final_score = ?, went_to_shootout = ?, winner = ?,
               ot_winner = ?, ot_loser = ?
           WHERE id = ?""",
        (
            data.get("game_date"), data.get("division"), division_id,
            data.get("home_team"), data.get("home_color"), data.get("home_final_score"),
            data.get("away_team"), data.get("away_color"), data.get("away_final_score"),
            went_to_shootout, winner, ot_winner, ot_loser, game_id,
        ),
    )

    conn.execute("DELETE FROM goals WHERE game_id = ?", (game_id,))
    conn.execute("DELETE FROM penalties WHERE game_id = ?", (game_id,))
    conn.execute("DELETE FROM shootout_attempts WHERE game_id = ?", (game_id,))
    insert_stat_rows(conn, game_id, data)

    conn.commit()
    register_players_from_game(conn, data, division_id)


# ---------------------------------------------------------------------------
# Divisions (season + year + age group)
# ---------------------------------------------------------------------------

def list_divisions(conn: sqlite3.Connection) -> list[dict]:
    """Active (not-deleted) divisions — what dropdowns and the main Divisions
    table should show. See list_deleted_divisions() for the recycle bin."""
    rows = conn.execute(
        """SELECT id, year, season, age_group, category FROM divisions
           WHERE deleted_at IS NULL
           ORDER BY year DESC, season, age_group"""
    ).fetchall()
    return [
        {"id": r[0], "year": r[1], "season": display_text(r[2]), "age_group": r[3], "category": r[4]}
        for r in rows
    ]


def add_division(conn: sqlite3.Connection, year: int, season: str, age_group: str,
                  category: str | None = None) -> int:
    season = normalize_text(season)
    age_group = match_age_group(age_group) or age_group.strip()
    category = category or AGE_GROUPS.get(age_group, "")
    conn.execute(
        "INSERT OR IGNORE INTO divisions (year, season, age_group, category) VALUES (?, ?, ?, ?)",
        (year, season, age_group, category),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM divisions WHERE year = ? AND season = ? AND age_group = ?",
        (year, season, age_group),
    ).fetchone()[0]


def resolve_division_id(conn: sqlite3.Connection, working_division_id: int, age_group: str | None) -> int:
    """Find-or-create the division that a game actually belongs to: the same
    year/season as the working division, but this game's own age group
    (which is usually the working division's age group, but may differ if
    this particular sheet is for a different age group)."""
    row = conn.execute(
        "SELECT year, season, age_group FROM divisions WHERE id = ?", (working_division_id,)
    ).fetchone()
    if row is None:
        raise ValueError("No working division selected.")
    year, season, working_age_group = row
    resolved_age_group = match_age_group(age_group) or age_group or working_age_group
    return add_division(conn, year, season, resolved_age_group)


def soft_delete_division(conn: sqlite3.Connection, division_id: int):
    """Move a division to the recycle bin — it's purged for good 30 days
    later (see purge_expired_divisions), or can be restored before then."""
    conn.execute(
        "UPDATE divisions SET deleted_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(timespec="seconds"), division_id),
    )
    conn.commit()


def restore_division(conn: sqlite3.Connection, division_id: int):
    conn.execute("UPDATE divisions SET deleted_at = NULL WHERE id = ?", (division_id,))
    conn.commit()


def list_deleted_divisions(conn: sqlite3.Connection) -> list[dict]:
    """The recycle bin: divisions soft-deleted but not yet purged, with how
    many days remain before they're gone for good."""
    rows = conn.execute(
        """SELECT id, year, season, age_group, category, deleted_at FROM divisions
           WHERE deleted_at IS NOT NULL
           ORDER BY deleted_at DESC"""
    ).fetchall()
    result = []
    for r in rows:
        deleted_at = datetime.fromisoformat(r[5])
        days_left = max(0, 30 - (datetime.utcnow() - deleted_at).days)
        result.append({
            "id": r[0], "year": r[1], "season": display_text(r[2]), "age_group": r[3],
            "category": r[4], "deleted_at": r[5], "days_left": days_left,
        })
    return result


def purge_expired_divisions(conn: sqlite3.Connection, days: int = 30):
    """Permanently delete divisions that have been in the recycle bin more
    than `days` days. Called automatically on every init_db()."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM divisions WHERE deleted_at IS NOT NULL AND deleted_at <= ?", (cutoff,))
    conn.commit()


# ---------------------------------------------------------------------------
# Team rosters
# ---------------------------------------------------------------------------

def list_teams(conn: sqlite3.Connection, division_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name FROM teams WHERE division_id = ? ORDER BY name", (division_id,)
    ).fetchall()
    return [{"id": r[0], "name": display_text(r[1])} for r in rows]


def add_team(conn: sqlite3.Connection, division_id: int, name: str) -> int:
    name = normalize_text(name)
    conn.execute(
        "INSERT OR IGNORE INTO teams (division_id, name) VALUES (?, ?)", (division_id, name)
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM teams WHERE division_id = ? AND name = ?", (division_id, name)
    ).fetchone()[0]


def list_roster(conn: sqlite3.Connection, team_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT id, number, name, player_id FROM roster_entries
           WHERE team_id = ? ORDER BY CAST(number AS INTEGER), number""",
        (team_id,),
    ).fetchall()
    return [{"id": r[0], "number": r[1], "name": display_text(r[2]), "player_id": r[3]} for r in rows]


def replace_roster(conn: sqlite3.Connection, team_id: int, entries: list[dict]):
    """Replace a team's whole roster with the given (number, name) rows. A
    row's link to a global player profile is preserved by jersey number
    match, since the roster editor doesn't expose player_id directly."""
    existing_player_ids = dict(
        conn.execute(
            "SELECT number, player_id FROM roster_entries WHERE team_id = ?", (team_id,)
        ).fetchall()
    )
    conn.execute("DELETE FROM roster_entries WHERE team_id = ?", (team_id,))
    for p in entries:
        number = (p.get("number") or "").strip()
        name = normalize_text(p.get("name")) or ""
        if not number and not name:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO roster_entries (team_id, number, name, player_id) VALUES (?, ?, ?, ?)",
            (team_id, number, name, existing_player_ids.get(number)),
        )
    conn.commit()


def link_roster_entry_to_player(conn: sqlite3.Connection, roster_entry_id: int, player_id: int):
    """Identify a roster entry (a jersey number seen on a game sheet) as a
    specific global player profile."""
    conn.execute(
        "UPDATE roster_entries SET player_id = ? WHERE id = ?", (player_id, roster_entry_id)
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Global players (identity persists across every division/season)
# ---------------------------------------------------------------------------

def list_players(conn: sqlite3.Connection, include_deleted: bool = False) -> list[dict]:
    where = "" if include_deleted else "WHERE deleted_at IS NULL"
    rows = conn.execute(
        f"""SELECT id, name, birth_date, current_division_id, contact_first_name,
                   contact_last_name, contact_phone, contact_email, deleted_at
            FROM players {where} ORDER BY name"""
    ).fetchall()
    cols = ["id", "name", "birth_date", "current_division_id", "contact_first_name",
            "contact_last_name", "contact_phone", "contact_email", "deleted_at"]
    return [dict(zip(cols, r)) for r in rows]


def get_player(conn: sqlite3.Connection, player_id: int) -> dict | None:
    players = {p["id"]: p for p in list_players(conn, include_deleted=True)}
    return players.get(player_id)


def add_player(
    conn: sqlite3.Connection, name: str, birth_date: str | None = None,
    current_division_id: int | None = None, contact_first_name: str | None = None,
    contact_last_name: str | None = None, contact_phone: str | None = None,
    contact_email: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO players
           (name, birth_date, current_division_id, contact_first_name,
            contact_last_name, contact_phone, contact_email)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (name.strip(), birth_date, current_division_id, contact_first_name,
         contact_last_name, contact_phone, contact_email),
    )
    conn.commit()
    return cur.lastrowid


def update_player(conn: sqlite3.Connection, player_id: int, **fields):
    """Update any subset of a player's profile fields, e.g.
    update_player(conn, 5, name="Alex Smith", contact_phone="412-555-0100")."""
    allowed = {
        "name", "birth_date", "current_division_id", "contact_first_name",
        "contact_last_name", "contact_phone", "contact_email",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE players SET {set_clause} WHERE id = ?", (*updates.values(), player_id)
    )
    conn.commit()


def soft_delete_player(conn: sqlite3.Connection, player_id: int):
    conn.execute(
        "UPDATE players SET deleted_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(timespec="seconds"), player_id),
    )
    conn.commit()


def restore_player(conn: sqlite3.Connection, player_id: int):
    conn.execute("UPDATE players SET deleted_at = NULL WHERE id = ?", (player_id,))
    conn.commit()


def player_division_history(conn: sqlite3.Connection, player_id: int) -> list[dict]:
    """Every division this player has a roster entry in, derived by joining
    roster_entries -> teams -> divisions rather than tracked separately, so
    it can never drift out of sync with the actual rosters."""
    rows = conn.execute(
        """SELECT DISTINCT d.id, d.year, d.season, d.age_group, d.category, t.name
           FROM roster_entries re
           JOIN teams t ON t.id = re.team_id
           JOIN divisions d ON d.id = t.division_id
           WHERE re.player_id = ?
           ORDER BY d.year DESC, d.season""",
        (player_id,),
    ).fetchall()
    return [
        {
            "division_id": r[0], "year": r[1], "season": display_text(r[2]),
            "age_group": r[3], "category": r[4], "team_name": display_text(r[5]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Coaches (global; assigned to a team, which anchors them to one division)
# ---------------------------------------------------------------------------

def list_coaches(conn: sqlite3.Connection, include_deleted: bool = False) -> list[dict]:
    where = "" if include_deleted else "WHERE deleted_at IS NULL"
    rows = conn.execute(f"SELECT id, name, deleted_at FROM coaches {where} ORDER BY name").fetchall()
    return [{"id": r[0], "name": r[1], "deleted_at": r[2]} for r in rows]


def add_coach(conn: sqlite3.Connection, name: str) -> int:
    cur = conn.execute("INSERT INTO coaches (name) VALUES (?)", (name.strip(),))
    conn.commit()
    return cur.lastrowid


def update_coach(conn: sqlite3.Connection, coach_id: int, name: str):
    conn.execute("UPDATE coaches SET name = ? WHERE id = ?", (name.strip(), coach_id))
    conn.commit()


def soft_delete_coach(conn: sqlite3.Connection, coach_id: int):
    conn.execute(
        "UPDATE coaches SET deleted_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(timespec="seconds"), coach_id),
    )
    conn.commit()


def restore_coach(conn: sqlite3.Connection, coach_id: int):
    conn.execute("UPDATE coaches SET deleted_at = NULL WHERE id = ?", (coach_id,))
    conn.commit()


def assign_coach_to_team(conn: sqlite3.Connection, team_id: int, coach_id: int):
    conn.execute(
        "INSERT OR IGNORE INTO team_coaches (team_id, coach_id) VALUES (?, ?)", (team_id, coach_id)
    )
    conn.commit()


def remove_coach_from_team(conn: sqlite3.Connection, team_id: int, coach_id: int):
    conn.execute(
        "DELETE FROM team_coaches WHERE team_id = ? AND coach_id = ?", (team_id, coach_id)
    )
    conn.commit()


def list_team_coaches(conn: sqlite3.Connection, team_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT c.id, c.name FROM team_coaches tc
           JOIN coaches c ON c.id = tc.coach_id
           WHERE tc.team_id = ? AND c.deleted_at IS NULL ORDER BY c.name""",
        (team_id,),
    ).fetchall()
    return [{"id": r[0], "name": r[1]} for r in rows]


# ---------------------------------------------------------------------------
# Evaluations (a player's grade for a given division/team)
# ---------------------------------------------------------------------------

def add_evaluation(conn: sqlite3.Connection, player_id: int, division_id: int, team_id: int | None, grade: str) -> int:
    cur = conn.execute(
        "INSERT INTO evaluations (player_id, division_id, team_id, grade) VALUES (?, ?, ?, ?)",
        (player_id, division_id, team_id, grade),
    )
    conn.commit()
    return cur.lastrowid


def list_evaluations(conn: sqlite3.Connection, player_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT e.id, d.year, d.season, d.age_group, t.name, e.grade, e.created_at
           FROM evaluations e
           JOIN divisions d ON d.id = e.division_id
           LEFT JOIN teams t ON t.id = e.team_id
           WHERE e.player_id = ?
           ORDER BY e.created_at DESC""",
        (player_id,),
    ).fetchall()
    return [
        {
            "id": r[0], "year": r[1], "season": display_text(r[2]), "age_group": r[3],
            "team_name": display_text(r[4]) if r[4] else None, "grade": r[5], "created_at": r[6],
        }
        for r in rows
    ]


def delete_evaluation(conn: sqlite3.Connection, evaluation_id: int):
    conn.execute("DELETE FROM evaluations WHERE id = ?", (evaluation_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Player stats
# ---------------------------------------------------------------------------

def get_player_stats(conn: sqlite3.Connection, division_id: int) -> list[dict]:
    """Per-player goals/assists/points/penalties/shootout stats for one
    division, keyed by (team name, jersey number) straight from the games
    already scanned — a roster entry is NOT required for a player to show
    up. If a roster entry exists for that (team, number), its name is used;
    otherwise the player is shown as "#<number>" until someone identifies
    them in Team Rosters. "player_id" is included so the UI can offer a
    "create/link player" action when it's still None."""

    def _tally(query: str) -> dict[tuple[str, str], int]:
        return {(team, number): count for team, number, count in conn.execute(query, (division_id, division_id))}

    goals = _tally(
        """SELECT team, scorer_number, COUNT(*) FROM (
               SELECT gm.home_team AS team, gl.scorer_number
               FROM goals gl JOIN games gm ON gm.id = gl.game_id AND gl.side = 'home'
               WHERE gm.division_id = ?
               UNION ALL
               SELECT gm.away_team AS team, gl.scorer_number
               FROM goals gl JOIN games gm ON gm.id = gl.game_id AND gl.side = 'away'
               WHERE gm.division_id = ?
           )
           WHERE scorer_number IS NOT NULL AND scorer_number <> ''
           GROUP BY team, scorer_number"""
    )
    assists = {}
    for team, number, count in conn.execute(
        """SELECT team, player_number, COUNT(*) FROM (
               SELECT gm.home_team AS team, gl.assist1_number AS player_number
               FROM goals gl JOIN games gm ON gm.id = gl.game_id AND gl.side = 'home'
               WHERE gm.division_id = ? AND gl.assist1_number IS NOT NULL AND gl.assist1_number <> ''
               UNION ALL
               SELECT gm.away_team, gl.assist1_number
               FROM goals gl JOIN games gm ON gm.id = gl.game_id AND gl.side = 'away'
               WHERE gm.division_id = ? AND gl.assist1_number IS NOT NULL AND gl.assist1_number <> ''
               UNION ALL
               SELECT gm.home_team, gl.assist2_number
               FROM goals gl JOIN games gm ON gm.id = gl.game_id AND gl.side = 'home'
               WHERE gm.division_id = ? AND gl.assist2_number IS NOT NULL AND gl.assist2_number <> ''
               UNION ALL
               SELECT gm.away_team, gl.assist2_number
               FROM goals gl JOIN games gm ON gm.id = gl.game_id AND gl.side = 'away'
               WHERE gm.division_id = ? AND gl.assist2_number IS NOT NULL AND gl.assist2_number <> ''
           )
           GROUP BY team, player_number""",
        (division_id, division_id, division_id, division_id),
    ):
        assists[(team, number)] = count
    penalties = _tally(
        """SELECT team, player_number, COUNT(*) FROM (
               SELECT gm.home_team AS team, pen.player_number
               FROM penalties pen JOIN games gm ON gm.id = pen.game_id AND pen.side = 'home'
               WHERE gm.division_id = ?
               UNION ALL
               SELECT gm.away_team, pen.player_number
               FROM penalties pen JOIN games gm ON gm.id = pen.game_id AND pen.side = 'away'
               WHERE gm.division_id = ?
           )
           WHERE player_number IS NOT NULL AND player_number <> ''
           GROUP BY team, player_number"""
    )
    # Shootout attempts are tracked separately — they never count toward "goals".
    shootout: dict[tuple[str, str], tuple[int, int]] = {}
    for team, number, attempts, made in conn.execute(
        """SELECT team, player_number, COUNT(*), COALESCE(SUM(scored), 0) FROM (
               SELECT gm.home_team AS team, so.player_number, so.scored
               FROM shootout_attempts so JOIN games gm ON gm.id = so.game_id AND so.side = 'home'
               WHERE gm.division_id = ?
               UNION ALL
               SELECT gm.away_team, so.player_number, so.scored
               FROM shootout_attempts so JOIN games gm ON gm.id = so.game_id AND so.side = 'away'
               WHERE gm.division_id = ?
           )
           WHERE player_number IS NOT NULL AND player_number <> ''
           GROUP BY team, player_number""",
        (division_id, division_id),
    ):
        shootout[(team, number)] = (attempts, made)

    roster = {
        (team, number): (name, player_id)
        for team, number, name, player_id in conn.execute(
            """SELECT t.name, re.number, re.name, re.player_id
               FROM roster_entries re JOIN teams t ON t.id = re.team_id
               WHERE t.division_id = ?""",
            (division_id,),
        )
    }
    team_ids = {
        name: team_id
        for team_id, name in conn.execute("SELECT id, name FROM teams WHERE division_id = ?", (division_id,))
    }

    keys = set(goals) | set(assists) | set(penalties) | set(shootout) | set(roster)

    stats = []
    for team, number in keys:
        g = goals.get((team, number), 0)
        a = assists.get((team, number), 0)
        so_attempts, so_made = shootout.get((team, number), (0, 0))
        name, player_id = roster.get((team, number), (None, None))
        stats.append({
            "team_id": team_ids.get(team), "team": team, "number": number,
            "name": name or f"#{number}", "player_id": player_id,
            "goals": g, "assists": a, "points": g + a,
            "penalties": penalties.get((team, number), 0),
            "shootout_goals": so_made, "shootout_misses": so_attempts - so_made,
        })
    stats.sort(key=lambda s: (s["team"], s["number"]))
    return stats


# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------

def get_standings(conn: sqlite3.Connection, division_id: int) -> list[dict]:
    """Per-team season record for one division, sorted by points then the
    tiebreak chain: head-to-head record, goal differential, regulation
    wins, OT wins, goals against (fewer first), goals for (more first).

    Points: 3 for a regulation win, 2 for an OT/shootout win, 1 for an OT/
    shootout loss, 1 each for an unresolved regulation tie, 0 for a
    regulation loss."""
    games = conn.execute(
        """SELECT home_team, away_team, home_final_score, away_final_score,
                  winner, ot_winner, ot_loser
           FROM games WHERE winner IS NOT NULL AND division_id = ?""",
        (division_id,),
    ).fetchall()

    teams: dict[str, dict] = {}

    def team(name: str) -> dict:
        if name not in teams:
            teams[name] = {
                "team": name, "games_played": 0, "wins": 0, "losses": 0,
                "ot_wins": 0, "ot_losses": 0, "ties": 0, "points": 0,
                "goals_for": 0, "goals_against": 0, "head_to_head": {},
            }
        return teams[name]

    for home_team, away_team, home_score, away_score, winner, ot_winner, ot_loser in games:
        if not home_team or not away_team:
            continue
        h, a = team(home_team), team(away_team)
        h["games_played"] += 1
        a["games_played"] += 1
        h["goals_for"] += home_score or 0
        h["goals_against"] += away_score or 0
        a["goals_for"] += away_score or 0
        a["goals_against"] += home_score or 0

        h_h2h = h["head_to_head"].setdefault(away_team, {"wins": 0, "losses": 0})
        a_h2h = a["head_to_head"].setdefault(home_team, {"wins": 0, "losses": 0})

        if winner == "tie":
            h["ties"] += 1
            a["ties"] += 1
            h["points"] += 1
            a["points"] += 1
        elif ot_winner:
            win_side, lose_side = (h, a) if ot_winner == "home" else (a, h)
            win_h2h, lose_h2h = (h_h2h, a_h2h) if ot_winner == "home" else (a_h2h, h_h2h)
            win_side["ot_wins"] += 1
            win_side["points"] += 2
            lose_side["ot_losses"] += 1
            lose_side["points"] += 1
            win_h2h["wins"] += 1
            lose_h2h["losses"] += 1
        elif winner in ("home", "away"):
            win_side, lose_side = (h, a) if winner == "home" else (a, h)
            win_h2h, lose_h2h = (h_h2h, a_h2h) if winner == "home" else (a_h2h, h_h2h)
            win_side["wins"] += 1
            win_side["points"] += 3
            lose_side["losses"] += 1
            win_h2h["wins"] += 1
            lose_h2h["losses"] += 1

    for t in teams.values():
        t["goal_diff"] = t["goals_for"] - t["goals_against"]

    return _sort_standings(list(teams.values()))


def _sort_standings(teams: list[dict]) -> list[dict]:
    return sorted(teams, key=functools.cmp_to_key(_compare_teams))


def _compare_teams(a: dict, b: dict) -> int:
    """Negative if a ranks above b, positive if below, 0 if fully tied."""
    if a["points"] != b["points"]:
        return b["points"] - a["points"]

    a_h2h = a["head_to_head"].get(b["team"], {"wins": 0, "losses": 0})
    b_h2h = b["head_to_head"].get(a["team"], {"wins": 0, "losses": 0})
    if a_h2h["wins"] != b_h2h["wins"]:
        return b_h2h["wins"] - a_h2h["wins"]

    if a["goal_diff"] != b["goal_diff"]:
        return b["goal_diff"] - a["goal_diff"]
    if a["wins"] != b["wins"]:
        return b["wins"] - a["wins"]
    if a["ot_wins"] != b["ot_wins"]:
        return b["ot_wins"] - a["ot_wins"]
    if a["goals_against"] != b["goals_against"]:
        return a["goals_against"] - b["goals_against"]
    return b["goals_for"] - a["goals_for"]


# ---------------------------------------------------------------------------
# Display tables — the exact rows/columns/order/sort shown in the app, so the
# UI and the Excel export can never drift out of sync with each other.
# ---------------------------------------------------------------------------

def standings_table(conn: sqlite3.Connection, division_id: int) -> list[dict]:
    standings = get_standings(conn, division_id)
    show_ties = any(s["ties"] for s in standings)
    table = []
    for i, s in enumerate(standings):
        row = {
            "Rank": i + 1, "Team": display_text(s["team"]), "GP": s["games_played"],
            "W": s["wins"], "L": s["losses"], "OTW": s["ot_wins"], "OTL": s["ot_losses"],
        }
        if show_ties:
            row["T"] = s["ties"]
        row.update({"PTS": s["points"], "GF": s["goals_for"], "GA": s["goals_against"], "DIFF": s["goal_diff"]})
        table.append(row)
    return table


def player_stats_table(conn: sqlite3.Connection, division_id: int) -> list[dict]:
    stats = sorted(get_player_stats(conn, division_id), key=lambda s: (-s["points"], -s["goals"]))
    return [
        {
            "Team": display_text(s["team"]), "#": s["number"], "Name": display_text(s["name"]),
            "G": s["goals"], "A": s["assists"], "PTS": s["points"], "PIM": s["penalties"],
            "SO Made": s["shootout_goals"], "SO Missed": s["shootout_misses"],
        }
        for s in stats
    ]


def roster_table(conn: sqlite3.Connection, division_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT t.name, re.number, re.name FROM roster_entries re
           JOIN teams t ON t.id = re.team_id
           WHERE t.division_id = ?
           ORDER BY t.name, CAST(re.number AS INTEGER), re.number""",
        (division_id,),
    ).fetchall()
    return [
        {"Team": display_text(team), "Number": number, "Name": display_text(name)}
        for team, number, name in rows
    ]


def games_table(conn: sqlite3.Connection, division_id: int) -> list[dict]:
    labels = {
        "id": "ID", "game_date": "Date", "division": "Division",
        "home_team": "Home", "home_final_score": "Home Score",
        "away_team": "Away", "away_final_score": "Away Score",
        "winner": "Winner", "ot_winner": "OT Winner", "ot_loser": "OT Loser",
        "source_file": "Source File",
    }
    return [{labels[k]: v for k, v in row.items()} for row in list_games(conn, division_id)]


def export_workbook(conn: sqlite3.Connection, division_id: int) -> bytes:
    """Build an in-memory .xlsx with one sheet per table, matching what's
    shown in the app (Games, Standings, Player Stats, Rosters) for one
    division."""
    import pandas as pd

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        sheets = {
            "Games": games_table(conn, division_id),
            "Standings": standings_table(conn, division_id),
            "Player Stats": player_stats_table(conn, division_id),
            "Rosters": roster_table(conn, division_id),
        }
        for sheet_name, rows in sheets.items():
            df = pd.DataFrame(rows)
            df.to_excel(writer, sheet_name=sheet_name, index=False)
    return buf.getvalue()
