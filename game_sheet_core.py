#!/usr/bin/env python3
"""
game_sheet_core.py

Framework-agnostic core for the Team Pittsburgh Ball Hockey game sheet
pipeline: calling Claude for handwriting extraction, computing the game
winner, splitting multi-page PDFs, and reading/writing the PostgreSQL
database.

Nothing here depends on a CLI (argparse/input/print), a GUI (tkinter), or a
web framework (Streamlit/Flask) — it's the shared logic that every front end
(terminal scripts today, a Streamlit app now, a Flask app later) builds on.
"""

import base64
import difflib
import functools
import json
import mimetypes
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import anthropic
import bcrypt
import psycopg2
from dotenv import load_dotenv
from pypdf import PdfReader, PdfWriter

# Load variables from a local .env file (e.g. DATABASE_URL, ANTHROPIC_API_KEY)
# into the process environment, so every front end that imports this module
# (app.py, process_game_sheet.py, edit_game.py, scripts/migrate_*.py) picks
# them up without each having to remember to load it separately. Never
# overrides a variable that's already set in the real environment (e.g. one
# exported by the shell or set by a deployment platform), so .env is purely
# a local-dev convenience and production config always wins.
load_dotenv()

# Type-hint alias only (a plain psycopg2 connection, wrapped by _ConnWrapper
# below at runtime) — kept as a string so this module doesn't need
# psycopg2.extensions imported just for annotations.
PGConnection = "psycopg2.extensions.connection"

SCHEMA_PATH = Path(__file__).parent / "schema_postgres.sql"
MODEL = "claude-sonnet-4-6"
SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _utcnow() -> datetime:
    """The current UTC time, as a naive datetime — every deleted_at/
    created_at timestamp in this file is stored (as TEXT) and compared
    without a timezone suffix, so this stays consistent with every existing
    stored value rather than switching format for new ones only.
    datetime.utcnow() itself is deprecated; this is the replacement its own
    deprecation warning points to, minus the tzinfo this file doesn't use."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

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


_MC_PREFIX_RE = re.compile(r"\bMc([a-z])")


def display_text(s: str | None) -> str | None:
    """Presentation form for an identity field: title-cased for display,
    with one correction str.title() alone gets wrong: a "Mc" surname
    prefix (McDavid, McDonald, McGregor) only gets its own first letter
    capitalized by title() — it treats a contiguous run of letters as one
    word, so "mcdavid" comes out "Mcdavid", not "McDavid". This
    re-capitalizes the letter right after "Mc" to fix that specific,
    reliably-a-name-prefix pattern.

    Deliberately doesn't attempt the same for "Mac": unlike "Mc", "Mac" is
    also the start of plenty of ordinary words/names (Macy, Mack, Macomb,
    machine), so guessing there would trade this one predictable wrong
    capitalization for a different, less predictable one — no fix, rather
    than a worse one."""
    if not s:
        return s
    return _MC_PREFIX_RE.sub(lambda m: "Mc" + m.group(1).upper(), s.title())


# Game dates are stored as ISO 'YYYY-MM-DD' so they sort/compare correctly
# (a plain "7/14/26" string doesn't), and shown back as M/D/YYYY.
_DATE_INPUT_FORMATS = [
    "%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d", "%m-%d-%y", "%m-%d-%Y", "%m.%d.%y", "%m.%d.%Y",
]


def normalize_date(s: str | None) -> str | None:
    """Storage form for game_date: ISO 'YYYY-MM-DD'. Falls back to the
    trimmed original string if it doesn't match a recognized format, rather
    than losing/corrupting unusual handwriting-extraction results.

    A spreadsheet date cell (birth date, an imported schedule's date
    column) often round-trips through pandas/openpyxl as a full timestamp
    string ("2015-08-11 00:00:00" or "2015-08-11T00:00:00") even when only
    the date matters — the time-of-day component, always midnight for a
    plain date cell, is dropped before matching against the formats above
    rather than making the whole value fail to parse and fall through
    unchanged."""
    if not s:
        return s
    s = s.strip()
    date_part = re.split(r"[ T]", s, maxsplit=1)[0]
    # Tries the date-only portion first (the common case for a spreadsheet
    # timestamp), then the untouched original -- redundant work when there
    # was nothing to split off, but harmless, and simpler than special-
    # casing "nothing to split" separately.
    for candidate in (date_part, s):
        for fmt in _DATE_INPUT_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).strftime("%Y-%m-%d")
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


def match_team_name(
    conn: PGConnection, division_id: int, value: str | None, existing_teams: list[str] | None = None,
) -> str | None:
    """Best-effort match of free text (extraction/typos) to a team already in
    this division, e.g. "Avachale" -> "Avalanche". Returns None if there's no
    confident match (including when this division has no teams yet).

    `existing_teams` lets a caller matching many values against the same
    division (e.g. list_schedule, once per scheduled game) pass the team
    list in once instead of this re-querying it — unchanging within that
    caller — on every single call."""
    if not value or not value.strip():
        return None
    value = value.strip()
    if existing_teams is None:
        existing_teams = [
            row[0] for row in conn.execute(
                "SELECT name FROM teams WHERE division_id = %s", (division_id,)
            ).fetchall()
        ]
    if not existing_teams:
        return None
    exact = next((n for n in existing_teams if n.lower() == value.lower()), None)
    if exact:
        return display_text(exact)
    match = _best_fuzzy_match(value, existing_teams)
    return display_text(match) if match else None


def normalize_team_name(
    conn: PGConnection, division_id: int, value: str | None, existing_teams: list[str] | None = None,
) -> str | None:
    """Storage form for a team name field: auto-corrected to an existing team
    in this division (fuzzy-matched) when there's a confident match, else
    just lowercased/trimmed like any other identity field so unrecognized
    text (e.g. a genuinely new team) isn't lost. `existing_teams` is passed
    straight through to match_team_name — see its docstring."""
    matched = match_team_name(conn, division_id, value, existing_teams=existing_teams)
    return normalize_text(matched) if matched else normalize_text(value)


# ---------------------------------------------------------------------------
# Claude extraction
# ---------------------------------------------------------------------------

def guess_mime(filename: str) -> str | None:
    mime, _ = mimetypes.guess_type(filename)
    return mime


_EXIF_ORIENTATION_TAG = 0x0112


def normalize_image_orientation(data: bytes, mime: str | None) -> bytes:
    """Bake a photo's EXIF orientation tag into its actual pixel data. Phone
    cameras commonly save a sideways/upside-down sensor image plus an EXIF
    tag saying how to rotate it for display — a phone gallery or browser
    applies that automatically, but Claude's vision only sees the raw,
    un-rotated pixels, which makes handwriting much harder to read. Passes
    everything else (PDFs, images with no orientation tag) through
    unchanged, and never raises — a rotation nicety shouldn't block
    extraction if something about the image is unusual."""
    if mime not in ("image/jpeg", "image/png", "image/webp"):
        return data
    try:
        from PIL import Image, ImageOps

        img = Image.open(BytesIO(data))
        if img.getexif().get(_EXIF_ORIENTATION_TAG, 1) == 1:
            return data
        transposed = ImageOps.exif_transpose(img)
        buf = BytesIO()
        transposed.save(buf, format=img.format)
        return buf.getvalue()
    except Exception:
        return data


def build_content_block(data: bytes, mime: str | None) -> dict:
    """Build an Anthropic API content block (image or document) from raw bytes."""
    if mime in ("image/png", "image/jpeg", "image/webp"):
        data = normalize_image_orientation(data, mime)
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

def shootout_winner(shootout_attempts: list[dict]) -> str | None:
    """'home'/'away' if the shootout has a decisive winner (more scored
    attempts on that side), else None — no attempts, or the recorded
    attempts are themselves tied (incomplete/not fully transcribed)."""
    home_so = sum(1 for s in shootout_attempts if s.get("side") == "home" and s.get("scored"))
    away_so = sum(1 for s in shootout_attempts if s.get("side") == "away" and s.get("scored"))
    if home_so > away_so:
        return "home"
    if away_so > home_so:
        return "away"
    return None


def compute_winner(data: dict) -> str | None:
    """Determine the game winner: 'home', 'away', or 'tie'. If there are
    shootout attempts, the shootout decides it whenever the final score is
    consistent with that: either a true regulation tie (shootout tracked
    entirely separately), or the shootout winner's score exactly one goal
    ahead — the common real-world box-score convention of showing a
    shootout winner as e.g. "5-4" rather than the true 4-4 tie. Otherwise
    falls back to a plain score comparison. Returns None if scores aren't
    both known yet."""
    home = data.get("home_final_score")
    away = data.get("away_final_score")
    if home is None or away is None:
        return None

    shootout = data.get("shootout_attempts") or []
    if shootout:
        so_winner = shootout_winner(shootout)
        if so_winner == "home" and home - away in (0, 1):
            return "home"
        if so_winner == "away" and away - home in (0, 1):
            return "away"

    if home > away:
        return "home"
    if away > home:
        return "away"
    return "tie"


def resolve_winner(data: dict) -> str | None:
    """The winner to actually store: an explicit override in data["winner"] takes
    precedence (e.g. a manual correction), otherwise it's computed from scores."""
    winner = data.get("winner")
    if winner in ("home", "away", "tie"):
        return winner
    return compute_winner(data)


def validate_shootout(data: dict):
    """Raise ValueError if shootout attempts are recorded but don't
    reconcile with the score: either regulation must have actually ended
    tied, or the score must show the shootout winner exactly one goal
    ahead (the standard box-score convention, e.g. "5-4" for a game that
    was really 4-4 before the shootout) — and the shootout itself must
    actually have a winner. Without this check, a real mismatch (e.g. a
    typo in the score, or shootout rows left over from correcting a game
    that used to be tied) would silently fall through compute_ot_result()
    as "no shootout decided this", counting it as a plain regulation
    win/loss instead of the separate shootout win/loss it actually is."""
    shootout = data.get("shootout_attempts") or []
    if not shootout:
        return
    home = data.get("home_final_score")
    away = data.get("away_final_score")
    if home is not None and away is not None and abs(home - away) > 1:
        raise ValueError(
            "Shootout attempts are recorded, but the score is more than one goal apart — "
            "a shootout only happens after a tied game (the final score may show the "
            "winner with one bonus goal added, e.g. 5-4, but not more than that). Fix the "
            "score, or remove the shootout attempts if this game wasn't actually decided by one."
        )
    if shootout_winner(shootout) is None:
        raise ValueError(
            "Shootout attempts are recorded, but the shootout itself doesn't show a winner — "
            "check which attempts were marked as scored before saving."
        )


def compute_ot_result(data: dict) -> tuple[str | None, str | None]:
    """Return (ot_winner, ot_loser) — 'home'/'away' each — if the game was
    decided by a shootout (the score is consistent with a tie, per
    compute_winner()'s rules, and there are shootout attempts), else
    (None, None). Used to award shootout-loss points distinctly from a
    regulation loss."""
    winner = resolve_winner(data)
    if winner not in ("home", "away"):
        return None, None
    shootout = data.get("shootout_attempts") or []
    if not shootout:
        return None, None
    home = data.get("home_final_score") or 0
    away = data.get("away_final_score") or 0
    if abs(home - away) > 1:
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


class _ConnWrapper:
    """Wraps a psycopg2 connection with the sqlite3-style `conn.execute(...)`
    shortcut (opening a fresh cursor per call and returning it so
    `.fetchall()`/`.fetchone()` can be chained directly) that every query in
    this file is already written against. Ported from sqlite3, where that
    shortcut is built in — psycopg2 requires an explicit cursor. This keeps
    the ~130 query call sites in this file unchanged in shape; only their
    `?` placeholders become `%s` and a handful of call sites that relied on
    sqlite3-only features (`cur.lastrowid`, `conn.total_changes`) are
    rewritten to use `RETURNING` instead."""

    def __init__(self, pg_conn):
        # psycopg2 defaults to autocommit=False, so even a plain SELECT
        # opens an implicit transaction that stays open ("idle in
        # transaction") until something calls commit() — which read-only
        # functions in this file never do. Since one connection is cached
        # per browser session and reused for the session's whole lifetime,
        # that left sessions sitting idle-in-transaction for hours between
        # writes, which can block schema-changing DDL (ALTER TABLE, etc.)
        # from ever acquiring its lock. Autocommit makes every statement
        # its own transaction, closing that gap; nothing here relies on
        # rolling back a multi-statement batch (see .rollback() below —
        # it's never actually called anywhere in this codebase).
        pg_conn.autocommit = True
        self._conn = pg_conn

    def execute(self, sql: str, params=()):
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return cur

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def _apply_schema(conn: _ConnWrapper):
    """Run schema_postgres.sql's CREATE TABLE/VIEW IF NOT EXISTS statements.
    psycopg2 has no equivalent of sqlite3's executescript() (multi-statement
    exec in one call), so this strips `--` line comments (several of which
    contain a literal ';', e.g. "soft-deleted; purged 30 days later" — left
    in, a naive split would break a CREATE TABLE mid-statement there) and
    then splits what's left on top-level `;` — safe here since the schema
    file has no semicolons inside string literals or function bodies."""
    lines = []
    for line in SCHEMA_PATH.read_text().splitlines():
        comment_at = line.find("--")
        lines.append(line[:comment_at] if comment_at != -1 else line)
    for statement in "\n".join(lines).split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)
    conn.commit()


def full_name(first_name: str | None, last_name: str | None) -> str:
    """Display name from first/last, e.g. for players/coaches — skips a
    missing last name rather than leaving a trailing space."""
    return " ".join(part for part in (first_name, last_name) if part)


def split_full_name(name: str | None) -> tuple[str, str | None]:
    """Best-effort split of a single "Full Name" string into (first, last),
    on the first space — used for one-time data migration (see
    _migrate_legacy_names) and by scripts/import_summer_player_list.py,
    whose source spreadsheet only has one name column per player/coach."""
    parts = (name or "").strip().split(None, 1)
    if not parts:
        return "", None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1]


def _column_exists(conn: PGConnection, table: str, column: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
        (table, column),
    ).fetchone()
    return row is not None


