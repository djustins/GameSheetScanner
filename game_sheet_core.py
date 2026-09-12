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
from datetime import datetime, timedelta
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
_DATE_INPUT_FORMATS = [
    "%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d", "%m-%d-%y", "%m-%d-%Y", "%m.%d.%y", "%m.%d.%Y",
]


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


def init_db(dsn: str) -> _ConnWrapper:
    """Connect to the Postgres database identified by dsn (a full connection
    string / DSN, e.g. "postgresql://user:pass@host:port/dbname?sslmode=require"),
    ensure the schema exists, and purge any long-expired soft-deleted
    divisions (see purge_expired_divisions)."""
    conn = _ConnWrapper(psycopg2.connect(dsn))
    _apply_schema(conn)
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
    "process": "Process New Scoresheet",
    "edit": "Games",
    "schedule": "Schedule",
    "standings": "Standings",
    "stats": "Player Stats",
    "rosters": "Team Rosters",
    "players": "Players",
    "divisions": "Divisions",
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
        f"SELECT id, email, display_name, is_admin, role_id, deleted_at FROM users {where} ORDER BY email"
    ).fetchall()
    users = [
        {
            "id": r[0], "email": r[1], "display_name": r[2], "is_admin": bool(r[3]),
            "role_id": r[4], "deleted_at": r[5],
        }
        for r in rows
    ]
    for u in users:
        u["pages"] = list_role_pages(conn, u["role_id"])
        u["read_only"], u["hide_contact_details"] = _get_role_flags(conn, u["role_id"])
    return users