def _migrate_legacy_names(conn: PGConnection):
    """One-time backfill: players and coaches used to store a single `name`
    column; first_name/last_name/nickname (see schema_postgres.sql) replaced
    it. Splits any not-yet-migrated `name` value into first_name/last_name
    and drops the column once every row is converted. Safe to call on every
    startup — a no-op as soon as `name` is gone, which the raw schema file
    can't express on its own (its statement-splitter can't handle the
    conditional logic a real migration needs — see _apply_schema)."""
    for table in ("players", "coaches"):
        if not _column_exists(conn, table, "name"):
            continue
        rows = conn.execute(f"SELECT id, name FROM {table} WHERE first_name IS NULL").fetchall()
        for row_id, name in rows:
            first, last = split_full_name(name)
            conn.execute(
                f"UPDATE {table} SET first_name = %s, last_name = %s WHERE id = %s", (first, last, row_id)
            )
        conn.execute(f"ALTER TABLE {table} DROP COLUMN name")
        conn.commit()


def init_db(dsn: str) -> _ConnWrapper:
    """Connect to the Postgres database identified by dsn (a full connection
    string / DSN, e.g. "postgresql://user:pass@host:port/dbname?sslmode=require"),
    ensure the schema exists, and purge any long-expired soft-deleted
    divisions (see purge_expired_divisions)."""
    conn = _ConnWrapper(psycopg2.connect(dsn))
    _apply_schema(conn)
    _migrate_legacy_names(conn)
    backfill_player_parents(conn)
    purge_expired_divisions(conn)
    return conn


def get_setting(conn: PGConnection, key: str) -> str | None:
    """A durable (survives a restart) app-level preference, e.g. the
    last-selected Working Division — stored in the database itself rather
    than session state, which resets every new browser session."""
    row = conn.execute("SELECT value FROM app_settings WHERE key = %s", (key,)).fetchone()
    return row[0] if row else None


def set_setting(conn: PGConnection, key: str, value: str):
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Roles, users & per-page permissions (who can log into the app — distinct
# from players/coaches, who's on a roster)
# ---------------------------------------------------------------------------

# Every page/tab a user could be granted access to (via a role), keyed the
# same way the app's tabs are. Kept here (not just in app.py) so CLI tooling
# (scripts/manage_users.py) validates against the same set without
# duplicating it, and so it can't silently drift out of sync with app.py.
PAGES = {
    "process": "Games → Import Scoresheets",
    "edit": "Games → Manage Games",
    "schedule": "Games → Schedule & Results",
    "standings": "Stats & Standings → Standings",
    "stats": "Stats & Standings → Player Stats",
    "rosters": "Teams → Team Rosters",
    "teams": "Teams → Teams",
    "players": "Teams → Players",
    "coaches": "Teams → Coaches",
    "divisions": "Teams → Divisions",
    "draft": "Teams → Draft",
}


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def _check_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


# --- Roles: a named bundle of page access, assigned to users -----------------

def list_role_pages(conn: PGConnection, role_id: int | None) -> list[str]:
    if role_id is None:
        return []
    rows = conn.execute("SELECT page FROM role_pages WHERE role_id = %s", (role_id,)).fetchall()
    return [r[0] for r in rows]


def set_role_pages(conn: PGConnection, role_id: int, pages: list[str]):
    conn.execute("DELETE FROM role_pages WHERE role_id = %s", (role_id,))
    for page in pages:
        conn.execute("INSERT INTO role_pages (role_id, page) VALUES (%s, %s)", (role_id, page))
    conn.commit()


def list_roles(conn: PGConnection) -> list[dict]:
    rows = conn.execute("SELECT id, name, read_only, hide_contact_details FROM roles ORDER BY name").fetchall()
    roles = [
        {"id": r[0], "name": r[1], "read_only": bool(r[2]), "hide_contact_details": bool(r[3])} for r in rows
    ]
    for role in roles:
        role["pages"] = list_role_pages(conn, role["id"])
    return roles


def get_role(conn: PGConnection, role_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, name, read_only, hide_contact_details FROM roles WHERE id = %s", (role_id,)
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "name": row[1], "read_only": bool(row[2]), "hide_contact_details": bool(row[3]),
        "pages": list_role_pages(conn, row[0]),
    }


def add_role(
    conn: PGConnection, name: str, pages: list[str] | None = None,
    read_only: bool = False, hide_contact_details: bool = False,
) -> int:
    cur = conn.execute(
        "INSERT INTO roles (name, read_only, hide_contact_details) VALUES (%s, %s, %s) RETURNING id",
        (name.strip(), int(read_only), int(hide_contact_details)),
    )
    role_id = cur.fetchone()[0]
    conn.commit()
    if pages:
        set_role_pages(conn, role_id, pages)
    return role_id


def update_role(
    conn: PGConnection, role_id: int, name: str | None = None, pages: list[str] | None = None,
    read_only: bool | None = None, hide_contact_details: bool | None = None,
):
    if name is not None:
        conn.execute("UPDATE roles SET name = %s WHERE id = %s", (name.strip(), role_id))
        conn.commit()
    if read_only is not None:
        conn.execute("UPDATE roles SET read_only = %s WHERE id = %s", (int(read_only), role_id))
        conn.commit()
    if hide_contact_details is not None:
        conn.execute("UPDATE roles SET hide_contact_details = %s WHERE id = %s", (int(hide_contact_details), role_id))
        conn.commit()
    if pages is not None:
        set_role_pages(conn, role_id, pages)


def delete_role(conn: PGConnection, role_id: int):
    # Any user with this role loses it (role_id -> NULL, ON DELETE SET NULL)
    # rather than the delete being blocked.
    conn.execute("DELETE FROM roles WHERE id = %s", (role_id,))
    conn.commit()


# --- Users --------------------------------------------------------------

def _get_role_flags(conn: PGConnection, role_id: int | None) -> tuple[bool, bool]:
    """(read_only, hide_contact_details) for a role, or (False, False) if
    role_id is None — the defaults for a user with no role assigned."""
    if role_id is None:
        return False, False
    row = conn.execute(
        "SELECT read_only, hide_contact_details FROM roles WHERE id = %s", (role_id,)
    ).fetchone()
    return (bool(row[0]), bool(row[1])) if row else (False, False)


def list_users(conn: PGConnection, include_deleted: bool = False) -> list[dict]:
    where = "" if include_deleted else "WHERE deleted_at IS NULL"
    rows = conn.execute(
        f"SELECT id, email, display_name, is_admin, role_id, coach_id, deleted_at FROM users {where} ORDER BY email"
    ).fetchall()
    users = [
        {
            "id": r[0], "email": r[1], "display_name": r[2], "is_admin": bool(r[3]),
            "role_id": r[4], "coach_id": r[5], "deleted_at": r[6],
        }
        for r in rows
    ]
    for u in users:
        u["pages"] = list_role_pages(conn, u["role_id"])
        u["read_only"], u["hide_contact_details"] = _get_role_flags(conn, u["role_id"])
    return users


def get_user_by_email(conn: PGConnection, email: str) -> dict | None:
    row = conn.execute(
        "SELECT id, email, password_hash, display_name, is_admin, role_id, coach_id, deleted_at "
        "FROM users WHERE email = %s",
        (email.strip().lower(),),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "email": row[1], "password_hash": row[2], "display_name": row[3],
        "is_admin": bool(row[4]), "role_id": row[5], "coach_id": row[6], "deleted_at": row[7],
    }


def verify_login(conn: PGConnection, email: str, password: str) -> dict | None:
    """Returns the user dict (password hash stripped) plus their permitted
    pages, read_only, and hide_contact_details flags (all derived from
    their role; the defaults below if they have none) if email/password
    match an active account, else None. Deliberately doesn't tell the
    caller whether the email exists vs. the password was wrong — same
    generic failure either way, so a login form can't be used to enumerate
    registered emails."""
    user = get_user_by_email(conn, email)
    if not user or user["deleted_at"] is not None:
        return None
    if not _check_password(password, user["password_hash"]):
        return None
    user.pop("password_hash")
    user["pages"] = list_role_pages(conn, user["role_id"])
    user["read_only"], user["hide_contact_details"] = _get_role_flags(conn, user["role_id"])
    return user


def add_user(
    conn: PGConnection, email: str, password: str, display_name: str | None = None,
    is_admin: bool = False, role_id: int | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO users (email, password_hash, display_name, is_admin, role_id) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (
            email.strip().lower(), _hash_password(password), (display_name or "").strip() or None,
            int(is_admin), role_id,
        ),
    )
    user_id = cur.fetchone()[0]
    conn.commit()
    return user_id


def update_user(
    conn: PGConnection, user_id: int, display_name: str | None = None, is_admin: bool | None = None,
):
    if display_name is not None:
        conn.execute("UPDATE users SET display_name = %s WHERE id = %s", (display_name.strip() or None, user_id))
    if is_admin is not None:
        conn.execute("UPDATE users SET is_admin = %s WHERE id = %s", (int(is_admin), user_id))
    conn.commit()


def set_user_role(conn: PGConnection, user_id: int, role_id: int | None):
    conn.execute("UPDATE users SET role_id = %s WHERE id = %s", (role_id, user_id))
    conn.commit()


def set_user_coach(conn: PGConnection, user_id: int, coach_id: int | None):
    """Link (or clear) which coach profile this login represents — lets the
    Draft page tell whether a signed-in user is a given team's coach."""
    conn.execute("UPDATE users SET coach_id = %s WHERE id = %s", (coach_id, user_id))
    conn.commit()


def set_user_password(conn: PGConnection, user_id: int, new_password: str):
    conn.execute("UPDATE users SET password_hash = %s WHERE id = %s", (_hash_password(new_password), user_id))
    conn.commit()


def soft_delete_user(conn: PGConnection, user_id: int):
    conn.execute(
        "UPDATE users SET deleted_at = %s WHERE id = %s",
        (_utcnow().isoformat(timespec="seconds"), user_id),
    )
    conn.commit()


def restore_user(conn: PGConnection, user_id: int):
    conn.execute("UPDATE users SET deleted_at = NULL WHERE id = %s", (user_id,))
    conn.commit()


def _merge_duplicate_teams(conn: PGConnection):
    """If case variants (e.g. "Blues" and "BLUes") already created separate
    team rows *within the same division* before storage was normalized, merge
    them into one canonical (lowest id) team so lowercasing teams.name below
    doesn't hit its UNIQUE constraint. Players under a merged-away duplicate
    move to the canonical team; a player number that already exists there is
    dropped rather than kept twice. The same name in a *different* division
    is a different team and is left alone.

    A one-time data-quality pass, not a Postgres/SQLite-specific concern —
    called from the migrate-data-into-Postgres script (see
    scripts/migrate_sqlite_to_postgres.py), not on every app connect."""
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
                "SELECT id, number FROM roster_entries WHERE team_id = %s", (dup_id,)
            ).fetchall():
                clash = conn.execute(
                    "SELECT 1 FROM roster_entries WHERE team_id = %s AND number = %s", (canonical_id, number),
                ).fetchone()
                if clash:
                    conn.execute("DELETE FROM roster_entries WHERE id = %s", (entry_id,))
                else:
                    conn.execute("UPDATE roster_entries SET team_id = %s WHERE id = %s", (canonical_id, entry_id))
            conn.execute("DELETE FROM teams WHERE id = %s", (dup_id,))


def _normalize_identity_case(conn: PGConnection):
    """One-time (idempotent) lowercase normalization of identity fields
    already in the database, for rows written before this existed. See
    _merge_duplicate_teams — same "run once during data migration" note."""
    conn.execute("UPDATE games SET home_team = LOWER(TRIM(home_team)) WHERE home_team IS NOT NULL")
    conn.execute("UPDATE games SET away_team = LOWER(TRIM(away_team)) WHERE away_team IS NOT NULL")
    conn.execute("UPDATE games SET division = LOWER(TRIM(division)) WHERE division IS NOT NULL")
    conn.execute("UPDATE teams SET name = LOWER(TRIM(name)) WHERE name IS NOT NULL")
    conn.execute("UPDATE roster_entries SET name = LOWER(TRIM(name)) WHERE name IS NOT NULL")


def _normalize_dates(conn: PGConnection):
    """One-time (idempotent) conversion of existing game_date values to the
    ISO storage format, for rows written before this existed."""
    rows = conn.execute("SELECT id, game_date FROM games WHERE game_date IS NOT NULL").fetchall()
    for game_id, game_date in rows:
        normalized = normalize_date(game_date)
        if normalized != game_date:
            conn.execute("UPDATE games SET game_date = %s WHERE id = %s", (normalized, game_id))


def _fix_division_typos(conn: PGConnection):
    """One-time (idempotent) auto-correction of existing games.division
    values against the known age group list, for rows written before this
    existed (or before the corresponding form field validated it)."""
    rows = conn.execute("SELECT id, division FROM games WHERE division IS NOT NULL").fetchall()
    for row_id, division in rows:
        fixed = normalize_division(division)
        if fixed != division:
            conn.execute("UPDATE games SET division = %s WHERE id = %s", (fixed, row_id))


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


def register_players_from_game(conn: PGConnection, data: dict, division_id: int):
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
                "INSERT INTO roster_entries (team_id, number, name) VALUES (%s, %s, %s) "
                "ON CONFLICT (team_id, number) DO NOTHING",
                (team_id, number, f"#{number}"),
            )
    conn.commit()


def insert_stat_rows(conn: PGConnection, game_id: int, data: dict):
    """Insert the goals/penalties/shootout_attempts rows for a game. Does not
    commit or touch the games row itself."""
    for g in data.get("goals", []):
        conn.execute(
            """INSERT INTO goals (game_id, side, scorer_number, assist1_number,
                                   assist2_number, period, time)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (game_id, g.get("side"), g.get("scorer_number"), g.get("assist1_number"),
             g.get("assist2_number"), g.get("period"), g.get("time")),
        )

    for p in data.get("penalties", []):
        conn.execute(
            """INSERT INTO penalties (game_id, side, player_number, penalty_type, period, time)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (game_id, p.get("side"), p.get("player_number"), p.get("penalty_type"),
             p.get("period"), p.get("time")),
        )

    for s in data.get("shootout_attempts", []):
        conn.execute(
            """INSERT INTO shootout_attempts (game_id, side, round, player_number, scored)
               VALUES (%s, %s, %s, %s, %s)""",
            (game_id, s.get("side"), s.get("round"), s.get("player_number"),
             1 if s.get("scored") else 0),
        )


def insert_game(conn: PGConnection, data: dict, source_file: str, working_division_id: int) -> tuple[int, bool]:
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
    validate_shootout(data)
    ot_winner, ot_loser = compute_ot_result(data)
    cur = conn.execute(
        """INSERT INTO games
           (game_date, division, division_id, home_team, home_color, home_final_score,
            away_team, away_color, away_final_score, went_to_shootout, winner,
            ot_winner, ot_loser, source_file)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (game_date, home_team, away_team, source_file) DO NOTHING
           RETURNING id""",
        (
            data.get("game_date"), data.get("division"), division_id,
            data.get("home_team"), data.get("home_color"), data.get("home_final_score"),
            data.get("away_team"), data.get("away_color"), data.get("away_final_score"),
            went_to_shootout, winner, ot_winner, ot_loser, source_file,
        ),
    )
    inserted = cur.fetchone()
    already_existed = inserted is None
    if already_existed:
        row = conn.execute(
            "SELECT id FROM games WHERE game_date=%s AND home_team=%s AND away_team=%s AND source_file=%s",
            (data.get("game_date"), data.get("home_team"), data.get("away_team"), source_file),
        ).fetchone()
        game_id = row[0]
    else:
        game_id = inserted[0]

    insert_stat_rows(conn, game_id, data)
    conn.commit()
    register_players_from_game(conn, data, division_id)
    return game_id, already_existed


def list_games(conn: PGConnection, division_id: int) -> list[dict]:
    cols = ["id", "game_date", "division", "home_team", "home_final_score",
            "away_team", "away_final_score", "winner", "ot_winner", "ot_loser", "source_file"]
    rows = conn.execute(
        f"SELECT {', '.join(cols)} FROM games WHERE division_id = %s ORDER BY id", (division_id,)
    ).fetchall()
    games = [dict(zip(cols, row)) for row in rows]
    for g in games:
        g["game_date"] = display_date(g["game_date"])
        g["division"] = display_text(g["division"])
        g["home_team"] = display_text(g["home_team"])
        g["away_team"] = display_text(g["away_team"])
    return games


def find_game_by_source_file(conn: PGConnection, source_file: str) -> dict | None:
    """Return the existing game whose source_file matches, or None. Used to
    warn before re-processing a file that's already been imported."""
    cols = ["id", "game_date", "division", "home_team", "away_team",
            "home_final_score", "away_final_score"]
    row = conn.execute(
        f"SELECT {', '.join(cols)} FROM games WHERE source_file = %s", (source_file,)
    ).fetchone()
    if row is None:
        return None
    g = dict(zip(cols, row))
    g["game_date"] = display_date(g["game_date"])
    g["division"] = display_text(g["division"])
    g["home_team"] = display_text(g["home_team"])
    g["away_team"] = display_text(g["away_team"])
    return g


def import_schedule(conn: PGConnection, division_id: int, schedule_rows: list[dict]) -> int:
    """Persist an uploaded season schedule for this division. Each row needs
    at least "game_date", "home_team", "away_team"; "order", "round",
    "start_time", "end_time", "location", "field" are optional. Upserted by
    (division_id, date, home, away) so re-uploading a corrected CSV updates
    round/time/location in place instead of creating duplicate rows —
    schedules do get revised mid-season. Rows missing date/home/away are
    skipped. Returns how many rows were saved."""
    saved = 0
    for row in schedule_rows:
        date = normalize_date(row.get("game_date"))
        home = normalize_text(row.get("home_team"))
        away = normalize_text(row.get("away_team"))
        if not date or not home or not away:
            continue
        conn.execute(
            """INSERT INTO schedule_games
               (division_id, order_num, round, game_date, home_team, away_team,
                start_time, end_time, location, field)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT(division_id, game_date, home_team, away_team) DO UPDATE SET
                   order_num = excluded.order_num, round = excluded.round,
                   start_time = excluded.start_time, end_time = excluded.end_time,
                   location = excluded.location, field = excluded.field""",
            (
                division_id, row.get("order"), row.get("round"), date, home, away,
                row.get("start_time"), row.get("end_time"), row.get("location"), row.get("field"),
            ),
        )
        saved += 1
    conn.commit()
    return saved


def list_schedule(conn: PGConnection, division_id: int) -> list[dict]:
    """The division's persisted schedule, each row annotated with
    "accounted_for" — whether a stored game shares its date and its two
    teams (regardless of which side is home/away, since a transcribed sheet
    occasionally has them swapped relative to the official schedule).
    Computed fresh against the current games table on every call — no
    caching anywhere in this path — so inserting, editing (including a
    home/away swap), or deleting a game is reflected the moment this is
    called again, without re-uploading the schedule.

    Team names are resolved through the same fuzzy match used for every
    other team-name field (normalize_team_name) before comparing, not
    compared as literal text — the schedule's spelling for a team (e.g.
    "Kings" from the CSV) can otherwise differ just enough from that team's
    actual stored spelling (a first-sheet typo that became canonical, extra
    whitespace, etc.) that an exact-string match silently never fires even
    though the game is plainly right there in the Games tab.

    An unaccounted row also gets "possible_matches": stored games between
    those same two teams whose date doesn't correspond to *any* scheduled
    date for that matchup — i.e. a game that's plainly for this matchup but
    filed under the wrong date (most often a mis-transcribed year), not a
    same-teams rematch that's simply scheduled for later. Surfaced so a
    date-entry typo can be found and fixed rather than guessed at
    automatically — silently declaring two different-year games "the same"
    would risk masking an actual gap.

    An accounted-for row also gets "result": the matching stored game's id,
    home/away teams (as actually recorded, which may be swapped relative to
    the schedule's designation), and final scores — so the schedule view can
    show results inline instead of just a yes/no "accounted for"."""
    stored = conn.execute(
        """SELECT id, game_date, home_team, away_team, home_final_score, away_final_score
           FROM games WHERE division_id = %s""",
        (division_id,),
    ).fetchall()
    played = {(date, frozenset((home, away))) for _id, date, home, away, _hs, _as in stored}
    results_by_key = {
        (date, frozenset((home, away))): {
            "game_id": game_id, "home_team": display_text(home), "away_team": display_text(away),
            "home_score": home_score, "away_score": away_score,
        }
        for game_id, date, home, away, home_score, away_score in stored
    }

    rows = conn.execute(
        """SELECT id, order_num, round, game_date, home_team, away_team,
                  start_time, end_time, location, field
           FROM schedule_games WHERE division_id = %s
           ORDER BY game_date, order_num""",
        (division_id,),
    ).fetchall()
    # Fetched once and reused for every row below — this division's team
    # list doesn't change mid-call, so re-querying it per scheduled game
    # (as match_team_name does by default) was one extra round trip per
    # game, twice over (home and away), for no different a result.
    existing_teams = [
        row[0] for row in conn.execute("SELECT name FROM teams WHERE division_id = %s", (division_id,)).fetchall()
    ]
    cols = ["id", "order_num", "round", "game_date", "home_team", "away_team",
            "start_time", "end_time", "location", "field"]

    # Pass 1: normalize every row's teams once, and note every date each
    # matchup is scheduled for anywhere in the season (not just this row) —
    # needed below to tell "a rematch scheduled for a different day" apart
    # from "the same game filed under a typo'd date".
    parsed = []
    scheduled_dates_by_teams: dict[frozenset, set[str]] = {}
    for values in rows:
        r = dict(zip(cols, values))
        home_key = normalize_team_name(conn, division_id, r["home_team"], existing_teams=existing_teams)
        away_key = normalize_team_name(conn, division_id, r["away_team"], existing_teams=existing_teams)
        r["_teams"] = frozenset((home_key, away_key))
        parsed.append(r)
        scheduled_dates_by_teams.setdefault(r["_teams"], set()).add(r["game_date"])

    # Pass 2: any stored game whose date isn't one of its matchup's
    # scheduled dates is an "orphan" — a candidate for being an unaccounted
    # row's actual game, just filed under the wrong date.
    orphans_by_teams: dict[frozenset, list[dict]] = {}
    for game_id, date, home, away, _hs, _as in stored:
        teams = frozenset((home, away))
        if date not in scheduled_dates_by_teams.get(teams, set()):
            orphans_by_teams.setdefault(teams, []).append({"id": game_id, "game_date": display_date(date)})

    result = []
    for r in parsed:
        key = (r["game_date"], r["_teams"])
        r["accounted_for"] = key in played
        r["possible_matches"] = [] if r["accounted_for"] else orphans_by_teams.get(r["_teams"], [])
        r["result"] = results_by_key.get(key) if r["accounted_for"] else None
        del r["_teams"]
        r["game_date"] = display_date(r["game_date"])
        r["home_team"] = display_text(r["home_team"])
        r["away_team"] = display_text(r["away_team"])
        result.append(r)
    return result


def clear_schedule(conn: PGConnection, division_id: int):
    """Delete the division's entire persisted schedule (e.g. the wrong CSV
    was uploaded) so a corrected one can be uploaded clean."""
    conn.execute("DELETE FROM schedule_games WHERE division_id = %s", (division_id,))
    conn.commit()