def get_user_by_email(conn: PGConnection, email: str) -> dict | None:
    row = conn.execute(
        "SELECT id, email, password_hash, display_name, is_admin, role_id, deleted_at "
        "FROM users WHERE email = %s",
        (email.strip().lower(),),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "email": row[1], "password_hash": row[2], "display_name": row[3],
        "is_admin": bool(row[4]), "role_id": row[5], "deleted_at": row[6],
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


def set_user_password(conn: PGConnection, user_id: int, new_password: str):
    conn.execute("UPDATE users SET password_hash = %s WHERE id = %s", (_hash_password(new_password), user_id))
    conn.commit()


def soft_delete_user(conn: PGConnection, user_id: int):
    conn.execute(
        "UPDATE users SET deleted_at = %s WHERE id = %s",
        (datetime.utcnow().isoformat(timespec="seconds"), user_id),
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
    though the game is plainly right there in the Games tab."""
    stored = conn.execute(
        "SELECT game_date, home_team, away_team FROM games WHERE division_id = %s", (division_id,)
    ).fetchall()
    played = {(date, frozenset((home, away))) for date, home, away in stored}

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
    result = []
    for values in rows:
        r = dict(zip(cols, values))
        home_key = normalize_team_name(conn, division_id, r["home_team"], existing_teams=existing_teams)
        away_key = normalize_team_name(conn, division_id, r["away_team"], existing_teams=existing_teams)
        r["accounted_for"] = (r["game_date"], frozenset((home_key, away_key))) in played
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
        (datetime.utcnow().isoformat(timespec="seconds"), division_id),
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
        days_left = max(0, 30 - (datetime.utcnow() - deleted_at).days)
        result.append({
            "id": r[0], "year": r[1], "season": display_text(r[2]), "age_group": r[3],
            "category": r[4], "deleted_at": r[5], "days_left": days_left,
        })
    return result


def purge_expired_divisions(conn: PGConnection, days: int = 30):
    """Permanently delete divisions that have been in the recycle bin more
    than `days` days. Called automatically on every init_db()."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM divisions WHERE deleted_at IS NOT NULL AND deleted_at <= %s", (cutoff,))
    conn.commit()


# ---------------------------------------------------------------------------
# Team rosters
# ---------------------------------------------------------------------------

def list_teams(conn: PGConnection, division_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name FROM teams WHERE division_id = %s ORDER BY name", (division_id,)
    ).fetchall()
    return [{"id": r[0], "name": display_text(r[1])} for r in rows]


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
        f"""SELECT re.id, re.number, COALESCE(p.name, re.name), p.id
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


# ---------------------------------------------------------------------------
# Global players (identity persists across every division/season)
# ---------------------------------------------------------------------------

def list_players(conn: PGConnection, include_deleted: bool = False) -> list[dict]:
    where = "" if include_deleted else "WHERE deleted_at IS NULL"
    rows = conn.execute(
        f"""SELECT id, name, birth_date, current_division_id, contact_first_name,
                   contact_last_name, contact_phone, contact_email, deleted_at
            FROM players {where} ORDER BY name"""
    ).fetchall()
    cols = ["id", "name", "birth_date", "current_division_id", "contact_first_name",
            "contact_last_name", "contact_phone", "contact_email", "deleted_at"]
    return [dict(zip(cols, r)) for r in rows]


def get_player(conn: PGConnection, player_id: int) -> dict | None:
    players = {p["id"]: p for p in list_players(conn, include_deleted=True)}
    return players.get(player_id)


def add_player(
    conn: PGConnection, name: str, birth_date: str | None = None,
    current_division_id: int | None = None, contact_first_name: str | None = None,
    contact_last_name: str | None = None, contact_phone: str | None = None,
    contact_email: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO players
           (name, birth_date, current_division_id, contact_first_name,
            contact_last_name, contact_phone, contact_email)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        (name.strip(), birth_date, current_division_id, contact_first_name,
         contact_last_name, contact_phone, contact_email),
    )
    player_id = cur.fetchone()[0]
    conn.commit()
    return player_id


def update_player(conn: PGConnection, player_id: int, **fields):
    """Update any subset of a player's profile fields, e.g.
    update_player(conn, 5, name="Alex Smith", contact_phone="412-555-0100")."""
    allowed = {
        "name", "birth_date", "current_division_id", "contact_first_name",
        "contact_last_name", "contact_phone", "contact_email",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    conn.execute(
        f"UPDATE players SET {set_clause} WHERE id = %s", (*updates.values(), player_id)
    )
    conn.commit()


def soft_delete_player(conn: PGConnection, player_id: int):
    conn.execute(
        "UPDATE players SET deleted_at = %s WHERE id = %s",
        (datetime.utcnow().isoformat(timespec="seconds"), player_id),
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
# Coaches (global; assigned to a team, which anchors them to one division)
# ---------------------------------------------------------------------------

def list_coaches(conn: PGConnection, include_deleted: bool = False) -> list[dict]:
    where = "" if include_deleted else "WHERE deleted_at IS NULL"
    rows = conn.execute(f"SELECT id, name, deleted_at FROM coaches {where} ORDER BY name").fetchall()
    return [{"id": r[0], "name": r[1], "deleted_at": r[2]} for r in rows]


def add_coach(conn: PGConnection, name: str) -> int:
    cur = conn.execute("INSERT INTO coaches (name) VALUES (%s) RETURNING id", (name.strip(),))
    coach_id = cur.fetchone()[0]
    conn.commit()
    return coach_id


def update_coach(conn: PGConnection, coach_id: int, name: str):
    conn.execute("UPDATE coaches SET name = %s WHERE id = %s", (name.strip(), coach_id))
    conn.commit()


def soft_delete_coach(conn: PGConnection, coach_id: int):
    conn.execute(
        "UPDATE coaches SET deleted_at = %s WHERE id = %s",
        (datetime.utcnow().isoformat(timespec="seconds"), coach_id),
    )
    conn.commit()


def restore_coach(conn: PGConnection, coach_id: int):
    conn.execute("UPDATE coaches SET deleted_at = NULL WHERE id = %s", (coach_id,))
    conn.commit()


def assign_coach_to_team(conn: PGConnection, team_id: int, coach_id: int):
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
        """SELECT c.id, c.name FROM team_coaches tc
           JOIN coaches c ON c.id = tc.coach_id
           WHERE tc.team_id = %s AND c.deleted_at IS NULL ORDER BY c.name""",
        (team_id,),
    ).fetchall()
    return [{"id": r[0], "name": r[1]} for r in rows]


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
            """SELECT t.name, re.number, COALESCE(p.name, re.name), p.id
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