def load_game(conn: PGConnection, game_id: int) -> tuple[dict | None, str | None]:
    """Return (data, source_file) for the given game id, matching the same
    structure extract_game_sheet() produces (plus the stored "winner"), or
    (None, None) if not found."""
    row = conn.execute(
        """SELECT game_date, division, home_team, home_color, home_final_score,
                  away_team, away_color, away_final_score, winner, source_file
           FROM games WHERE id = %s""",
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
               FROM goals WHERE game_id = %s ORDER BY id""",
            (game_id,),
        ).fetchall()
    ]
    penalties = [
        dict(zip(("side", "player_number", "penalty_type", "period", "time"), r))
        for r in conn.execute(
            """SELECT side, player_number, penalty_type, period, time
               FROM penalties WHERE game_id = %s ORDER BY id""",
            (game_id,),
        ).fetchall()
    ]
    shootout_attempts = [
        {"side": side, "round": round_, "player_number": player_number, "scored": bool(scored)}
        for side, round_, player_number, scored in conn.execute(
            """SELECT side, round, player_number, scored
               FROM shootout_attempts WHERE game_id = %s ORDER BY round, id""",
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


def update_game(conn: PGConnection, game_id: int, data: dict, working_division_id: int):
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
    validate_shootout(data)
    ot_winner, ot_loser = compute_ot_result(data)

    conn.execute(
        """UPDATE games SET
               game_date = %s, division = %s, division_id = %s, home_team = %s, home_color = %s,
               home_final_score = %s, away_team = %s, away_color = %s,
               away_final_score = %s, went_to_shootout = %s, winner = %s,
               ot_winner = %s, ot_loser = %s
           WHERE id = %s""",
        (
            data.get("game_date"), data.get("division"), division_id,
            data.get("home_team"), data.get("home_color"), data.get("home_final_score"),
            data.get("away_team"), data.get("away_color"), data.get("away_final_score"),
            went_to_shootout, winner, ot_winner, ot_loser, game_id,
        ),
    )

    conn.execute("DELETE FROM goals WHERE game_id = %s", (game_id,))
    conn.execute("DELETE FROM penalties WHERE game_id = %s", (game_id,))
    conn.execute("DELETE FROM shootout_attempts WHERE game_id = %s", (game_id,))
    insert_stat_rows(conn, game_id, data)

    conn.commit()
    register_players_from_game(conn, data, division_id)


def delete_game(conn: PGConnection, game_id: int):
    """Permanently remove a game and its goals/penalties/shootout_attempts
    (cascaded via foreign keys — see schema_postgres.sql). Games aren't
    soft-deleted/recycled like divisions: a single mis-entered game is easy
    enough to re-process from its sheet if removed by mistake, so a
    straight delete keeps this simple."""
    conn.execute("DELETE FROM games WHERE id = %s", (game_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Divisions (season + year + age group)
# ---------------------------------------------------------------------------

def list_divisions(conn: PGConnection) -> list[dict]:
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


def add_division(conn: PGConnection, year: int, season: str, age_group: str,
                  category: str | None = None) -> int:
    season = normalize_text(season)
    age_group = match_age_group(age_group) or age_group.strip()
    category = category or AGE_GROUPS.get(age_group, "")
    conn.execute(
        "INSERT INTO divisions (year, season, age_group, category) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (year, season, age_group) DO NOTHING",
        (year, season, age_group, category),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM divisions WHERE year = %s AND season = %s AND age_group = %s",
        (year, season, age_group),
    ).fetchone()[0]


_SEASON_ORDER = {"spring": 0, "summer": 1, "fall": 2, "winter": 3}


def find_previous_division(conn: PGConnection, division_id: int) -> dict | None:
    """The most recent *other* division with the same age group that comes
    chronologically before this one (by year, then season within a year —
    Spring < Summer < Fall < Winter) — this age group's prior division, for
    carrying its coaches forward (see the Divisions page's "Assign Coaches
    From Last Division"). None if this is the earliest division on record
    for this age group."""
    current = conn.execute(
        "SELECT year, season, age_group FROM divisions WHERE id = %s", (division_id,)
    ).fetchone()
    if current is None:
        return None
    year, season, age_group = current
    current_key = (year, _SEASON_ORDER.get((season or "").lower(), 99))

    best, best_key = None, None
    for row in conn.execute(
        "SELECT id, year, season, age_group, category FROM divisions "
        "WHERE age_group = %s AND id != %s AND deleted_at IS NULL",
        (age_group, division_id),
    ).fetchall():
        key = (row[1], _SEASON_ORDER.get((row[2] or "").lower(), 99))
        if key < current_key and (best_key is None or key > best_key):
            best, best_key = row, key

    if best is None:
        return None
    return {"id": best[0], "year": best[1], "season": display_text(best[2]), "age_group": best[3], "category": best[4]}


def resolve_division_id(conn: PGConnection, working_division_id: int, age_group: str | None) -> int:
    """Find-or-create the division that a game actually belongs to: the same
    year/season as the working division, but this game's own age group
    (which is usually the working division's age group, but may differ if
    this particular sheet is for a different age group)."""
    row = conn.execute(
        "SELECT year, season, age_group FROM divisions WHERE id = %s", (working_division_id,)
    ).fetchone()
    if row is None:
        raise ValueError("No working division selected.")
    year, season, working_age_group = row
    resolved_age_group = match_age_group(age_group) or age_group or working_age_group
    return add_division(conn, year, season, resolved_age_group)


def soft_delete_division(conn: PGConnection, division_id: int):
    """Move a division to the recycle bin — it's purged for good 30 days
    later (see purge_expired_divisions), or can be restored before then."""
    conn.execute(
        "UPDATE divisions SET deleted_at = %s WHERE id = %s",
        (_utcnow().isoformat(timespec="seconds"), division_id),
    )
    conn.commit()


def restore_division(conn: PGConnection, division_id: int):
    conn.execute("UPDATE divisions SET deleted_at = NULL WHERE id = %s", (division_id,))
    conn.commit()


def list_deleted_divisions(conn: PGConnection) -> list[dict]:
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
        days_left = max(0, 30 - (_utcnow() - deleted_at).days)
        result.append({
            "id": r[0], "year": r[1], "season": display_text(r[2]), "age_group": r[3],
            "category": r[4], "deleted_at": r[5], "days_left": days_left,
        })
    return result


def purge_expired_divisions(conn: PGConnection, days: int = 30):
    """Permanently delete divisions that have been in the recycle bin more
    than `days` days. Called automatically on every init_db()."""
    cutoff = (_utcnow() - timedelta(days=days)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM divisions WHERE deleted_at IS NOT NULL AND deleted_at <= %s", (cutoff,))
    conn.commit()


# ---------------------------------------------------------------------------
# Team rosters
# ---------------------------------------------------------------------------

def list_teams(conn: PGConnection, division_id: int, include_deleted: bool = False) -> list[dict]:
    where = "WHERE division_id = %s" + ("" if include_deleted else " AND deleted_at IS NULL")
    rows = conn.execute(
        f"SELECT id, name, color, deleted_at FROM teams {where} ORDER BY name", (division_id,)
    ).fetchall()
    return [{"id": r[0], "name": display_text(r[1]), "color": r[2], "deleted_at": r[3]} for r in rows]


def add_team(conn: PGConnection, division_id: int, name: str) -> int:
    name = normalize_text(name)
    conn.execute(
        "INSERT INTO teams (division_id, name) VALUES (%s, %s) "
        "ON CONFLICT (division_id, name) DO NOTHING",
        (division_id, name),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM teams WHERE division_id = %s AND name = %s", (division_id, name)
    ).fetchone()[0]


def update_team(conn: PGConnection, team_id: int, **fields):
    """Update a team's editable fields (name, color), e.g.
    update_team(conn, 5, color="Red"). Raises ValueError if renaming would
    collide with another team already in the same division."""
    allowed = {"name", "color"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    if "name" in updates:
        updates["name"] = normalize_text(updates["name"])
        row = conn.execute("SELECT division_id FROM teams WHERE id = %s", (team_id,)).fetchone()
        if row is None:
            raise ValueError("Team not found.")
        conflict = conn.execute(
            "SELECT id FROM teams WHERE division_id = %s AND name = %s AND id != %s",
            (row[0], updates["name"], team_id),
        ).fetchone()
        if conflict:
            raise ValueError(f"A team named {display_text(updates['name'])!r} already exists in this division.")
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    conn.execute(f"UPDATE teams SET {set_clause} WHERE id = %s", (*updates.values(), team_id))
    conn.commit()


def soft_delete_team(conn: PGConnection, team_id: int):
    conn.execute(
        "UPDATE teams SET deleted_at = %s WHERE id = %s",
        (_utcnow().isoformat(timespec="seconds"), team_id),
    )
    conn.commit()


def restore_team(conn: PGConnection, team_id: int):
    conn.execute("UPDATE teams SET deleted_at = NULL WHERE id = %s", (team_id,))
    conn.commit()


def list_deleted_teams(conn: PGConnection) -> list[dict]:
    """Every soft-deleted team, across every division, with enough division
    context (year/season/age_group) to show which one it belonged to —
    used by the Divisions tab's recycle bin."""
    rows = conn.execute(
        """SELECT t.id, t.name, t.color, t.deleted_at, t.division_id, d.year, d.season, d.age_group
           FROM teams t JOIN divisions d ON d.id = t.division_id
           WHERE t.deleted_at IS NOT NULL
           ORDER BY t.deleted_at DESC"""
    ).fetchall()
    cols = ["id", "name", "color", "deleted_at", "division_id", "year", "season", "age_group"]
    teams = [dict(zip(cols, r)) for r in rows]
    for t in teams:
        t["name"] = display_text(t["name"])
    return teams


# A jersey number is usually numeric but can be a placeholder like "G" or
# "?" for oddities on a scanned sheet. SQLite's CAST(x AS INTEGER) silently
# returns 0 for non-numeric text, so ordering by it was always safe;
# Postgres's CAST raises on non-numeric input, so ordering guards with a
# regex check first and falls back to NULL (sorted last) for non-numeric
# values instead of erroring the whole query.
_NUMERIC_SORT_KEY = "CASE WHEN {col} ~ '^[0-9]+$' THEN CAST({col} AS INTEGER) END"


def list_roster(conn: PGConnection, team_id: int) -> list[dict]:
    """A team's roster. Once a row is linked to a global player profile,
    its name is taken from that profile (kept live if the player is later
    renamed) rather than the name originally auto-extracted from the game
    sheet into roster_entries.name. A row whose linked player has been
    soft-deleted reverts to unlinked (player_id None, original scanned
    name) until that player is restored or a new one is linked — the
    roster_entries.player_id FK itself is left alone, so restoring the
    player re-links it automatically."""
    sort_key = _NUMERIC_SORT_KEY.format(col="re.number")
    rows = conn.execute(
        f"""SELECT re.id, re.number,
                   COALESCE(NULLIF(TRIM(CONCAT_WS(' ', p.first_name, p.last_name)), ''), re.name), p.id
           FROM roster_entries re
           LEFT JOIN players p ON p.id = re.player_id AND p.deleted_at IS NULL
           WHERE re.team_id = %s ORDER BY {sort_key}, re.number""",
        (team_id,),
    ).fetchall()
    return [{"id": r[0], "number": r[1], "name": display_text(r[2]), "player_id": r[3]} for r in rows]


def replace_roster(conn: PGConnection, team_id: int, entries: list[dict]):
    """Replace a team's whole roster with the given (number, name) rows. A
    row's link to a global player profile is preserved by jersey number
    match, since the roster editor doesn't expose player_id directly."""
    existing_player_ids = dict(
        conn.execute(
            "SELECT number, player_id FROM roster_entries WHERE team_id = %s", (team_id,)
        ).fetchall()
    )
    conn.execute("DELETE FROM roster_entries WHERE team_id = %s", (team_id,))
    for p in entries:
        number = (p.get("number") or "").strip()
        name = normalize_text(p.get("name")) or ""
        if not number and not name:
            continue
        conn.execute(
            "INSERT INTO roster_entries (team_id, number, name, player_id) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (team_id, number) DO NOTHING",
            (team_id, number, name, existing_player_ids.get(number)),
        )
    conn.commit()


def link_roster_entry_to_player(conn: PGConnection, roster_entry_id: int, player_id: int):
    """Identify a roster entry (a jersey number seen on a game sheet) as a
    specific global player profile."""
    conn.execute(
        "UPDATE roster_entries SET player_id = %s WHERE id = %s", (player_id, roster_entry_id)
    )
    conn.commit()


def add_roster_entry(conn: PGConnection, team_id: int, number: str, name: str, player_id: int | None = None) -> int:
    """A single new roster row — unlike replace_roster (which replaces a
    team's whole roster from the editor grid), this adds just one, e.g. for
    a drafted player."""
    cur = conn.execute(
        "INSERT INTO roster_entries (team_id, number, name, player_id) VALUES (%s, %s, %s, %s) RETURNING id",
        (team_id, number.strip(), normalize_text(name) or "", player_id),
    )
    entry_id = cur.fetchone()[0]
    conn.commit()
    return entry_id


# ---------------------------------------------------------------------------
# Global players (identity persists across every division/season)
# ---------------------------------------------------------------------------

def list_players(conn: PGConnection, include_deleted: bool = False) -> list[dict]:
    where = "" if include_deleted else "WHERE deleted_at IS NULL"
    rows = conn.execute(
        f"""SELECT id, first_name, last_name, nickname, birth_date, current_division_id,
                   contact_first_name, contact_last_name, contact_phone, contact_email, deleted_at,
                   parent_id
            FROM players {where} ORDER BY last_name, first_name"""
    ).fetchall()
    cols = ["id", "first_name", "last_name", "nickname", "birth_date", "current_division_id",
            "contact_first_name", "contact_last_name", "contact_phone", "contact_email", "deleted_at",
            "parent_id"]
    players = [dict(zip(cols, r)) for r in rows]
    # "name" is a derived display convenience (not a real column — see
    # first_name/last_name above), kept so the many read-only call sites
    # that just want "the player's name" don't need to know about the split.
    for p in players:
        p["name"] = full_name(p["first_name"], p["last_name"])
    return players


def get_player(conn: PGConnection, player_id: int) -> dict | None:
    players = {p["id"]: p for p in list_players(conn, include_deleted=True)}
    return players.get(player_id)


def list_players_in_division(conn: PGConnection, division_id: int) -> list[dict]:
    """Every player "in" a division — the union of two groups that don't
    always overlap: players whose profile's current_division_id points
    here (signed up, possibly not yet on a team) and players on any of
    this division's team rosters (which can happen without
    current_division_id being updated, e.g. drafted straight onto a team
    without the profile being touched). Each row also carries "teams": the
    names of any of this division's teams they're rostered on (empty if
    signed-up-only)."""
    id_rows = conn.execute(
        """SELECT DISTINCT p.id FROM players p
           WHERE p.deleted_at IS NULL AND (
               p.current_division_id = %s
               OR p.id IN (
                   SELECT re.player_id FROM roster_entries re
                   JOIN teams t ON t.id = re.team_id
                   WHERE t.division_id = %s AND re.player_id IS NOT NULL
               )
           )""",
        (division_id, division_id),
    ).fetchall()
    player_ids = [r[0] for r in id_rows]
    if not player_ids:
        return []

    by_id = {p["id"]: p for p in list_players(conn)}
    team_rows = conn.execute(
        """SELECT re.player_id, t.name FROM roster_entries re
           JOIN teams t ON t.id = re.team_id
           WHERE t.division_id = %s AND re.player_id = ANY(%s)""",
        (division_id, player_ids),
    ).fetchall()
    teams_by_player: dict[int, list[str]] = {}
    for player_id, team_name in team_rows:
        teams_by_player.setdefault(player_id, []).append(display_text(team_name))

    result = []
    for player_id in player_ids:
        p = dict(by_id[player_id])
        p["teams"] = sorted(teams_by_player.get(player_id, []))
        result.append(p)
    result.sort(key=lambda p: (p["last_name"] or "", p["first_name"] or ""))
    return result


# ---------------------------------------------------------------------------
# Parents — a shared entity multiple children (players) can link to, so
# "siblings" is simply "players who share a parent" rather than a pairwise
# link that gets awkward past two kids. Auto-matched from contact info on
# add/update (see get_or_create_parent, called from add_player/
# update_player) and backfilled once for players who predate this feature
# (see backfill_player_parents, run automatically by init_db) — a
# best-effort match, not a guarantee, correctable from the player's own
# profile via set_player_parent.
# ---------------------------------------------------------------------------

def _normalize_phone(phone: str | None) -> str | None:
    """Digits only, so "412-555-0100", "(412) 555-0100", and "4125550100"
    all compare equal."""
    if not phone:
        return None
    digits = "".join(c for c in phone if c.isdigit())
    return digits or None


def get_or_create_parent(
    conn: PGConnection, first_name: str | None, last_name: str | None,
    phone: str | None, email: str | None,
) -> int | None:
    """Find-or-create the parent/guardian identified by this contact info.
    Matches, in priority order (whichever this contact info actually has),
    on email, then phone, then first+last name — all case/formatting-
    insensitive. Returns None if there's not enough identifying info to
    safely match or create one at all (no name, no phone, no email)."""
    email_norm = normalize_text(email) or None
    phone_norm = _normalize_phone(phone)
    first_norm = normalize_text(first_name) or None
    last_norm = normalize_text(last_name) or None

    if not (email_norm or phone_norm or first_norm or last_norm):
        return None

    if email_norm:
        row = conn.execute("SELECT id FROM parents WHERE LOWER(email) = %s", (email_norm,)).fetchone()
        if row:
            return row[0]
    if phone_norm:
        for pid, existing_phone in conn.execute(
            "SELECT id, phone FROM parents WHERE phone IS NOT NULL"
        ).fetchall():
            if _normalize_phone(existing_phone) == phone_norm:
                return pid
    if first_norm and last_norm:
        row = conn.execute(
            "SELECT id FROM parents WHERE LOWER(first_name) = %s AND LOWER(last_name) = %s",
            (first_norm, last_norm),
        ).fetchone()
        if row:
            return row[0]

    cur = conn.execute(
        "INSERT INTO parents (first_name, last_name, phone, email) VALUES (%s, %s, %s, %s) RETURNING id",
        (
            first_name.strip() if first_name else None, last_name.strip() if last_name else None,
            phone.strip() if phone else None, email.strip() if email else None,
        ),
    )
    parent_id = cur.fetchone()[0]
    conn.commit()
    return parent_id


def list_parents(conn: PGConnection) -> list[dict]:
    """Every parent on file — for a "search existing parent" picker when
    manually linking/correcting a player's parent."""
    rows = conn.execute(
        "SELECT id, first_name, last_name, phone, email FROM parents ORDER BY last_name, first_name"
    ).fetchall()
    cols = ["id", "first_name", "last_name", "phone", "email"]
    parents = [dict(zip(cols, r)) for r in rows]
    for p in parents:
        p["name"] = full_name(p["first_name"], p["last_name"]) or "(no name on file)"
    return parents


def set_player_parent(conn: PGConnection, player_id: int, parent_id: int | None):
    """Manually link (parent_id given) or unlink (parent_id=None) a player
    to a parent/sibling-group — for correcting a get_or_create_parent
    auto-match that got it wrong, or linking two profiles that weren't
    automatically matched (e.g. no contact info on file for one of them)."""
    conn.execute("UPDATE players SET parent_id = %s WHERE id = %s", (parent_id, player_id))
    conn.commit()


def list_siblings(conn: PGConnection, player_id: int) -> list[dict]:
    """Other non-deleted players who share this player's parent — empty if
    this player has no parent linked yet."""
    player = get_player(conn, player_id)
    if not player or not player.get("parent_id"):
        return []
    return [p for p in list_players(conn) if p["parent_id"] == player["parent_id"] and p["id"] != player_id]


def backfill_player_parents(conn: PGConnection) -> int:
    """One-time (but safe to re-run — skips anyone already linked) pass
    matching every player who has contact info on file but no parent_id
    yet, for players added before this feature existed. Run automatically
    by init_db. Returns how many were newly linked."""
    linked = 0
    for p in list_players(conn, include_deleted=True):
        if p.get("parent_id"):
            continue
        parent_id = get_or_create_parent(
            conn, p.get("contact_first_name"), p.get("contact_last_name"),
            p.get("contact_phone"), p.get("contact_email"),
        )
        if parent_id:
            conn.execute("UPDATE players SET parent_id = %s WHERE id = %s", (parent_id, p["id"]))
            linked += 1
    conn.commit()
    return linked


def add_player(
    conn: PGConnection, first_name: str, last_name: str | None = None, nickname: str | None = None,
    birth_date: str | None = None, current_division_id: int | None = None,
    contact_first_name: str | None = None, contact_last_name: str | None = None,
    contact_phone: str | None = None, contact_email: str | None = None,
) -> int:
    # Auto-links this player to a parent/sibling-group matched (or created)
    # from the contact info given here — see get_or_create_parent(). A
    # best-effort match, not a guarantee (correctable afterward from the
    # player's profile), which is why this never blocks on it and just
    # takes whatever comes back, including None.
    parent_id = get_or_create_parent(conn, contact_first_name, contact_last_name, contact_phone, contact_email)
    cur = conn.execute(
        """INSERT INTO players
           (first_name, last_name, nickname, birth_date, current_division_id, contact_first_name,
            contact_last_name, contact_phone, contact_email, parent_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        (first_name.strip(), (last_name or "").strip() or None, (nickname or "").strip() or None,
         birth_date, current_division_id, contact_first_name, contact_last_name, contact_phone, contact_email,
         parent_id),
    )
    player_id = cur.fetchone()[0]
    conn.commit()
    return player_id


def update_player(conn: PGConnection, player_id: int, **fields):
    """Update any subset of a player's profile fields, e.g.
    update_player(conn, 5, first_name="Alex", contact_phone="412-555-0100").

    Touching any contact_* field re-derives parent_id too (merged with
    whichever contact fields aren't part of this update), so editing just
    the phone number, say, still matches/creates the right parent using
    the name already on file rather than losing that context."""
    allowed = {
        "first_name", "last_name", "nickname", "birth_date", "current_division_id",
        "contact_first_name", "contact_last_name", "contact_phone", "contact_email",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    contact_fields = ("contact_first_name", "contact_last_name", "contact_phone", "contact_email")
    if any(f in updates for f in contact_fields):
        current = get_player(conn, player_id) or {}
        merged = {f: updates.get(f, current.get(f)) for f in contact_fields}
        updates["parent_id"] = get_or_create_parent(conn, *(merged[f] for f in contact_fields))
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    conn.execute(
        f"UPDATE players SET {set_clause} WHERE id = %s", (*updates.values(), player_id)
    )
    conn.commit()


def soft_delete_player(conn: PGConnection, player_id: int):
    conn.execute(
        "UPDATE players SET deleted_at = %s WHERE id = %s",
        (_utcnow().isoformat(timespec="seconds"), player_id),
    )
    conn.commit()


def restore_player(conn: PGConnection, player_id: int):
    conn.execute("UPDATE players SET deleted_at = NULL WHERE id = %s", (player_id,))
    conn.commit()


def player_division_history(conn: PGConnection, player_id: int) -> list[dict]:
    """Every division this player has a roster entry in (with the team and
    jersey number they wore), derived by joining roster_entries -> teams ->
    divisions rather than tracked separately, so it can never drift out of
    sync with the actual rosters."""
    rows = conn.execute(
        """SELECT DISTINCT d.id, d.year, d.season, d.age_group, d.category, t.name, re.number, t.id
           FROM roster_entries re
           JOIN teams t ON t.id = re.team_id
           JOIN divisions d ON d.id = t.division_id
           WHERE re.player_id = %s
           ORDER BY d.year DESC, d.season""",
        (player_id,),
    ).fetchall()
    return [
        {
            "division_id": r[0], "year": r[1], "season": display_text(r[2]),
            "age_group": r[3], "category": r[4], "team_name": display_text(r[5]), "number": r[6],
            "team_id": r[7],
        }
        for r in rows
    ]


def player_division_histories(conn: PGConnection, player_ids: list[int]) -> dict[int, list[dict]]:
    """Same data as player_division_history, for every id in player_ids at
    once — one query instead of one per player. Used wherever a list of
    many players needs their history (e.g. the Players tab's picker
    labels), since that N+1 pattern was slow enough over a remote database
    connection to make the whole app feel unresponsive on every rerun."""
    if not player_ids:
        return {}
    rows = conn.execute(
        """SELECT re.player_id, d.id, d.year, d.season, d.age_group, d.category, t.name, re.number, t.id
           FROM roster_entries re
           JOIN teams t ON t.id = re.team_id
           JOIN divisions d ON d.id = t.division_id
           WHERE re.player_id = ANY(%s)
           ORDER BY re.player_id, d.year DESC, d.season""",
        (player_ids,),
    ).fetchall()
    result: dict[int, list[dict]] = {}
    for r in rows:
        result.setdefault(r[0], []).append({
            "division_id": r[1], "year": r[2], "season": display_text(r[3]),
            "age_group": r[4], "category": r[5], "team_name": display_text(r[6]), "number": r[7],
            "team_id": r[8],
        })
    return result


def get_positions_for_team(conn: PGConnection, division_id: int, team_id: int) -> dict[int, str]:
    """Every position already set for this team/division, player_id ->
    position, in one query — used to batch-populate Team Rosters' Position
    column instead of one get_position() round trip per row."""
    rows = conn.execute(
        "SELECT player_id, position FROM player_positions WHERE division_id = %s AND team_id = %s",
        (division_id, team_id),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def get_season_grades_for_division(conn: PGConnection, division_id: int) -> dict[int, str]:
    """Every player's most recent evaluation grade for this division,
    player_id -> grade, in one query — used to batch-populate Team
    Rosters' Season Grade column instead of one get_season_grade() round
    trip per row."""
    rows = conn.execute(
        """SELECT DISTINCT ON (player_id) player_id, grade
           FROM evaluations WHERE division_id = %s
           ORDER BY player_id, created_at DESC, id DESC""",
        (division_id,),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def list_evaluated_player_ids(conn: PGConnection, division_id: int) -> set[int]:
    """Every player_id with at least one evaluation recorded for this
    division — used to filter for "has a rating on file for division X",
    e.g. checking who was already evaluated in a past season."""
    rows = conn.execute(
        "SELECT DISTINCT player_id FROM evaluations WHERE division_id = %s", (division_id,)
    ).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Player draft — a live, snake-order draft of a division's registered-but-
# unrostered players onto its teams. One active draft per division; delete
# it (delete_draft) to start over.
# ---------------------------------------------------------------------------

def get_draft(conn: PGConnection, division_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, division_id, status, current_pick_number FROM drafts WHERE division_id = %s",
        (division_id,),
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "division_id": row[1], "status": row[2], "current_pick_number": row[3]}


def list_draft_order(conn: PGConnection, draft_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT o.slot, o.team_id, t.name FROM draft_order o
           JOIN teams t ON t.id = o.team_id
           WHERE o.draft_id = %s ORDER BY o.slot""",
        (draft_id,),
    ).fetchall()
    return [{"slot": r[0], "team_id": r[1], "team_name": display_text(r[2])} for r in rows]


def draft_pool(conn: PGConnection, division_id: int) -> list[dict]:
    """Players eligible to be drafted: registered for this division
    (current_division_id) and not already on any of its teams' rosters."""
    rows = conn.execute(
        """SELECT p.id, p.first_name, p.last_name, p.nickname, p.birth_date
           FROM players p
           WHERE p.current_division_id = %s AND p.deleted_at IS NULL
             AND p.id NOT IN (
                 SELECT re.player_id FROM roster_entries re
                 JOIN teams t ON t.id = re.team_id
                 WHERE t.division_id = %s AND re.player_id IS NOT NULL
             )
           ORDER BY p.last_name, p.first_name""",
        (division_id, division_id),
    ).fetchall()
    cols = ["id", "first_name", "last_name", "nickname", "birth_date"]
    players = [dict(zip(cols, r)) for r in rows]
    for p in players:
        p["name"] = full_name(p["first_name"], p["last_name"])
    return players


def _snake_team_id(order: list[dict], pick_number: int) -> tuple[int, int]:
    """(team_id, round) for a 1-based overall pick_number, given round-1
    order — odd rounds go slot 1..N, even rounds reverse to N..1."""
    n = len(order)
    round_num = (pick_number - 1) // n + 1
    pos_in_round = (pick_number - 1) % n
    if round_num % 2 == 0:
        pos_in_round = n - 1 - pos_in_round
    return order[pos_in_round]["team_id"], round_num


def current_pick_team_id(conn: PGConnection, draft_id: int) -> int | None:
    """The team whose turn the current pick is — None if the draft has no
    team order (shouldn't happen once started) or has finished."""
    order = list_draft_order(conn, draft_id)
    draft = conn.execute(
        "SELECT status, current_pick_number FROM drafts WHERE id = %s", (draft_id,)
    ).fetchone()
    if not order or draft is None or draft[0] != "in_progress":
        return None
    team_id, _ = _snake_team_id(order, draft[1])
    return team_id


def start_draft(conn: PGConnection, division_id: int, team_ids_in_order: list[int]) -> int:
    """Create a new draft for a division with the given round-1 team order.
    Raises ValueError if one's already active for this division."""
    if get_draft(conn, division_id) is not None:
        raise ValueError("A draft already exists for this division — delete it first to start over.")
    if not team_ids_in_order:
        raise ValueError("Need at least one team to draft into.")
    cur = conn.execute("INSERT INTO drafts (division_id) VALUES (%s) RETURNING id", (division_id,))
    draft_id = cur.fetchone()[0]
    for slot, team_id in enumerate(team_ids_in_order, start=1):
        conn.execute(
            "INSERT INTO draft_order (draft_id, team_id, slot) VALUES (%s, %s, %s)", (draft_id, team_id, slot)
        )
    conn.commit()
    return draft_id


def delete_draft(conn: PGConnection, draft_id: int):
    """Deletes the draft's own tracking (order/pick history) only — NOT the
    roster rows its picks already created, which by now are just normal
    roster entries like any other (edit/remove those from Team Rosters)."""
    conn.execute("DELETE FROM drafts WHERE id = %s", (draft_id,))
    conn.commit()


def submit_draft_pick(conn: PGConnection, draft_id: int, player_id: int) -> int:
    """Records the next pick for whichever team's turn it is, and creates a
    roster row for that player on that team — jersey number left as a
    placeholder ("TBD<n>") for the coach to fill in later via Team Rosters,
    same as any other roster row. Raises ValueError if the draft isn't in
    progress or the player isn't in its pool. Returns the new roster row's
    id."""
    draft = conn.execute(
        "SELECT status, current_pick_number, division_id FROM drafts WHERE id = %s", (draft_id,)
    ).fetchone()
    if draft is None:
        raise ValueError("Draft not found.")
    status, pick_number, division_id = draft
    if status != "in_progress":
        raise ValueError("This draft has already finished.")

    order = list_draft_order(conn, draft_id)
    team_id, round_num = _snake_team_id(order, pick_number)

    pool = draft_pool(conn, division_id)
    pool_ids = {p["id"] for p in pool}
    if player_id not in pool_ids:
        raise ValueError(
            "That player isn't in this draft's pool (already rostered, or not registered for this division)."
        )

    already_on_team = conn.execute(
        "SELECT COUNT(*) FROM roster_entries WHERE team_id = %s AND number LIKE 'TBD%%'", (team_id,)
    ).fetchone()[0]
    player = get_player(conn, player_id)
    roster_entry_id = add_roster_entry(conn, team_id, f"TBD{already_on_team + 1}", player["name"], player_id=player_id)

    conn.execute(
        """INSERT INTO draft_picks (draft_id, pick_number, round, team_id, player_id, roster_entry_id)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (draft_id, pick_number, round_num, team_id, player_id, roster_entry_id),
    )
    new_status = "completed" if len(pool_ids) <= 1 else "in_progress"
    conn.execute(
        "UPDATE drafts SET current_pick_number = %s, status = %s WHERE id = %s",
        (pick_number + 1, new_status, draft_id),
    )
    conn.commit()
    return roster_entry_id


def undo_last_pick(conn: PGConnection, draft_id: int):
    """Removes the most recent pick and the roster row it created, and
    rewinds current_pick_number so that pick is up for grabs again."""
    last = conn.execute(
        "SELECT id, pick_number, roster_entry_id FROM draft_picks WHERE draft_id = %s ORDER BY pick_number DESC LIMIT 1",
        (draft_id,),
    ).fetchone()
    if last is None:
        raise ValueError("No picks to undo.")
    pick_id, pick_number, roster_entry_id = last
    conn.execute("DELETE FROM draft_picks WHERE id = %s", (pick_id,))
    if roster_entry_id is not None:
        conn.execute("DELETE FROM roster_entries WHERE id = %s", (roster_entry_id,))
    conn.execute(
        "UPDATE drafts SET current_pick_number = %s, status = 'in_progress' WHERE id = %s",
        (pick_number, draft_id),
    )
    conn.commit()


def list_draft_picks(conn: PGConnection, draft_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT dp.pick_number, dp.round, t.name, p.first_name, p.last_name, p.nickname, dp.picked_at
           FROM draft_picks dp
           JOIN teams t ON t.id = dp.team_id
           JOIN players p ON p.id = dp.player_id
           WHERE dp.draft_id = %s ORDER BY dp.pick_number""",
        (draft_id,),
    ).fetchall()
    cols = ["pick_number", "round", "team_name", "first_name", "last_name", "nickname", "picked_at"]
    picks = [dict(zip(cols, r)) for r in rows]
    for p in picks:
        p["team_name"] = display_text(p["team_name"])
        p["player_name"] = full_name(p["first_name"], p["last_name"])
    return picks


# ---------------------------------------------------------------------------
# Coaches (global; assigned to a team, which anchors them to one division)
# ---------------------------------------------------------------------------

def list_coaches(conn: PGConnection, include_deleted: bool = False) -> list[dict]:
    where = "" if include_deleted else "WHERE deleted_at IS NULL"
    rows = conn.execute(
        f"""SELECT id, first_name, last_name, nickname, phone, email, deleted_at
            FROM coaches {where} ORDER BY last_name, first_name"""
    ).fetchall()
    cols = ["id", "first_name", "last_name", "nickname", "phone", "email", "deleted_at"]
    coaches = [dict(zip(cols, r)) for r in rows]
    # "name" is a derived display convenience, same as players.name — see
    # list_players.
    for c in coaches:
        c["name"] = full_name(c["first_name"], c["last_name"])
    return coaches


def get_coach(conn: PGConnection, coach_id: int) -> dict | None:
    coaches = {c["id"]: c for c in list_coaches(conn, include_deleted=True)}
    return coaches.get(coach_id)


def add_coach(
    conn: PGConnection, first_name: str, last_name: str | None = None, nickname: str | None = None,
    phone: str | None = None, email: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO coaches (first_name, last_name, nickname, phone, email)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (first_name.strip(), (last_name or "").strip() or None, (nickname or "").strip() or None,
         phone, email),
    )
    coach_id = cur.fetchone()[0]
    conn.commit()
    return coach_id


def update_coach(conn: PGConnection, coach_id: int, **fields):
    """Update any subset of a coach's profile fields, e.g.
    update_coach(conn, 5, first_name="Alex", phone="412-555-0100")."""
    allowed = {"first_name", "last_name", "nickname", "phone", "email"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    conn.execute(f"UPDATE coaches SET {set_clause} WHERE id = %s", (*updates.values(), coach_id))
    conn.commit()


def soft_delete_coach(conn: PGConnection, coach_id: int):
    conn.execute(
        "UPDATE coaches SET deleted_at = %s WHERE id = %s",
        (_utcnow().isoformat(timespec="seconds"), coach_id),
    )
    conn.commit()


def restore_coach(conn: PGConnection, coach_id: int):
    conn.execute("UPDATE coaches SET deleted_at = NULL WHERE id = %s", (coach_id,))
    conn.commit()


def assign_coach_to_team(conn: PGConnection, team_id: int, coach_id: int):
    """Raises ValueError in either direction of a one-team-one-coach rule:
    if this team already has a *different* coach assigned (a team has
    exactly one coach at a time — remove the existing one first to replace
    them), or if this coach already coaches a different team in the same
    division (a coach can coach only one team per division, though that's
    still multiple teams across a season's different divisions, e.g. U10
    Summer and U13 Summer, or across different seasons)."""
    row = conn.execute("SELECT division_id FROM teams WHERE id = %s", (team_id,)).fetchone()
    if row is None:
        raise ValueError("Team not found.")
    division_id = row[0]

    existing_team_coach = conn.execute(
        "SELECT c.first_name, c.last_name FROM team_coaches tc JOIN coaches c ON c.id = tc.coach_id "
        "WHERE tc.team_id = %s AND tc.coach_id != %s",
        (team_id, coach_id),
    ).fetchone()
    if existing_team_coach:
        raise ValueError(
            f"This team already has a coach ({full_name(*existing_team_coach)}) — remove them first."
        )

    conflict = conn.execute(
        """SELECT t.name FROM team_coaches tc
           JOIN teams t ON t.id = tc.team_id
           WHERE tc.coach_id = %s AND t.division_id = %s AND t.id != %s""",
        (coach_id, division_id, team_id),
    ).fetchone()
    if conflict:
        raise ValueError(f"This coach already coaches {conflict[0]} in this division.")
    conn.execute(
        "INSERT INTO team_coaches (team_id, coach_id) VALUES (%s, %s) "
        "ON CONFLICT (team_id, coach_id) DO NOTHING",
        (team_id, coach_id),
    )
    conn.commit()


def remove_coach_from_team(conn: PGConnection, team_id: int, coach_id: int):
    conn.execute(
        "DELETE FROM team_coaches WHERE team_id = %s AND coach_id = %s", (team_id, coach_id)
    )
    conn.commit()


def list_team_coaches(conn: PGConnection, team_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT c.id, c.first_name, c.last_name, c.nickname FROM team_coaches tc
           JOIN coaches c ON c.id = tc.coach_id
           WHERE tc.team_id = %s AND c.deleted_at IS NULL ORDER BY c.last_name, c.first_name""",
        (team_id,),
    ).fetchall()
    return [
        {"id": r[0], "first_name": r[1], "last_name": r[2], "nickname": r[3], "name": full_name(r[1], r[2])}
        for r in rows
    ]


def list_team_coaches_for_division(conn: PGConnection, division_id: int) -> dict[int, list[dict]]:
    """Every team's assigned coach(es) in this division, team_id -> [coach
    dicts] (empty list if none), in one query — used to label team pickers
    with who coaches each one without a list_team_coaches() round trip per
    team. Teams with no coach still get a (empty-list) entry, via the LEFT
    JOIN, so callers don't need a fallback for a missing key."""
    rows = conn.execute(
        """SELECT t.id, c.id, c.first_name, c.last_name, c.nickname
           FROM teams t
           LEFT JOIN team_coaches tc ON tc.team_id = t.id
           LEFT JOIN coaches c ON c.id = tc.coach_id AND c.deleted_at IS NULL
           WHERE t.division_id = %s
           ORDER BY t.id, c.last_name, c.first_name""",
        (division_id,),
    ).fetchall()
    result: dict[int, list[dict]] = {}
    for team_id, coach_id, first_name, last_name, nickname in rows:
        result.setdefault(team_id, [])
        if coach_id is not None:
            result[team_id].append({
                "id": coach_id, "first_name": first_name, "last_name": last_name, "nickname": nickname,
                "name": full_name(first_name, last_name),
            })
    return result


def list_coach_teams(conn: PGConnection, coach_id: int) -> list[dict]:
    """Every team this coach is/has been assigned to, across every division
    and season — since teams are scoped one-per-season (a returning coach
    gets a new team_coaches row each season rather than reusing last
    season's team_id), this naturally accumulates the coach's full
    multi-season coaching history, not just their current assignment(s)."""
    rows = conn.execute(
        """SELECT t.id, t.name, d.id, d.year, d.season, d.age_group, d.category
           FROM team_coaches tc
           JOIN teams t ON t.id = tc.team_id
           JOIN divisions d ON d.id = t.division_id
           WHERE tc.coach_id = %s
           ORDER BY d.year DESC, d.season, t.name""",
        (coach_id,),
    ).fetchall()
    cols = ["team_id", "team_name", "division_id", "year", "season", "age_group", "category"]
    return [dict(zip(cols, r)) for r in rows]


# --- Coach children: explicit link from a coach to their own registered --
# player(s), so "does this coach have kids registered" and "which ones" can
# be answered directly rather than guessed from a name/contact-info match.

def list_coach_children(conn: PGConnection, coach_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT p.id, p.first_name, p.last_name, p.nickname, p.current_division_id
           FROM coach_children cc JOIN players p ON p.id = cc.player_id
           WHERE cc.coach_id = %s AND p.deleted_at IS NULL
           ORDER BY p.last_name, p.first_name""",
        (coach_id,),
    ).fetchall()
    cols = ["id", "first_name", "last_name", "nickname", "current_division_id"]
    children = [dict(zip(cols, r)) for r in rows]
    for c in children:
        c["name"] = full_name(c["first_name"], c["last_name"])
    return children


def link_coach_child(conn: PGConnection, coach_id: int, player_id: int):
    conn.execute(
        "INSERT INTO coach_children (coach_id, player_id) VALUES (%s, %s) "
        "ON CONFLICT (coach_id, player_id) DO NOTHING",
        (coach_id, player_id),
    )
    conn.commit()


def unlink_coach_child(conn: PGConnection, coach_id: int, player_id: int):
    conn.execute("DELETE FROM coach_children WHERE coach_id = %s AND player_id = %s", (coach_id, player_id))
    conn.commit()


def coach_ids_with_children(conn: PGConnection) -> set[int]:
    """Every coach_id with at least one linked child — used to filter/flag
    "has children registered" without an N+1 query per coach."""
    rows = conn.execute("SELECT DISTINCT coach_id FROM coach_children").fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Auto-draft — a one-shot alternative to the live pick-by-pick draft above
# (start_draft/submit_draft_pick), which assigns the *entire* pool onto
# teams in a single pass instead of one pick at a time. Bypasses that flow
# entirely (no drafts/draft_order/draft_picks rows) because its hard
# placements -- siblings kept together, a coach's kid seated with their
# own parent's team -- can't be honored by submit_draft_pick's fixed snake
# turn order, which always determines the team for a pick, never the caller.
# ---------------------------------------------------------------------------

_AUTO_DRAFT_TIER_RANK = {"A": 0, "B": 1, "C": 2}  # anything else (D, or ungraded/"New") shares tier 3
_AUTO_DRAFT_TIER_SKILL = {0: 4, 1: 3, 2: 2, 3: 1}  # numeric skill value per tier, for balancing totals


def _birth_year(birth_date: str | None) -> int | None:
    if not birth_date:
        return None
    match = re.search(r"(19|20)\d{2}", birth_date)
    return int(match.group()) if match else None


def get_auto_draft_run(conn: PGConnection, division_id: int) -> dict | None:
    """The division's auto-draft run still available to undo — None once
    undone (undo_auto_draft deletes the row) or if auto_draft has never
    been run for this division."""
    row = conn.execute(
        "SELECT id, roster_entry_ids, coach_assignments, created_at FROM auto_draft_runs WHERE division_id = %s",
        (division_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "division_id": division_id,
        "roster_entry_ids": json.loads(row[1]), "coach_assignments": json.loads(row[2]),
        "created_at": row[3],
    }


def undo_auto_draft(conn: PGConnection, division_id: int):
    """Reverses this division's auto-draft run: removes exactly the roster
    rows and coach assignments it created (anything added or changed
    manually afterward is left alone), then clears the run record so the
    pool is back to how it was beforehand — ready for auto_draft() to be
    run again from scratch. Raises ValueError if there's nothing to undo."""
    run = get_auto_draft_run(conn, division_id)
    if run is None:
        raise ValueError("No auto-draft to undo for this division.")
    for roster_entry_id in run["roster_entry_ids"]:
        conn.execute("DELETE FROM roster_entries WHERE id = %s", (roster_entry_id,))
    for assignment in run["coach_assignments"]:
        conn.execute(
            "DELETE FROM team_coaches WHERE team_id = %s AND coach_id = %s",
            (assignment["team_id"], assignment["coach_id"]),
        )
    conn.execute("DELETE FROM auto_draft_runs WHERE id = %s", (run["id"],))
    conn.commit()


def auto_draft(conn: PGConnection, division_id: int) -> dict:
    """One-shot auto-draft: assigns every currently-undrafted player in this
    division's pool (see draft_pool) onto its teams in a single pass.

    Ranking: A > B > C > (D and ungraded/"New" players, treated as tied
    with each other, tie-broken by birth year — an older "New" player
    ranks above a D player rather than New always sorting last).

    Hard placements, resolved before the balanced pass so it treats them
    as already-seated when sizing up how full each team is:
      - Siblings (players sharing a parent, see the parents table) always
        land on the same team. If a sibling already has a team in this
        division from outside this run, the rest of the group is forced
        onto that team.
      - A coach's own registered child (coach_children) is forced onto
        whichever team that coach already coaches in this division.
    Coach-follows-kid: a coach who does *not* yet coach a team in this
    division, but whose child is auto-drafted here, is assigned to
    whichever team their child lands on afterward (skipped with a warning
    if that team already ended up with a different coach).

    Everyone else is assigned greedily, best-skill unit first, always to
    whichever team currently has the lowest total skill (ties broken by
    fewest players so far) — keeping both roster size and aggregate rank
    as even as possible across teams.

    Re-running this when a previous run exists for this division first
    undoes it (see undo_auto_draft), so re-drafting from scratch is just
    calling this again rather than a separate manual cleanup step.

    Raises ValueError if there are fewer than 2 teams or an empty pool.
    Returns {"assigned": n, "teams": n, "warnings": [...]}."""
    if get_auto_draft_run(conn, division_id) is not None:
        undo_auto_draft(conn, division_id)

    teams = list_teams(conn, division_id)
    if len(teams) < 2:
        raise ValueError("Add at least two teams to this division before auto-drafting.")
    pool = draft_pool(conn, division_id)
    if not pool:
        raise ValueError("No eligible players in this division's pool.")

    pool_by_id = {p["id"]: p for p in pool}
    grades = get_season_grades_for_division(conn, division_id)

    def tier_rank_of(player_id: int) -> int:
        tier = (grades.get(player_id) or "").strip().upper()
        return _AUTO_DRAFT_TIER_RANK.get(tier, 3)

    def skill_of(player_id: int) -> int:
        return _AUTO_DRAFT_TIER_SKILL[tier_rank_of(player_id)]

    def draft_order_key(player_id: int) -> tuple[int, int]:
        """Lower sorts first (drafted sooner). Only within tier 3 (D and
        ungraded/"New", deliberately tied with each other) does birth year
        break the tie — older (smaller year) first — per an older "New"
        player outranking a D player; A/B/C players don't need it since
        they're already separated by tier."""
        tier_rank = tier_rank_of(player_id)
        if tier_rank != 3:
            return (tier_rank, 0)
        birth_year = _birth_year(pool_by_id[player_id]["birth_date"])
        return (tier_rank, birth_year if birth_year is not None else 9999)

    division_players = conn.execute(
        "SELECT id, parent_id FROM players WHERE current_division_id = %s AND deleted_at IS NULL", (division_id,)
    ).fetchall()
    parent_of = {r[0]: r[1] for r in division_players if r[1] is not None}
    siblings_by_parent: dict[int, list[int]] = {}
    for pid, parent_id in parent_of.items():
        siblings_by_parent.setdefault(parent_id, []).append(pid)

    existing_team_by_player: dict[int, int] = {
        row[0]: row[1] for row in conn.execute(
            """SELECT re.player_id, re.team_id FROM roster_entries re
               JOIN teams t ON t.id = re.team_id
               WHERE t.division_id = %s AND re.player_id IS NOT NULL""",
            (division_id,),
        ).fetchall()
    }

    # One "unit" per pool player, merging in any pool siblings (an
    # already-rostered sibling pins the unit's team but isn't itself
    # re-assigned — it's already seated).
    units = []
    seen: set[int] = set()
    for player_id in pool_by_id:
        if player_id in seen:
            continue
        parent_id = parent_of.get(player_id)
        group = siblings_by_parent.get(parent_id, [player_id]) if parent_id else [player_id]
        pool_member_ids = [pid for pid in group if pid in pool_by_id]
        pinned_team_id = next(
            (existing_team_by_player[pid] for pid in group if pid in existing_team_by_player), None
        )
        seen.update(pool_member_ids)
        units.append({"player_ids": pool_member_ids, "pinned_team_id": pinned_team_id})

    # Coach's-kid: pin a unit to a team the coach already coaches.
    coach_team_by_id: dict[int, int] = {}
    for team_id, coaches in list_team_coaches_for_division(conn, division_id).items():
        for c in coaches:
            coach_team_by_id[c["id"]] = team_id
    for coach_id, team_id in coach_team_by_id.items():
        children = {c["id"] for c in list_coach_children(conn, coach_id)}
        if not children:
            continue
        for unit in units:
            if children.intersection(unit["player_ids"]):
                unit["pinned_team_id"] = unit["pinned_team_id"] or team_id

    team_size = {t["id"]: 0 for t in teams}
    team_skill = {t["id"]: 0 for t in teams}
    for player_id, team_id in existing_team_by_player.items():
        if team_id in team_size:
            team_size[team_id] += 1
            team_skill[team_id] += skill_of(player_id)

    assignments: dict[int, int] = {}
    pinned_units = [u for u in units if u["pinned_team_id"] is not None]
    open_units = sorted(
        (u for u in units if u["pinned_team_id"] is None),
        key=lambda u: min(draft_order_key(pid) for pid in u["player_ids"]),
    )

    for unit in pinned_units + open_units:
        team_id = unit["pinned_team_id"] or min(team_size, key=lambda t: (team_skill[t], team_size[t], t))
        for pid in unit["player_ids"]:
            assignments[pid] = team_id
        team_size[team_id] += len(unit["player_ids"])
        team_skill[team_id] += sum(skill_of(pid) for pid in unit["player_ids"])

    # Number each team's new players after whatever's already on its
    # roster, same "placeholder jersey number" convention as
    # submit_draft_pick's "TBD<n>".
    next_number = {t["id"]: 0 for t in teams}
    for team_id in existing_team_by_player.values():
        if team_id in next_number:
            next_number[team_id] += 1
    roster_entry_ids = []
    for player_id, team_id in assignments.items():
        next_number[team_id] += 1
        roster_entry_id = add_roster_entry(
            conn, team_id, f"AUTO{next_number[team_id]}", pool_by_id[player_id]["name"], player_id=player_id
        )
        roster_entry_ids.append(roster_entry_id)

    # Coach-follows-kid: a coach with no team yet in this division whose
    # child just got auto-drafted is assigned to that child's team.
    warnings: list[str] = []
    coach_assignments = []
    for coach_id in coach_ids_with_children(conn):
        if coach_id in coach_team_by_id:
            continue
        children_ids = {c["id"] for c in list_coach_children(conn, coach_id)}
        landed_team_ids = {assignments[pid] for pid in children_ids if pid in assignments}
        if not landed_team_ids:
            continue
        team_id = sorted(landed_team_ids)[0]
        try:
            assign_coach_to_team(conn, team_id, coach_id)
            coach_assignments.append({"team_id": team_id, "coach_id": coach_id})
        except ValueError as e:
            warnings.append(str(e))

    conn.execute(
        "INSERT INTO auto_draft_runs (division_id, roster_entry_ids, coach_assignments) VALUES (%s, %s, %s)",
        (division_id, json.dumps(roster_entry_ids), json.dumps(coach_assignments)),
    )
    conn.commit()
    return {"assigned": len(assignments), "teams": len(teams), "warnings": warnings}


# ---------------------------------------------------------------------------
# Player list import (CSV/Excel/ODS) — always into a specific, already-
# selected division; this never creates or infers a division from the
# file. A two-step flow: build_player_import_plan() figures out, per row,
# whether it's a new player or matches an existing one (flagging anything
# it can't decide confidently on its own), a caller (the UI) resolves
# whatever needs a human's judgment, then apply_player_import_plan() writes
# it. Column names are matched flexibly (case/whitespace-insensitive,
# several accepted spellings per field) since different leagues' exports
# don't all use the same headers — see the real one this was modeled on,
# scripts/import_summer_player_list.py, whose spreadsheet is the source of
# these exact header spellings.
# ---------------------------------------------------------------------------

PLAYER_IMPORT_FIELDS: dict[str, list[str]] = {
    "name": ["player name", "name", "full name", "player"],
    "first_name": ["first name", "first"],
    "last_name": ["last name", "last"],
    "birth_date": ["date of birth", "dob", "birth date", "birthdate"],
    "contact_first_name": ["parent firstname", "parent first name", "contact first name", "guardian first name"],
    "contact_last_name": ["parent lastname", "parent last name", "contact last name", "guardian last name"],
    "contact_phone": [
        "primary contact telephone", "phone", "phone number", "contact phone", "telephone", "parent phone",
    ],
    "contact_email": ["primary contact email", "email", "contact email", "parent email"],
    "team": ["team", "team name", "recent team"],
    "number": ["number", "jersey", "jersey #", "jersey number", "#"],
    "position": ["position"],
    "coach": ["coach", "coach name"],
}

# A header that doesn't exactly match any alias above falls back to a
# word-boundary match instead — real source files are inconsistent,
# ranging from a tidy "Position" column to a verbatim registration-form
# question ("What position does your child prefer?"), or "First Name" vs.
# "Player First Name" vs. "Participant First Name". Word-boundary (not a
# bare substring) matters here — a naive "team" in "teammate request", or
# "coach" in "coaching notes", would wrongly claim a header that isn't
# actually about a coach at all; \b keeps "coach" as a whole word without
# giving up on the fallback for it entirely.
#
# No "team" entry here, deliberately: a whole-word "team" still isn't safe
# enough to guess from, since an org can have "Team" as part of its own
# name — a real file this was tested against had "Is your child new to
# Team Pittsburgh?", which \bteam\b matches just as validly as an actual
# team-assignment column would. Team assignment relies on the exact
# aliases ("team"/"team name"/"recent team") only; anything else is left
# unmatched rather than risking that kind of false positive.
PLAYER_IMPORT_CONTAINS_FALLBACK: dict[str, list[str]] = {
    "birth_date": ["birth date", "birthdate", "date of birth", "dob"],
    "contact_phone": ["phone", "telephone", "cell", "cellphone", "cell phone"],
    "contact_email": ["email"],
    "number": ["jersey number", "jersey"],
    "position": ["position"],
    "coach": ["coach"],
}

# A header naming a first/last name field is ambiguous on its own --
# "First Name" could be the player's or a parent's ("Account First Name",
# "Guardian First Name"). Resolved by whether one of these appears in the
# header: present means it's about the contact, not the player.
_OTHER_PERSON_WORDS = ("parent", "guardian", "account", "contact", "emergency")


def _normalize_header(header: str) -> str:
    """Loose-match form of a column header for the substring fallback
    below: lowercased, internal whitespace collapsed, trailing "?"
    dropped — tolerates a verbatim survey-style question, not just a tidy
    label."""
    return re.sub(r"\s+", " ", header.strip().lower()).rstrip("?").strip()


def _contains_word(text: str, phrase: str) -> bool:
    """Whether `phrase` appears in `text` as a whole word (or phrase), not
    as part of a longer word — e.g. "team" matches "assigned team" but not
    "teammate request", and "coach" matches "co-coach" but not "coaching
    notes"."""
    return re.search(rf"\b{re.escape(phrase)}\b", text) is not None


def detect_player_import_columns(headers: list[str]) -> dict[str, str]:
    """Best-effort match of a file's actual column headers to
    PLAYER_IMPORT_FIELDS's canonical names. Returns {field:
    actual_header_as_given} for whatever it recognized — absence of "name"
    (or both "first_name" and "last_name") means the file can't be used,
    since there's no name to match or create a player by.

    Three passes, each only considering headers no earlier pass already
    claimed (so, e.g., a "Phone Number" header claimed by contact_phone
    can't also be claimed by "number" for jersey number):
      1. Exact match against PLAYER_IMPORT_FIELDS (case/whitespace-
         insensitive) — unambiguous, so tried first.
      2. first_name/last_name vs. contact_first_name/contact_last_name,
         told apart by an _OTHER_PERSON_WORDS qualifier (see above).
      3. A substring fallback for the remaining fields in
         PLAYER_IMPORT_CONTAINS_FALLBACK."""
    by_exact = {h.strip().lower(): h for h in headers if h and h.strip()}
    by_loose = {_normalize_header(h): h for h in headers if h and h.strip()}

    detected: dict[str, str] = {}
    used: set[str] = set()

    for field, aliases in PLAYER_IMPORT_FIELDS.items():
        for alias in aliases:
            header = by_exact.get(alias) or by_loose.get(alias)
            if header and header not in used:
                detected[field] = header
                used.add(header)
                break

    for normalized_header, header in by_loose.items():
        if header in used:
            continue
        is_other_person = any(_contains_word(normalized_header, word) for word in _OTHER_PERSON_WORDS)
        if _contains_word(normalized_header, "first name"):
            field = "contact_first_name" if is_other_person else "first_name"
        elif _contains_word(normalized_header, "last name"):
            field = "contact_last_name" if is_other_person else "last_name"
        else:
            continue
        if field not in detected:
            detected[field] = header
            used.add(header)

    for field, substrings in PLAYER_IMPORT_CONTAINS_FALLBACK.items():
        if field in detected:
            continue
        for normalized_header, header in by_loose.items():
            if header not in used and any(_contains_word(normalized_header, sub) for sub in substrings):
                detected[field] = header
                used.add(header)
                break

    return detected


def _is_missing_import_value(value) -> bool:
    """True for a genuinely empty cell — None, or a pandas/numpy NaN float
    (an empty cell read with dtype=str still comes back as float NaN, not
    a string, so this can't just check `value is None`). NaN is the only
    Python value that isn't equal to itself, which is what `value != value`
    actually tests here — safe for every other type (str, int, etc.)."""
    return value is None or value != value


def _import_cell(row: dict, columns: dict[str, str], field: str) -> str | None:
    header = columns.get(field)
    if header is None:
        return None
    value = row.get(header)
    if _is_missing_import_value(value):
        return None
    value = str(value).strip()
    return value or None


def _normalize_import_date(row: dict, columns: dict[str, str]) -> str | None:
    header = columns.get("birth_date")
    if header is None:
        return None
    value = row.get(header)
    if _is_missing_import_value(value) or (isinstance(value, str) and not value.strip()):
        return None
    if hasattr(value, "strftime"):  # a real date/datetime cell (openpyxl/pandas), not text
        return value.strftime("%Y-%m-%d")
    return normalize_date(str(value).strip())


def build_player_import_plan(conn: PGConnection, division_id: int, rows: list[dict], columns: dict[str, str]) -> list[dict]:
    """For each row (a dict keyed by the file's own headers), work out what
    importing it would do. Returns a list of plan rows, in file order, each
    with:
      "row_number": 1-based position in the file (for display/error messages)
      "name", "first_name", "last_name", "birth_date", "contact_first_name",
        "contact_last_name", "contact_phone", "contact_email": parsed fields
      "team_name", "number", "position", "coach_name": also parsed, used by
        apply_player_import_plan() for roster/coach assignment — None if
        the file has no such column, or this row left it blank
      "status": "invalid" (no name — skipped, never applied), "create" (no
        matching existing player), "update" (a single confident match),
        "ambiguous" (2+ same-name candidates that birth_date couldn't tell
        apart — see "candidates"), or "conflict" (a single same-name match,
        but its stored birth_date disagrees with the file's — see
        "conflict_detail")
      "matched_player_id": set for "update" (auto-resolved) only
      "candidates": the competing existing players, for "ambiguous"
      "conflict_detail": {"existing": ..., "incoming": ...} birth dates, for "conflict"
      "resolved_action": None for "ambiguous"/"conflict" until a caller
        (the UI) sets it to "use_existing" (with "resolved_player_id" set)
        or "create_new"; pre-set to "create"/"update" for the other two
        statuses, matching "status", so apply_player_import_plan() only
        ever looks at "resolved_action"/"resolved_player_id" — never
        "status" itself — and a row nobody has reviewed can't slip through.
    """
    existing_by_name: dict[str, list[dict]] = {}
    for p in list_players(conn):
        existing_by_name.setdefault(p["name"].strip().lower(), []).append(p)

    plan = []
    for i, row in enumerate(rows, start=1):
        name = _import_cell(row, columns, "name")
        first_name = _import_cell(row, columns, "first_name")
        last_name = _import_cell(row, columns, "last_name")
        if not name and (first_name or last_name):
            name = full_name(first_name, last_name)
        if name and not (first_name or last_name):
            first_name, last_name = split_full_name(name)

        entry = {
            "row_number": i,
            "name": name,
            "first_name": first_name,
            "last_name": last_name,
            "birth_date": _normalize_import_date(row, columns),
            "contact_first_name": _import_cell(row, columns, "contact_first_name"),
            "contact_last_name": _import_cell(row, columns, "contact_last_name"),
            "contact_phone": _import_cell(row, columns, "contact_phone"),
            "contact_email": _import_cell(row, columns, "contact_email"),
            "team_name": _import_cell(row, columns, "team"),
            "number": _import_cell(row, columns, "number"),
            "position": _import_cell(row, columns, "position"),
            "coach_name": _import_cell(row, columns, "coach"),
        }

        if not name:
            entry.update(status="invalid", matched_player_id=None, candidates=[], conflict_detail=None,
                         resolved_action="invalid", resolved_player_id=None)
            plan.append(entry)
            continue

        candidates = existing_by_name.get(name.strip().lower(), [])
        if len(candidates) == 0:
            entry.update(status="create", matched_player_id=None, candidates=[], conflict_detail=None,
                         resolved_action="create", resolved_player_id=None)
        elif len(candidates) == 1:
            match = candidates[0]
            if entry["birth_date"] and match["birth_date"] and entry["birth_date"] != match["birth_date"]:
                entry.update(
                    status="conflict", matched_player_id=match["id"], candidates=[match],
                    conflict_detail={"existing": match["birth_date"], "incoming": entry["birth_date"]},
                    resolved_action=None, resolved_player_id=None,
                )
            else:
                entry.update(status="update", matched_player_id=match["id"], candidates=[match], conflict_detail=None,
                             resolved_action="update", resolved_player_id=match["id"])
        else:
            # Multiple same-name candidates -- use birth_date (if the file
            # has one) to try to tell them apart before giving up and
            # asking a human. This is the "use all available data to
            # validate" step, not just a name lookup.
            by_dob = [c for c in candidates if entry["birth_date"] and c["birth_date"] == entry["birth_date"]]
            if len(by_dob) == 1:
                match = by_dob[0]
                entry.update(status="update", matched_player_id=match["id"], candidates=[match], conflict_detail=None,
                             resolved_action="update", resolved_player_id=match["id"])
            else:
                entry.update(status="ambiguous", matched_player_id=None, candidates=candidates, conflict_detail=None,
                             resolved_action=None, resolved_player_id=None)
        plan.append(entry)
    return plan


def apply_player_import_plan(conn: PGConnection, division_id: int, plan: list[dict]) -> dict:
    """Write a plan built by build_player_import_plan() (with every
    "ambiguous"/"conflict" row's "resolved_action" filled in by the UI) to
    the database. A row whose "resolved_action" is still None (an
    ambiguous/conflict row nobody resolved) is skipped, not guessed at.

    Team/coach are only ever touched when the row actually has one (per
    the row's own "team_name"/"coach_name") -- a name-only row just
    creates/updates the player profile and nothing else. A team is
    found-or-created in this division by name; a coach is found-or-created
    globally by name and assigned to that team. That assignment is skipped
    with a warning (not an error that aborts the rest of the import) if it
    would violate assign_coach_to_team's one-coach-per-team /
    one-team-per-coach-per-division rule -- either this team already has a
    *different* coach, or this coach already coaches a *different* team in
    this division."""
    created = updated = skipped = rostered = coached = 0
    warnings: list[str] = []

    for entry in plan:
        action = entry["resolved_action"]
        if action in (None, "invalid"):
            skipped += 1
            continue

        if action == "create":
            player_id = add_player(
                conn, entry["first_name"] or entry["name"], entry["last_name"],
                birth_date=entry["birth_date"], current_division_id=division_id,
                contact_first_name=entry["contact_first_name"], contact_last_name=entry["contact_last_name"],
                contact_phone=entry["contact_phone"], contact_email=entry["contact_email"],
            )
            created += 1
        elif action == "use_existing" or action == "update":
            player_id = entry.get("resolved_player_id") or entry["matched_player_id"]
            update_player(
                conn, player_id, first_name=entry["first_name"] or entry["name"], last_name=entry["last_name"],
                birth_date=entry["birth_date"], current_division_id=division_id,
                contact_first_name=entry["contact_first_name"], contact_last_name=entry["contact_last_name"],
                contact_phone=entry["contact_phone"], contact_email=entry["contact_email"],
            )
            updated += 1
        else:
            skipped += 1
            continue

        team_name = entry["team_name"]
        if not team_name:
            continue
        team_id = add_team(conn, division_id, team_name)

        if entry["number"]:
            existing_entry = next(
                (r for r in list_roster(conn, team_id) if r["number"] == entry["number"]), None
            )
            if existing_entry:
                conn.execute(
                    "UPDATE roster_entries SET name = %s, player_id = %s WHERE id = %s",
                    (entry["name"], player_id, existing_entry["id"]),
                )
                conn.commit()
            else:
                add_roster_entry(conn, team_id, entry["number"], entry["name"], player_id=player_id)
            rostered += 1

        if entry["position"]:
            row = conn.execute(
                "SELECT id FROM roster_entries WHERE team_id = %s AND player_id = %s", (team_id, player_id)
            ).fetchone()
            if row:
                set_position(conn, player_id, division_id, team_id, entry["position"])

        coach_name = entry["coach_name"]
        if coach_name:
            existing_coaches = {c["name"].strip().lower(): c["id"] for c in list_coaches(conn)}
            coach_id = existing_coaches.get(coach_name.strip().lower())
            if coach_id is None:
                coach_first, coach_last = split_full_name(coach_name)
                coach_id = add_coach(conn, coach_first, coach_last)
            try:
                assign_coach_to_team(conn, team_id, coach_id)
                coached += 1
            except ValueError as e:
                warnings.append(f"Row {entry['row_number']} ({entry['name']}): {e}")

    return {
        "created": created, "updated": updated, "skipped": skipped,
        "rostered": rostered, "coached": coached, "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Evaluations (a player's grade for a given division/team)
# ---------------------------------------------------------------------------

def add_evaluation(conn: PGConnection, player_id: int, division_id: int, team_id: int | None, grade: str) -> int:
    cur = conn.execute(
        "INSERT INTO evaluations (player_id, division_id, team_id, grade) VALUES (%s, %s, %s, %s) RETURNING id",
        (player_id, division_id, team_id, grade),
    )
    evaluation_id = cur.fetchone()[0]
    conn.commit()
    return evaluation_id


def list_evaluations(conn: PGConnection, player_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT e.id, d.year, d.season, d.age_group, t.name, e.grade, e.created_at
           FROM evaluations e
           JOIN divisions d ON d.id = e.division_id
           LEFT JOIN teams t ON t.id = e.team_id
           WHERE e.player_id = %s
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


def delete_evaluation(conn: PGConnection, evaluation_id: int):
    conn.execute("DELETE FROM evaluations WHERE id = %s", (evaluation_id,))
    conn.commit()


def get_season_grade(conn: PGConnection, player_id: int, division_id: int) -> str | None:
    """A player's "Season Grade" — the most recent evaluation on record for
    them in a given division (a division already IS one season's instance
    of an age group, so this needs no separate concept/table of its own)."""
    row = conn.execute(
        """SELECT grade FROM evaluations WHERE player_id = %s AND division_id = %s
           ORDER BY created_at DESC, id DESC LIMIT 1""",
        (player_id, division_id),
    ).fetchone()
    return row[0] if row else None


def set_season_grade(conn: PGConnection, player_id: int, division_id: int, team_id: int | None, grade: str):
    """Set a player's Season Grade for a division: updates that player's
    most recent evaluation there in place rather than growing a new history
    row every time, since a quick roster-grid edit isn't a new evaluation
    event the way the Evaluations popover's "Add evaluation" is. Clearing
    the grade deletes that row rather than leaving an empty one behind."""
    grade = grade.strip()
    existing_id = conn.execute(
        """SELECT id FROM evaluations WHERE player_id = %s AND division_id = %s
           ORDER BY created_at DESC, id DESC LIMIT 1""",
        (player_id, division_id),
    ).fetchone()
    if not grade:
        if existing_id:
            conn.execute("DELETE FROM evaluations WHERE id = %s", (existing_id[0],))
            conn.commit()
        return
    if existing_id:
        conn.execute(
            "UPDATE evaluations SET grade = %s, team_id = %s WHERE id = %s", (grade, team_id, existing_id[0])
        )
        conn.commit()
    else:
        add_evaluation(conn, player_id, division_id, team_id, grade)


def get_position(conn: PGConnection, player_id: int, division_id: int, team_id: int) -> str | None:
    """A player's position on one specific team for one division — unlike
    Season Grade, this is scoped to the team too (not just the division),
    since the same player could in principle be rostered on more than one
    team within a division."""
    row = conn.execute(
        "SELECT position FROM player_positions WHERE player_id = %s AND division_id = %s AND team_id = %s",
        (player_id, division_id, team_id),
    ).fetchone()
    return row[0] if row else None


def set_position(conn: PGConnection, player_id: int, division_id: int, team_id: int, position: str):
    """Set (or clear) a player's position for one team/division — a plain
    current value with no history, so this just overwrites the row."""
    position = position.strip()
    if not position:
        conn.execute(
            "DELETE FROM player_positions WHERE player_id = %s AND division_id = %s AND team_id = %s",
            (player_id, division_id, team_id),
        )
    else:
        conn.execute(
            """INSERT INTO player_positions (player_id, division_id, team_id, position) VALUES (%s, %s, %s, %s)
               ON CONFLICT(player_id, division_id, team_id) DO UPDATE SET position = excluded.position""",
            (player_id, division_id, team_id, position),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Player stats
# ---------------------------------------------------------------------------

def get_player_stats(conn: PGConnection, division_id: int) -> list[dict]:
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
               WHERE gm.division_id = %s
               UNION ALL
               SELECT gm.away_team AS team, gl.scorer_number
               FROM goals gl JOIN games gm ON gm.id = gl.game_id AND gl.side = 'away'
               WHERE gm.division_id = %s
           ) sub
           WHERE scorer_number IS NOT NULL AND scorer_number <> ''
           GROUP BY team, scorer_number"""
    )
    assists = {}
    for team, number, count in conn.execute(
        """SELECT team, player_number, COUNT(*) FROM (
               SELECT gm.home_team AS team, gl.assist1_number AS player_number
               FROM goals gl JOIN games gm ON gm.id = gl.game_id AND gl.side = 'home'
               WHERE gm.division_id = %s AND gl.assist1_number IS NOT NULL AND gl.assist1_number <> ''
               UNION ALL
               SELECT gm.away_team, gl.assist1_number
               FROM goals gl JOIN games gm ON gm.id = gl.game_id AND gl.side = 'away'
               WHERE gm.division_id = %s AND gl.assist1_number IS NOT NULL AND gl.assist1_number <> ''
               UNION ALL
               SELECT gm.home_team, gl.assist2_number
               FROM goals gl JOIN games gm ON gm.id = gl.game_id AND gl.side = 'home'
               WHERE gm.division_id = %s AND gl.assist2_number IS NOT NULL AND gl.assist2_number <> ''
               UNION ALL
               SELECT gm.away_team, gl.assist2_number
               FROM goals gl JOIN games gm ON gm.id = gl.game_id AND gl.side = 'away'
               WHERE gm.division_id = %s AND gl.assist2_number IS NOT NULL AND gl.assist2_number <> ''
           ) sub
           GROUP BY team, player_number""",
        (division_id, division_id, division_id, division_id),
    ):
        assists[(team, number)] = count
    penalties = _tally(
        """SELECT team, player_number, COUNT(*) FROM (
               SELECT gm.home_team AS team, pen.player_number
               FROM penalties pen JOIN games gm ON gm.id = pen.game_id AND pen.side = 'home'
               WHERE gm.division_id = %s
               UNION ALL
               SELECT gm.away_team, pen.player_number
               FROM penalties pen JOIN games gm ON gm.id = pen.game_id AND pen.side = 'away'
               WHERE gm.division_id = %s
           ) sub
           WHERE player_number IS NOT NULL AND player_number <> ''
           GROUP BY team, player_number"""
    )
    # Shootout attempts are tracked separately — they never count toward "goals".
    shootout: dict[tuple[str, str], tuple[int, int]] = {}
    for team, number, attempts, made in conn.execute(
        """SELECT team, player_number, COUNT(*), COALESCE(SUM(scored), 0) FROM (
               SELECT gm.home_team AS team, so.player_number, so.scored
               FROM shootout_attempts so JOIN games gm ON gm.id = so.game_id AND so.side = 'home'
               WHERE gm.division_id = %s
               UNION ALL
               SELECT gm.away_team, so.player_number, so.scored
               FROM shootout_attempts so JOIN games gm ON gm.id = so.game_id AND so.side = 'away'
               WHERE gm.division_id = %s
           ) sub
           WHERE player_number IS NOT NULL AND player_number <> ''
           GROUP BY team, player_number""",
        (division_id, division_id),
    ):
        shootout[(team, number)] = (attempts, made)

    # A soft-deleted player's link doesn't count as linked here (see
    # list_roster) — the row reverts to unlinked until restored/re-linked.
    roster = {
        (team, number): (name, player_id)
        for team, number, name, player_id in conn.execute(
            """SELECT t.name, re.number,
                      COALESCE(NULLIF(TRIM(CONCAT_WS(' ', p.first_name, p.last_name)), ''), re.name), p.id
               FROM roster_entries re
               JOIN teams t ON t.id = re.team_id
               LEFT JOIN players p ON p.id = re.player_id AND p.deleted_at IS NULL
               WHERE t.division_id = %s""",
            (division_id,),
        )
    }
    team_ids = {
        name: team_id
        for team_id, name in conn.execute("SELECT id, name FROM teams WHERE division_id = %s", (division_id,))
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

def get_standings(conn: PGConnection, division_id: int) -> list[dict]:
    """Per-team season record for one division, sorted by points then the
    tiebreak chain: head-to-head record, goal differential, regulation
    wins, OT wins, goals against (fewer first), goals for (more first).

    Points: 3 for a regulation win, 2 for an OT/shootout win, 1 for an OT/
    shootout loss, 1 each for an unresolved regulation tie, 0 for a
    regulation loss."""
    games = conn.execute(
        """SELECT home_team, away_team, home_final_score, away_final_score,
                  winner, ot_winner, ot_loser
           FROM games WHERE winner IS NOT NULL AND division_id = %s""",
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

def standings_table(conn: PGConnection, division_id: int) -> list[dict]:
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


def player_stats_table(conn: PGConnection, division_id: int) -> list[dict]:
    stats = sorted(get_player_stats(conn, division_id), key=lambda s: (-s["points"], -s["goals"]))
    return [
        {
            "Team": display_text(s["team"]), "#": s["number"], "Name": display_text(s["name"]),
            "G": s["goals"], "A": s["assists"], "PTS": s["points"], "PIM": s["penalties"],
            "SO Made": s["shootout_goals"], "SO Missed": s["shootout_misses"],
        }
        for s in stats
    ]


def roster_table(conn: PGConnection, division_id: int) -> list[dict]:
    sort_key = _NUMERIC_SORT_KEY.format(col="re.number")
    rows = conn.execute(
        f"""SELECT t.name, re.number, re.name FROM roster_entries re
           JOIN teams t ON t.id = re.team_id
           WHERE t.division_id = %s
           ORDER BY t.name, {sort_key}, re.number""",
        (division_id,),
    ).fetchall()
    return [
        {"Team": display_text(team), "Number": number, "Name": display_text(name)}
        for team, number, name in rows
    ]


def games_table(conn: PGConnection, division_id: int) -> list[dict]:
    labels = {
        "id": "ID", "game_date": "Date", "division": "Division",
        "home_team": "Home", "home_final_score": "Home Score",
        "away_team": "Away", "away_final_score": "Away Score",
        "winner": "Winner", "ot_winner": "OT Winner", "ot_loser": "OT Loser",
        "source_file": "Source File",
    }
    return [{labels[k]: v for k, v in row.items()} for row in list_games(conn, division_id)]


def export_workbook(conn: PGConnection, division_id: int) -> bytes:
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
