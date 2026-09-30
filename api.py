"""
api.py — HTTP API for GameSheetScanner, alongside the Streamlit app (app.py).

Both talk to the same Postgres database via game_sheet_core.py, which was
deliberately kept framework-agnostic for exactly this kind of reuse — every
endpoint here is a thin wrapper over an existing, already-tested core
function rather than new business logic.

Run locally:
    export DATABASE_URL=postgresql://user:password@host:port/dbname?sslmode=require
    uvicorn api:app --reload

This is a separate deployable service from the Streamlit app — Streamlit
Community Cloud only serves the Streamlit process itself, so this needs
its own host (Render/Fly.io/Railway/a VM/etc.) pointed at the same
DATABASE_URL. See README's "Running the API" section.

Auth: HTTP Basic against the same `users` table the Streamlit app's login
uses (see game_sheet_core.verify_login) — any existing user account works,
no separate API credential to manage. Every endpoint requires a valid
login; writes additionally require the same "not read-only" check the
Streamlit app applies (an admin, or a non-read-only role) — see
require_writer below. A role's hide_contact_details is enforced too:
phone/email fields are blanked in every response for such a role, and
ignored on writes (see ContactRedactingRoute). Fine-grained per-page
visibility (a role's `pages` list) isn't: any authenticated user can read
any resource through this API. Tighten that if this API gets exposed
beyond trusted, already-vetted league admins/coaches.
"""

import json
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials, HTTPBearer
from pydantic import BaseModel

import game_sheet_core as core

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Set the DATABASE_URL environment variable (same one app.py uses).")

# Contact details a "hide contact details" role must never receive (see
# ContactRedactingRoute). "phone"/"email" are coaches' and parents' own
# fields; contact_* are a player's registration contact.
_CONTACT_KEYS = frozenset({"contact_phone", "contact_email", "phone", "email"})
# The caller's own account (their login email, their tokens) and the
# admin-only user/role screens aren't other people's contact details.
_CONTACT_REDACTION_EXEMPT_PREFIXES = ("/me", "/login", "/tokens", "/users", "/roles")


def _hides_contacts(user: dict | None) -> bool:
    return bool(user) and not user["is_admin"] and bool(user["hide_contact_details"])


def _redact_contacts(value):
    if isinstance(value, list):
        return [_redact_contacts(v) for v in value]
    if isinstance(value, dict):
        return {k: (None if k in _CONTACT_KEYS else _redact_contacts(v)) for k, v in value.items()}
    return value


class ContactRedactingRoute(APIRoute):
    """Enforces a role's hide_contact_details server-side, the way the
    Streamlit app hides those fields from its UI -- centrally here, so every
    endpoint (including future ones) is covered rather than each having to
    remember. Relies on get_current_user having stored the caller on
    request.state; unauthenticated or non-JSON responses pass through."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def route_handler(request: Request):
            response = await handler(request)
            if (
                _hides_contacts(getattr(request.state, "user", None))
                and (response.media_type or "").startswith("application/json")
                and getattr(response, "body", None)
                and not request.url.path.startswith(_CONTACT_REDACTION_EXEMPT_PREFIXES)
            ):
                headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
                return JSONResponse(
                    _redact_contacts(json.loads(response.body)),
                    status_code=response.status_code,
                    headers=headers,
                )
            return response

        return route_handler


app = FastAPI(
    title="GameSheetScanner API",
    description="Programmatic access to the same league data the Streamlit app manages.",
    version="1.0.0",
)
# Must be set before any route is declared below -- each @app.get/... uses
# the router's route_class at declaration time.
app.router.route_class = ContactRedactingRoute

# A browser-based frontend (e.g. the React app) runs on a different origin than
# this API, so it needs explicit CORS allowance — FastAPI has none by default.
# Wide open: auth here is a Bearer token in a header, not a cookie, so there's
# no session to leak cross-origin, and allow_credentials must be False anyway
# — browsers reject the combination of a wildcard origin with credentials=True.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# auto_error=False on both: a request supplies at most one of these (Basic
# credentials or a Bearer token), never both, and each scheme's dependency
# would otherwise 401 on its own before get_current_user gets a chance to
# fall back to the other one.
basic_security = HTTPBasic(auto_error=False)
bearer_security = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Auth + per-request DB connection
# ---------------------------------------------------------------------------

def get_conn():
    """One plain connection per request (see core.connect) — psycopg2
    connections aren't safe to share across concurrently-handled requests,
    and init_db's one-time schema/migration work is assumed already done
    (by app.py's own startup, or a one-off `python -c "import
    game_sheet_core as core; core.init_db(...)"` against a brand new
    database before this service's first request)."""
    conn = core.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()


def get_current_user(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(basic_security),
    bearer: HTTPAuthorizationCredentials | None = Depends(bearer_security),
    conn=Depends(get_conn),
) -> dict:
    """Either an email/password (HTTP Basic, same as the Streamlit login)
    or a long-lived API token (HTTP Bearer, see /tokens) works — pick
    whichever the request actually sent. A token carries exactly the same
    access as the user it belongs to (see verify_api_token)."""
    if bearer is not None:
        user = core.verify_api_token(conn, bearer.credentials)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or revoked API token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        request.state.user = user
        return user
    if credentials is not None:
        user = core.verify_login(conn, credentials.username, credentials.password)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password.",
                headers={"WWW-Authenticate": "Basic"},
            )
        request.state.user = user
        return user
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Provide either HTTP Basic credentials or a Bearer API token.",
        headers={"WWW-Authenticate": "Basic"},
    )


def require_writer(user: dict = Depends(get_current_user)) -> dict:
    """Same rule the Streamlit app applies: an admin, or a non-read-only
    role, can write; everyone else (including an unauthenticated request,
    caught earlier by get_current_user) is read-only."""
    is_read_only = (not user["is_admin"]) and user["read_only"]
    if is_read_only:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Your role has read-only access."
        )
    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return user


def _writable_fields(user: dict, body: dict) -> dict:
    """A PATCH body's set fields, minus contact details a hide-contact role
    can't see and so mustn't overwrite (they'd only ever be sending back a
    redacted blank)."""
    hide = _hides_contacts(user)
    return {k: v for k, v in body.items() if v is not None and not (hide and k in _CONTACT_KEYS)}


def not_found(detail: str = "Not found"):
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


@app.get("/", tags=["meta"])
def root():
    return {"name": "GameSheetScanner API", "docs": "/docs"}


def _public_user(user: dict) -> dict:
    return {
        "email": user["email"], "display_name": user["display_name"], "is_admin": user["is_admin"],
        "read_only": user["read_only"], "coach_id": user["coach_id"], "pages": user["pages"],
        "hide_contact_details": user["hide_contact_details"],
    }


@app.get("/me", tags=["meta"])
def whoami(user: dict = Depends(get_current_user)) -> dict:
    """Confirms your credentials work and shows what they grant — the
    quickest way to sanity-check an API client's auth setup."""
    return _public_user(user)


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/login", tags=["meta"])
def login(body: LoginRequest, conn=Depends(get_conn)) -> dict:
    """A one-step email/password -> token exchange, for a browser-based
    client (e.g. a React app) that shouldn't hold onto Basic auth
    credentials on every request. Equivalent to logging in with Basic once
    and immediately calling POST /tokens -- creates a new token each call,
    named "Login" (revoke old ones from GET /tokens if they pile up)."""
    user = core.verify_login(conn, body.email, body.password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")
    _, raw_token = core.create_api_token(conn, user["id"], "Login")
    return {"token": raw_token, "user": _public_user(user)}


# ---------------------------------------------------------------------------
# API tokens — a long-lived alternative to sending your actual email/
# password on every request (e.g. for a script or integration). Only ever
# scoped to yourself: there's no endpoint here to manage another user's
# tokens (an admin does that from the Streamlit app's User Management tab,
# same as resetting someone's password).
# ---------------------------------------------------------------------------

class ApiTokenCreate(BaseModel):
    name: str


@app.post("/tokens", status_code=status.HTTP_201_CREATED, tags=["tokens"])
def api_create_token(
    body: ApiTokenCreate, conn=Depends(get_conn), user: dict = Depends(get_current_user)
) -> dict:
    """Creates a new API token for you. The `token` value in this response
    is the only time it's ever shown — save it now (e.g. in a secrets
    manager or .env), since it can't be retrieved again afterward, only
    revoked and replaced with a new one."""
    token_id, raw_token = core.create_api_token(conn, user["id"], body.name)
    return {"id": token_id, "name": body.name, "token": raw_token}


@app.get("/tokens", tags=["tokens"])
def api_list_tokens(conn=Depends(get_conn), user: dict = Depends(get_current_user)) -> list[dict]:
    """Your own tokens — never includes the raw value, only id/name/
    created_at/last_used_at/revoked_at, for deciding what to revoke."""
    return core.list_api_tokens(conn, user["id"])


@app.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["tokens"])
def api_revoke_token(token_id: int, conn=Depends(get_conn), user: dict = Depends(get_current_user)):
    core.revoke_api_token(conn, token_id, user["id"])


# ---------------------------------------------------------------------------
# Divisions
# ---------------------------------------------------------------------------

class DivisionCreate(BaseModel):
    year: int
    season: str
    age_group: str
    category: str | None = None


@app.get("/divisions", tags=["divisions"])
def api_list_divisions(conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_divisions(conn)


@app.post("/divisions", status_code=status.HTTP_201_CREATED, tags=["divisions"])
def api_create_division(body: DivisionCreate, conn=Depends(get_conn), user=Depends(require_writer)) -> dict:
    division_id = core.add_division(conn, body.year, body.season, body.age_group, body.category)
    return next(d for d in core.list_divisions(conn) if d["id"] == division_id)


@app.delete("/divisions/{division_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["divisions"])
def api_delete_division(division_id: int, conn=Depends(get_conn), user=Depends(require_admin)):
    core.soft_delete_division(conn, division_id)


@app.post("/divisions/{division_id}/restore", status_code=status.HTTP_204_NO_CONTENT, tags=["divisions"])
def api_restore_division(division_id: int, conn=Depends(get_conn), user=Depends(require_admin)):
    core.restore_division(conn, division_id)


@app.get("/divisions/deleted", tags=["divisions"])
def api_list_deleted_divisions(conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_deleted_divisions(conn)


@app.get("/divisions/{division_id}/previous", tags=["divisions"])
def api_previous_division(division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> dict | None:
    """The most recent earlier division with the same age group -- used by
    the coach-carryover panel to offer "assign coaches from last season"."""
    return core.find_previous_division(conn, division_id)


@app.get("/divisions/{division_id}/teams", tags=["divisions"])
def api_division_teams(
    division_id: int, include_deleted: bool = False, conn=Depends(get_conn), user=Depends(get_current_user)
) -> list[dict]:
    return core.list_teams(conn, division_id, include_deleted=include_deleted)


@app.get("/divisions/{division_id}/players", tags=["divisions"])
def api_division_players(division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    """Excludes "Sub"/"SUB" placeholder players (one per team, for
    attributing stats to an unidentified substitute rather than a real
    person) -- a purely statistical bucket, not someone worth listing at
    the division level. They still appear in GET /teams/{id}/roster."""
    players = [p for p in core.list_players_in_division(conn, division_id) if p["name"].strip().lower() != "sub"]
    grades = core.get_latest_grades_with_source(conn, division_id, [p["id"] for p in players])
    notes = core.player_experience_notes(conn, division_id, [p["id"] for p in players])
    for p in players:
        grade_info = grades.get(p["id"])
        p["grade"] = grade_info["grade"] if grade_info else None
        p["grade_is_carryover"] = bool(grade_info) and not grade_info["same_age_group"]
        p["note"] = notes.get(p["id"])
    return players


@app.delete("/divisions/{division_id}/players", tags=["divisions"])
def api_remove_all_players_from_division(
    division_id: int, conn=Depends(get_conn), user=Depends(require_writer)
) -> dict:
    """Unlinks every player from this division (roster spots, evaluations,
    positions, move notes, and current_division_id) without deleting the
    player records themselves or touching any other division they're in
    -- e.g. to undo a bad import. See core.remove_all_players_from_division."""
    removed = core.remove_all_players_from_division(conn, division_id)
    return {"removed": removed}


@app.post("/divisions/{division_id}/players/import-plan", tags=["divisions"])
async def api_player_import_plan(
    division_id: int, file: UploadFile = File(...), conn=Depends(get_conn), user=Depends(require_writer)
) -> dict:
    """Reads an uploaded player list (CSV/XLSX/XLS/ODS), matches each row
    against existing player profiles by name (and birth date, to tell
    same-named players apart or flag a possible mismatch), and returns a
    plan for review -- see core.build_player_import_plan. Any row whose
    status is "ambiguous"/"conflict" needs a "resolved_action" filled in
    (client-side) before POST .../import-apply will act on it."""
    raw = await file.read()
    try:
        rows = core.read_table_bytes(file.filename, raw)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    columns = core.detect_player_import_columns(list(rows[0].keys()) if rows else [])
    has_name = "name" in columns or ("first_name" in columns and "last_name" in columns)
    if not has_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Couldn\'t find a name column (e.g. "Player Name", "Name", or separate "First Name"/'
                   '"Last Name" columns) — can\'t match or create players without one.',
        )
    plan = core.build_player_import_plan(conn, division_id, rows, columns)
    return {"columns": columns, "plan": plan}


class PlayerImportApply(BaseModel):
    plan: list[dict]


@app.post("/divisions/{division_id}/players/import-apply", tags=["divisions"])
def api_player_import_apply(
    division_id: int, body: PlayerImportApply, conn=Depends(get_conn), user=Depends(require_writer)
) -> dict:
    """Writes a plan from POST .../import-plan (with every ambiguous/
    conflict row's resolved_action filled in) to the database -- see
    core.apply_player_import_plan."""
    return core.apply_player_import_plan(conn, division_id, body.plan)


@app.get("/divisions/{division_id}/schedule", tags=["divisions"])
def api_division_schedule(division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_schedule(conn, division_id)


class ScheduleImportRequest(BaseModel):
    rows: list[dict]


@app.post("/divisions/{division_id}/schedule", tags=["divisions"])
def api_import_schedule(
    division_id: int, body: ScheduleImportRequest, conn=Depends(get_conn), user=Depends(require_writer)
) -> dict:
    """Upserts a parsed schedule (e.g. from a CSV the frontend already
    parsed client-side) by (division_id, date, home, away) -- re-importing
    a corrected file updates round/time/location in place instead of
    duplicating rows. See core.import_schedule for the row shape."""
    saved = core.import_schedule(conn, division_id, body.rows)
    return {"saved": saved}


@app.delete("/divisions/{division_id}/schedule", status_code=status.HTTP_204_NO_CONTENT, tags=["divisions"])
def api_clear_schedule(division_id: int, conn=Depends(get_conn), user=Depends(require_writer)):
    core.clear_schedule(conn, division_id)


@app.get("/divisions/{division_id}/export.xlsx", tags=["divisions"])
def api_export_workbook(division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)):
    """One .xlsx with a sheet each for Games/Standings/Player Stats/Rosters
    -- the same workbook the Streamlit app's Export button produces."""
    workbook = core.export_workbook(conn, division_id)
    return StreamingResponse(
        iter([workbook]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=division_{division_id}.xlsx"},
    )


@app.get("/divisions/{division_id}/games", tags=["divisions"])
def api_division_games(division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_games(conn, division_id)


@app.get("/divisions/{division_id}/standings", tags=["divisions"])
def api_division_standings(division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    # core leaves team names in their stored (lowercased) form here -- the
    # Streamlit app title-cases them itself at display time.
    standings = core.get_standings(conn, division_id)
    for s in standings:
        s["team"] = core.display_text(s["team"])
    return standings


@app.get("/divisions/{division_id}/stats", tags=["divisions"])
def api_division_stats(division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    stats = core.get_player_stats(conn, division_id)
    for s in stats:
        s["team"] = core.display_text(s["team"])
        s["name"] = core.display_text(s["name"])
    return sorted(stats, key=lambda s: (-s["points"], -s["goals"]))


@app.get("/divisions/{division_id}/season-grades", tags=["divisions"])
def api_division_season_grades(
    division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)
) -> dict[int, str]:
    """Every player's most recent evaluation grade for this division,
    player_id -> grade, in one query -- for a Teams overview's average/
    breakdown or a grade-distribution summary without a per-player round
    trip. Keys are ints but come back as JSON object string keys, same as
    any dict[int, ...] FastAPI response."""
    return core.get_season_grades_for_division(conn, division_id)


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

class TeamCreate(BaseModel):
    division_id: int
    name: str


class TeamUpdate(BaseModel):
    name: str | None = None
    color: str | None = None


@app.post("/teams", status_code=status.HTTP_201_CREATED, tags=["teams"])
def api_create_team(body: TeamCreate, conn=Depends(get_conn), user=Depends(require_writer)) -> dict:
    team_id = core.add_team(conn, body.division_id, body.name)
    return next(t for t in core.list_teams(conn, body.division_id) if t["id"] == team_id)


@app.patch("/teams/{team_id}", tags=["teams"])
def api_update_team(
    team_id: int, body: TeamUpdate, conn=Depends(get_conn), user=Depends(require_writer)
) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if fields:
        try:
            core.update_team(conn, team_id, **fields)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    row = conn.execute("SELECT division_id FROM teams WHERE id = %s", (team_id,)).fetchone()
    if row is None:
        not_found("Team not found.")
    return next(t for t in core.list_teams(conn, row[0]) if t["id"] == team_id)


@app.delete("/teams/{team_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["teams"])
def api_delete_team(team_id: int, conn=Depends(get_conn), user=Depends(require_admin)):
    core.soft_delete_team(conn, team_id)


@app.post("/teams/{team_id}/restore", status_code=status.HTTP_204_NO_CONTENT, tags=["teams"])
def api_restore_team(team_id: int, conn=Depends(get_conn), user=Depends(require_admin)):
    core.restore_team(conn, team_id)


@app.get("/teams/deleted", tags=["teams"])
def api_list_deleted_teams(conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_deleted_teams(conn)


@app.get("/teams/{team_id}/roster", tags=["teams"])
def api_team_roster(team_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_roster(conn, team_id)


class RosterEntryCreate(BaseModel):
    number: str
    name: str
    player_id: int | None = None


@app.post("/teams/{team_id}/roster", status_code=status.HTTP_201_CREATED, tags=["teams"])
def api_add_roster_entry(
    team_id: int, body: RosterEntryCreate, conn=Depends(get_conn), user=Depends(require_writer)
) -> dict:
    entry_id = core.add_roster_entry(conn, team_id, body.number, body.name, player_id=body.player_id)
    return next(r for r in core.list_roster(conn, team_id) if r["id"] == entry_id)


class RosterEntryUpdate(BaseModel):
    number: str | None = None
    name: str | None = None
    player_id: int | None = None


@app.patch("/teams/{team_id}/roster/{entry_id}", tags=["teams"])
def api_update_roster_entry(
    team_id: int, entry_id: int, body: RosterEntryUpdate, conn=Depends(get_conn), user=Depends(require_writer)
) -> dict:
    """Renumber, rename, and/or (re)link a roster entry to a player profile
    directly — e.g. swap a draft's placeholder jersey number ("TBD3"/
    "AUTO3") for the real one, or identify/correct which player a scanned
    jersey number actually is, without resending the team's whole roster."""
    existing = next((r for r in core.list_roster(conn, team_id) if r["id"] == entry_id), None)
    if existing is None:
        not_found("Roster entry not found on this team.")
    fields = {k: v for k, v in body.model_dump().items() if v is not None and k != "player_id"}
    if fields:
        try:
            core.update_roster_entry(conn, entry_id, **fields)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if body.player_id is not None:
        core.link_roster_entry_to_player(conn, entry_id, body.player_id)
    return next(r for r in core.list_roster(conn, team_id) if r["id"] == entry_id)


@app.delete("/teams/{team_id}/roster/{entry_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["teams"])
def api_remove_roster_entry(
    team_id: int, entry_id: int, conn=Depends(get_conn), user=Depends(require_writer)
):
    """Removes a single roster row — e.g. a mis-scanned or duplicate entry.
    If it's linked to a player profile, only this roster spot goes away;
    the player record itself is untouched."""
    existing = next((r for r in core.list_roster(conn, team_id) if r["id"] == entry_id), None)
    if existing is None:
        not_found("Roster entry not found on this team.")
    core.remove_roster_entry(conn, entry_id)


@app.get("/teams/{team_id}/coaches", tags=["teams"])
def api_team_coaches(team_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_team_coaches(conn, team_id)


@app.post("/teams/{team_id}/coaches/{coach_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["teams"])
def api_assign_coach(
    team_id: int, coach_id: int, conn=Depends(get_conn), user=Depends(require_writer)
):
    try:
        core.assign_coach_to_team(conn, team_id, coach_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@app.delete("/teams/{team_id}/coaches/{coach_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["teams"])
def api_remove_coach(
    team_id: int, coach_id: int, conn=Depends(get_conn), user=Depends(require_writer)
):
    core.remove_coach_from_team(conn, team_id, coach_id)


# ---------------------------------------------------------------------------
# Coaches
# ---------------------------------------------------------------------------

class CoachCreate(BaseModel):
    first_name: str
    last_name: str | None = None
    nickname: str | None = None
    phone: str | None = None
    email: str | None = None


class CoachUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    nickname: str | None = None
    phone: str | None = None
    email: str | None = None


@app.get("/coaches", tags=["coaches"])
def api_list_coaches(
    include_deleted: bool = False, conn=Depends(get_conn), user=Depends(get_current_user)
) -> list[dict]:
    return core.list_coaches(conn, include_deleted=include_deleted)


@app.post("/coaches", status_code=status.HTTP_201_CREATED, tags=["coaches"])
def api_create_coach(body: CoachCreate, conn=Depends(get_conn), user=Depends(require_writer)) -> dict:
    hide = _hides_contacts(user)
    coach_id = core.add_coach(
        conn, body.first_name, body.last_name, body.nickname,
        None if hide else body.phone, None if hide else body.email,
    )
    return core.get_coach(conn, coach_id)


@app.get("/coaches/{coach_id}", tags=["coaches"])
def api_get_coach(coach_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> dict:
    coach = core.get_coach(conn, coach_id)
    if coach is None:
        not_found("Coach not found.")
    return coach


@app.patch("/coaches/{coach_id}", tags=["coaches"])
def api_update_coach(
    coach_id: int, body: CoachUpdate, conn=Depends(get_conn), user=Depends(require_writer)
) -> dict:
    fields = _writable_fields(user, body.model_dump())
    if fields:
        core.update_coach(conn, coach_id, **fields)
    coach = core.get_coach(conn, coach_id)
    if coach is None:
        not_found("Coach not found.")
    return coach


@app.delete("/coaches/{coach_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["coaches"])
def api_delete_coach(coach_id: int, conn=Depends(get_conn), user=Depends(require_admin)):
    core.soft_delete_coach(conn, coach_id)


@app.post("/coaches/{coach_id}/restore", status_code=status.HTTP_204_NO_CONTENT, tags=["coaches"])
def api_restore_coach(coach_id: int, conn=Depends(get_conn), user=Depends(require_admin)):
    core.restore_coach(conn, coach_id)


@app.get("/coaches/{coach_id}/children-in-division", tags=["coaches"])
def api_coach_children_in_division(
    coach_id: int, division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)
) -> list[dict]:
    """This coach's registered child(ren) in one division -- an explicit
    link if one exists, otherwise a name-match fallback against that
    division's players that auto-links it when found (see
    core.find_coach_children_in_division). Used by the coach-carryover
    panel to guess who's "relevant" without asking."""
    return core.find_coach_children_in_division(conn, coach_id, division_id)


@app.get("/coaches/{coach_id}/children", tags=["coaches"])
def api_coach_children(coach_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_coach_children(conn, coach_id)


@app.post("/coaches/{coach_id}/children/{player_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["coaches"])
def api_link_coach_child(
    coach_id: int, player_id: int, conn=Depends(get_conn), user=Depends(require_writer)
):
    core.link_coach_child(conn, coach_id, player_id)


@app.delete("/coaches/{coach_id}/children/{player_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["coaches"])
def api_unlink_coach_child(
    coach_id: int, player_id: int, conn=Depends(get_conn), user=Depends(require_writer)
):
    core.unlink_coach_child(conn, coach_id, player_id)


@app.get("/coaches/{coach_id}/teams", tags=["coaches"])
def api_coach_teams(coach_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    """Every team this coach has ever been assigned to, across every
    division/season -- the Coaches page's "Teams coached" history."""
    return core.list_coach_teams(conn, coach_id)


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------

class PlayerCreate(BaseModel):
    first_name: str
    last_name: str | None = None
    nickname: str | None = None
    birth_date: str | None = None
    current_division_id: int | None = None
    contact_first_name: str | None = None
    contact_last_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    usa_ball_hockey_id: str | None = None


class PlayerUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    nickname: str | None = None
    birth_date: str | None = None
    current_division_id: int | None = None
    contact_first_name: str | None = None
    contact_last_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    usa_ball_hockey_id: str | None = None


@app.get("/players", tags=["players"])
def api_list_players(
    include_deleted: bool = False, conn=Depends(get_conn), user=Depends(get_current_user)
) -> list[dict]:
    return core.list_players(conn, include_deleted=include_deleted)


@app.post("/players", status_code=status.HTTP_201_CREATED, tags=["players"])
def api_create_player(body: PlayerCreate, conn=Depends(get_conn), user=Depends(require_writer)) -> dict:
    player_id = core.add_player(
        conn, body.first_name, body.last_name, body.nickname, body.birth_date, body.current_division_id,
        body.contact_first_name, body.contact_last_name,
        None if _hides_contacts(user) else body.contact_phone,
        None if _hides_contacts(user) else body.contact_email,
        body.usa_ball_hockey_id,
    )
    return core.get_player(conn, player_id)


@app.get("/players/{player_id}", tags=["players"])
def api_get_player(player_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> dict:
    player = core.get_player(conn, player_id)
    if player is None:
        not_found("Player not found.")
    return player


@app.patch("/players/{player_id}", tags=["players"])
def api_update_player(
    player_id: int, body: PlayerUpdate, conn=Depends(get_conn), user=Depends(require_writer)
) -> dict:
    fields = _writable_fields(user, body.model_dump())
    if fields:
        core.update_player(conn, player_id, **fields)
    player = core.get_player(conn, player_id)
    if player is None:
        not_found("Player not found.")
    return player


@app.put("/players/{player_id}/divisions/{division_id}", tags=["players"])
def api_register_player_in_division(
    player_id: int, division_id: int, conn=Depends(get_conn), user=Depends(require_writer)
) -> dict:
    """Also register a player in another age group this season -- see
    core.register_player_in_division (a division from another season
    replaces their registrations instead)."""
    if core.get_player(conn, player_id) is None:
        not_found("Player not found.")
    core.register_player_in_division(conn, player_id, division_id)
    return core.get_player(conn, player_id)


@app.delete("/players/{player_id}/divisions/{division_id}", tags=["players"])
def api_unregister_player_from_division(
    player_id: int, division_id: int, conn=Depends(get_conn), user=Depends(require_writer)
) -> dict:
    if core.get_player(conn, player_id) is None:
        not_found("Player not found.")
    core.unregister_player_from_division(conn, player_id, division_id)
    return core.get_player(conn, player_id)


@app.delete("/players/{player_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["players"])
def api_delete_player(player_id: int, conn=Depends(get_conn), user=Depends(require_admin)):
    core.soft_delete_player(conn, player_id)


@app.post("/players/{player_id}/restore", status_code=status.HTTP_204_NO_CONTENT, tags=["players"])
def api_restore_player(player_id: int, conn=Depends(get_conn), user=Depends(require_admin)):
    core.restore_player(conn, player_id)


@app.get("/players/{player_id}/siblings", tags=["players"])
def api_player_siblings(player_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_siblings(conn, player_id)


@app.post("/players/{player_id}/siblings/{other_player_id}", tags=["players"])
def api_link_siblings(
    player_id: int, other_player_id: int, conn=Depends(get_conn), user=Depends(require_writer)
) -> dict:
    """Directly marks two players as siblings, without needing a parent
    record with real name/contact info on file first -- see
    core.link_players_as_siblings. If either already has a parent group,
    that group is reused/merged rather than creating a redundant one."""
    try:
        parent_id = core.link_players_as_siblings(conn, player_id, other_player_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return {"parent_id": parent_id}


class PlayerRequestCreate(BaseModel):
    note: str | None = None
    hard: bool = False


class PlayerRequestUpdate(BaseModel):
    hard: bool


@app.get("/players/{player_id}/requests", tags=["players"])
def api_player_requests(player_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_player_requests(conn, player_id)


@app.post("/players/{player_id}/requests/{requested_player_id}", status_code=status.HTTP_201_CREATED, tags=["players"])
def api_add_player_request(
    player_id: int, requested_player_id: int, body: PlayerRequestCreate = PlayerRequestCreate(),
    conn=Depends(get_conn), user=Depends(require_writer),
) -> dict:
    """A play-with request for next draft/season -- a deliberate, non-
    family "friend" ask, distinct from siblings (see core.add_player_request
    for exactly how auto_draft treats it differently: a soft preference by
    default, or with hard: true, a placement as firm as a sibling's)."""
    try:
        request_id = core.add_player_request(conn, player_id, requested_player_id, body.note, body.hard)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return next(r for r in core.list_player_requests(conn, player_id) if r["id"] == request_id)


@app.put("/players/{player_id}/requests/{request_id}", tags=["players"])
def api_update_player_request(
    player_id: int, request_id: int, body: PlayerRequestUpdate,
    conn=Depends(get_conn), user=Depends(require_writer),
) -> dict:
    """Switch a request between soft and hard -- see core.set_player_request_hard."""
    if not any(r["id"] == request_id for r in core.list_player_requests(conn, player_id)):
        not_found("Request not found for this player.")
    core.set_player_request_hard(conn, request_id, body.hard)
    return next(r for r in core.list_player_requests(conn, player_id) if r["id"] == request_id)


@app.delete(
    "/players/{player_id}/requests/{request_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["players"]
)
def api_remove_player_request(
    player_id: int, request_id: int, conn=Depends(get_conn), user=Depends(require_writer)
):
    existing = next((r for r in core.list_player_requests(conn, player_id) if r["id"] == request_id), None)
    if existing is None:
        not_found("Request not found for this player.")
    core.remove_player_request(conn, request_id)


@app.get("/parents", tags=["players"])
def api_list_parents(conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_parents(conn)


class PlayerParentUpdate(BaseModel):
    parent_id: int | None = None


@app.put("/players/{player_id}/parent", tags=["players"])
def api_set_player_parent(
    player_id: int, body: PlayerParentUpdate, conn=Depends(get_conn), user=Depends(require_writer)
):
    """Manually link or unlink (parent_id: null) a player to a parent/
    sibling-group -- see GET /players/{id}/siblings and core.set_player_parent."""
    core.set_player_parent(conn, player_id, body.parent_id)


@app.get("/players/{player_id}/evaluations", tags=["players"])
def api_player_evaluations(player_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_evaluations(conn, player_id)


@app.get("/players/{player_id}/history", tags=["players"])
def api_player_history(player_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    """One row per season this player was rostered in (team, position,
    grade, coaches) -- the same data the Streamlit app's All Players
    dialog shows under a selected player, pre-joined here so the frontend
    doesn't need a position + coaches round trip per season."""
    history = core.player_division_history(conn, player_id)
    grade_by_season: dict[tuple, str] = {}
    for e in core.list_evaluations(conn, player_id):
        key = (e["year"], e["season"], e["age_group"])
        grade_by_season.setdefault(key, e["grade"])  # newest first, so first write wins
    for h in history:
        h["position"] = core.get_position(conn, player_id, h["division_id"], h["team_id"])
        h["coaches"] = core.list_team_coaches(conn, h["team_id"])
        h["grade"] = grade_by_season.get((h["year"], h["season"], h["age_group"]))
    return history


class EvaluationCreate(BaseModel):
    division_id: int
    team_id: int | None = None
    grade: str


@app.post("/players/{player_id}/evaluations", status_code=status.HTTP_201_CREATED, tags=["players"])
def api_add_evaluation(
    player_id: int, body: EvaluationCreate, conn=Depends(get_conn), user=Depends(require_writer)
) -> dict:
    evaluation_id = core.add_evaluation(conn, player_id, body.division_id, body.team_id, body.grade)
    return next(e for e in core.list_evaluations(conn, player_id) if e["id"] == evaluation_id)


@app.delete(
    "/players/{player_id}/evaluations/{evaluation_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["players"]
)
def api_delete_evaluation(
    player_id: int, evaluation_id: int, conn=Depends(get_conn), user=Depends(require_admin)
):
    """Admin-only: the one way an evaluation can be removed (a grade input
    never erases one -- see core.set_season_grade). Audit-logged."""
    existing = next((e for e in core.list_evaluations(conn, player_id) if e["id"] == evaluation_id), None)
    if existing is None:
        not_found("Evaluation not found for this player.")
    core.delete_evaluation(conn, evaluation_id)


class MovePlayerRequest(BaseModel):
    division_id: int
    new_team_id: int
    note: str | None = None


@app.post("/players/{player_id}/move", status_code=status.HTTP_204_NO_CONTENT, tags=["players"])
def api_move_player(
    player_id: int, body: MovePlayerRequest, conn=Depends(get_conn), user=Depends(require_writer)
):
    """A move `note` (a reason) is only ever stored if the caller is an
    admin — same restriction as the Streamlit app's Team Rosters page,
    where a coach can move a player but doesn't get to (or see) why past
    moves happened."""
    note = body.note if user["is_admin"] else None
    try:
        core.move_player_to_team(conn, player_id, body.division_id, body.new_team_id, note=note)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@app.get("/players/{player_id}/move-notes", tags=["players"])
def api_player_move_notes(
    player_id: int, division_id: int | None = None, conn=Depends(get_conn), user=Depends(require_admin)
) -> list[dict]:
    """Admin-only, matching the Streamlit app — a coach can call
    POST .../move but never sees why a player was moved before."""
    return core.list_player_move_notes(conn, player_id, division_id)


@app.get("/players/{player_id}/position", tags=["players"])
def api_get_position(
    player_id: int, division_id: int, team_id: int, conn=Depends(get_conn), user=Depends(get_current_user)
) -> dict:
    """A player's position on one specific team/division — pass both as
    query params, e.g. ?division_id=1&team_id=2."""
    return {"position": core.get_position(conn, player_id, division_id, team_id)}


class PositionUpdate(BaseModel):
    division_id: int
    team_id: int
    position: str


@app.put("/players/{player_id}/position", tags=["players"])
def api_set_position(
    player_id: int, body: PositionUpdate, conn=Depends(get_conn), user=Depends(require_writer)
) -> dict:
    """Sets (or clears, with position="") a player's position for one
    team/division — a plain current value with no history, same as the
    Streamlit app's Position dropdown."""
    core.set_position(conn, player_id, body.division_id, body.team_id, body.position)
    return {"position": core.get_position(conn, player_id, body.division_id, body.team_id)}


@app.get("/players/{player_id}/season-grade", tags=["players"])
def api_get_season_grade(
    player_id: int, division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)
) -> dict:
    """A player's "Season Grade" for one division -- the most recent
    evaluation on record there, same value the Team Rosters grid's Season
    Grade cell shows/edits (a quick edit here updates that evaluation in
    place rather than growing a new history row; use POST
    /players/{id}/evaluations for that)."""
    return {"grade": core.get_season_grade(conn, player_id, division_id)}


class SeasonGradeUpdate(BaseModel):
    division_id: int
    team_id: int | None = None
    grade: str


@app.put("/players/{player_id}/season-grade", tags=["players"])
def api_set_season_grade(
    player_id: int, body: SeasonGradeUpdate, conn=Depends(get_conn), user=Depends(require_writer)
) -> dict:
    core.set_season_grade(conn, player_id, body.division_id, body.team_id, body.grade)
    return {"grade": core.get_season_grade(conn, player_id, body.division_id)}


# ---------------------------------------------------------------------------
# Draft
# ---------------------------------------------------------------------------

@app.get("/divisions/{division_id}/draft", tags=["draft"])
def api_get_draft(division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> dict | None:
    """None if no draft exists yet for this division. Otherwise also
    includes "order" (the snake draft's team sequence) and, while
    in_progress, "current_team_id"/"current_team_name"/"round" -- so the
    frontend doesn't need to reimplement the snake-order math client-side
    just to show whose turn it is."""
    draft = core.get_draft(conn, division_id)
    if draft is None:
        return None
    order = core.list_draft_order(conn, draft["id"])
    draft["order"] = order
    if draft["status"] == "in_progress" and order:
        current_team_id = core.current_pick_team_id(conn, draft["id"])
        draft["current_team_id"] = current_team_id
        draft["current_team_name"] = next(
            (o["team_name"] for o in order if o["team_id"] == current_team_id), None
        )
        draft["round"] = (draft["current_pick_number"] - 1) // len(order) + 1
    return draft


@app.get("/divisions/{division_id}/draft/auto-draft-run", tags=["draft"])
def api_get_auto_draft_run(division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> dict | None:
    return core.get_auto_draft_run(conn, division_id)


@app.get("/divisions/{division_id}/draft/pool", tags=["draft"])
def api_draft_pool(division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.draft_pool(conn, division_id)


class DraftStartRequest(BaseModel):
    team_ids_in_order: list[int]


@app.post("/divisions/{division_id}/draft/start", status_code=status.HTTP_201_CREATED, tags=["draft"])
def api_start_draft(
    division_id: int, body: DraftStartRequest, conn=Depends(get_conn), user=Depends(require_writer)
) -> dict:
    try:
        draft_id = core.start_draft(conn, division_id, body.team_ids_in_order)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return core.get_draft(conn, division_id) or {"id": draft_id}


class DraftPickRequest(BaseModel):
    player_id: int


@app.post("/drafts/{draft_id}/pick", status_code=status.HTTP_201_CREATED, tags=["draft"])
def api_submit_pick(
    draft_id: int, body: DraftPickRequest, conn=Depends(get_conn), user=Depends(require_writer)
) -> dict:
    try:
        roster_entry_id = core.submit_draft_pick(conn, draft_id, body.player_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return {"roster_entry_id": roster_entry_id}


@app.post("/drafts/{draft_id}/undo", status_code=status.HTTP_204_NO_CONTENT, tags=["draft"])
def api_undo_pick(draft_id: int, conn=Depends(get_conn), user=Depends(require_writer)):
    try:
        core.undo_last_pick(conn, draft_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@app.get("/drafts/{draft_id}/picks", tags=["draft"])
def api_list_picks(draft_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_draft_picks(conn, draft_id)


@app.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["draft"])
def api_delete_draft(draft_id: int, conn=Depends(get_conn), user=Depends(require_admin)):
    core.delete_draft(conn, draft_id)


@app.post("/divisions/{division_id}/auto-draft", tags=["draft"])
def api_auto_draft(division_id: int, conn=Depends(get_conn), user=Depends(require_writer)) -> dict:
    try:
        return core.auto_draft(conn, division_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@app.post("/divisions/{division_id}/auto-draft/undo", status_code=status.HTTP_204_NO_CONTENT, tags=["draft"])
def api_undo_auto_draft(division_id: int, conn=Depends(get_conn), user=Depends(require_writer)):
    try:
        core.undo_auto_draft(conn, division_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


# ---------------------------------------------------------------------------
# Game sheets (Claude OCR extraction) & Games
# ---------------------------------------------------------------------------

def _anthropic_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ANTHROPIC_API_KEY is not configured on the server.",
        )
    return anthropic.Anthropic(api_key=api_key)


@app.post("/game-sheets/extract", tags=["games"])
async def api_extract_game_sheet(
    file: UploadFile = File(...), conn=Depends(get_conn), user=Depends(require_writer)
) -> list[dict]:
    """Extracts one uploaded game sheet (PDF or image) with Claude. A
    multi-page PDF is split first (core.split_pdf_bytes) so each page comes
    back as its own queue item, matching the Streamlit app's Import
    Scoresheets flow -- one {label, extracted_data, duplicate} entry per
    page, "duplicate" set to the existing game on file under that label
    (core.find_game_by_source_file) so the frontend can prompt Replace/Skip
    before saving over it."""
    raw = await file.read()
    mime = core.guess_mime(file.filename)
    pages = core.split_pdf_bytes(raw) if mime == "application/pdf" else [raw]
    stem = Path(file.filename).stem
    client = _anthropic_client()

    results = []
    for i, page_bytes in enumerate(pages, start=1):
        label = file.filename if len(pages) == 1 else f"{stem}_p{i}.pdf"
        page_mime = mime if len(pages) == 1 else "application/pdf"
        try:
            content_block = core.build_content_block(page_bytes, page_mime)
            extracted = core.extract_game_sheet(client, content_block)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Extraction failed for {label}: {e}"
            )
        results.append({
            "label": label, "extracted_data": extracted,
            "duplicate": core.find_game_by_source_file(conn, label),
        })
    return results


class GameSave(BaseModel):
    data: dict
    source_file: str
    division_id: int


@app.post("/games", status_code=status.HTTP_201_CREATED, tags=["games"])
def api_create_game(body: GameSave, conn=Depends(get_conn), user=Depends(require_writer)) -> dict:
    """`division_id` here is the *working* division -- insert_game resolves
    the game's own division (year/season same as working, age group from
    the sheet itself, which can differ) via core.resolve_division_id."""
    try:
        game_id, already_existed = core.insert_game(conn, body.data, body.source_file, body.division_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return {"id": game_id, "already_existed": already_existed}


@app.get("/games/{game_id}", tags=["games"])
def api_get_game(game_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> dict:
    data, source_file = core.load_game(conn, game_id)
    if data is None:
        not_found("Game not found.")
    return {"data": data, "source_file": source_file}


class GameUpdate(BaseModel):
    data: dict
    division_id: int


@app.patch("/games/{game_id}", tags=["games"])
def api_update_game(game_id: int, body: GameUpdate, conn=Depends(get_conn), user=Depends(require_writer)) -> dict:
    existing, _ = core.load_game(conn, game_id)
    if existing is None:
        not_found("Game not found.")
    try:
        core.update_game(conn, game_id, body.data, body.division_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return {"id": game_id}


@app.delete("/games/{game_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["games"])
def api_delete_game(game_id: int, conn=Depends(get_conn), user=Depends(require_writer)):
    core.delete_game(conn, game_id)


# ---------------------------------------------------------------------------
# Roles & Users -- admin-only, mirroring the Streamlit app's User Management
# tab, which is itself gated to admins only (require_admin below matches).
# ---------------------------------------------------------------------------

class RoleCreate(BaseModel):
    name: str
    pages: list[str] = []
    read_only: bool = False
    hide_contact_details: bool = False


class RoleUpdate(BaseModel):
    name: str | None = None
    pages: list[str] | None = None
    read_only: bool | None = None
    hide_contact_details: bool | None = None


@app.get("/roles", tags=["users"])
def api_list_roles(conn=Depends(get_conn), user=Depends(require_admin)) -> list[dict]:
    return core.list_roles(conn)


@app.post("/roles", status_code=status.HTTP_201_CREATED, tags=["users"])
def api_create_role(body: RoleCreate, conn=Depends(get_conn), user=Depends(require_admin)) -> dict:
    role_id = core.add_role(
        conn, body.name, pages=body.pages, read_only=body.read_only, hide_contact_details=body.hide_contact_details
    )
    return core.get_role(conn, role_id)


@app.patch("/roles/{role_id}", tags=["users"])
def api_update_role(role_id: int, body: RoleUpdate, conn=Depends(get_conn), user=Depends(require_admin)) -> dict:
    core.update_role(
        conn, role_id, name=body.name, pages=body.pages,
        read_only=body.read_only, hide_contact_details=body.hide_contact_details,
    )
    role = core.get_role(conn, role_id)
    if role is None:
        not_found("Role not found.")
    return role


@app.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["users"])
def api_delete_role(role_id: int, conn=Depends(get_conn), user=Depends(require_admin)):
    """Any user with this role loses it (falls back to no page access, same
    as core.delete_role's ON DELETE SET NULL) rather than the delete being
    blocked."""
    core.delete_role(conn, role_id)


@app.get("/users", tags=["users"])
def api_list_users(
    include_deleted: bool = False, conn=Depends(get_conn), user=Depends(require_admin)
) -> list[dict]:
    return core.list_users(conn, include_deleted=include_deleted)


class UserCreate(BaseModel):
    email: str
    password: str
    display_name: str | None = None
    is_admin: bool = False
    role_id: int | None = None


@app.post("/users", status_code=status.HTTP_201_CREATED, tags=["users"])
def api_create_user(body: UserCreate, conn=Depends(get_conn), user=Depends(require_admin)) -> dict:
    if core.get_user_by_email(conn, body.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A user with that email already exists.")
    user_id = core.add_user(
        conn, body.email, body.password, display_name=body.display_name,
        is_admin=body.is_admin, role_id=body.role_id,
    )
    return core.get_user(conn, user_id)


class UserUpdate(BaseModel):
    display_name: str | None = None
    is_admin: bool | None = None
    role_id: int | None = None
    coach_id: int | None = None


@app.patch("/users/{user_id}", tags=["users"])
def api_update_user(user_id: int, body: UserUpdate, conn=Depends(get_conn), user=Depends(require_admin)) -> dict:
    """Bundles update_user + set_user_role + set_user_coach into one call,
    matching the Streamlit User Management tab's single "Save" button for a
    user row -- pass only the fields that changed; role_id/coach_id are
    each still applied (including clearing to null) whenever the field is
    present in the request body at all, same as PATCH elsewhere in this API."""
    body_fields = body.model_dump(exclude_unset=True)
    if "display_name" in body_fields or "is_admin" in body_fields:
        core.update_user(conn, user_id, display_name=body.display_name, is_admin=body.is_admin)
    if "role_id" in body_fields:
        core.set_user_role(conn, user_id, body.role_id)
    if "coach_id" in body_fields:
        core.set_user_coach(conn, user_id, body.coach_id)
    updated = core.get_user(conn, user_id)
    if updated is None:
        not_found("User not found.")
    return updated


class PasswordUpdate(BaseModel):
    password: str


@app.put("/users/{user_id}/password", tags=["users"])
def api_set_user_password(
    user_id: int, body: PasswordUpdate, conn=Depends(get_conn), user=Depends(require_admin)
) -> dict:
    core.set_user_password(conn, user_id, body.password)
    return {"ok": True}


@app.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["users"])
def api_deactivate_user(user_id: int, conn=Depends(get_conn), user=Depends(require_admin)):
    core.soft_delete_user(conn, user_id)


@app.post("/users/{user_id}/restore", status_code=status.HTTP_204_NO_CONTENT, tags=["users"])
def api_restore_user(user_id: int, conn=Depends(get_conn), user=Depends(require_admin)):
    core.restore_user(conn, user_id)


# ---------------------------------------------------------------------------
# Misc lookups
# ---------------------------------------------------------------------------

@app.get("/age-groups", tags=["meta"])
def api_age_groups(user: dict = Depends(get_current_user)) -> dict[str, str]:
    return core.AGE_GROUPS


@app.get("/settings/working-division", tags=["meta"])
def api_get_working_division(conn=Depends(get_conn), user=Depends(get_current_user)) -> dict:
    """The app-wide "Working Division" (game_sheet_core.get_setting) --
    shared with the Streamlit app's sidebar selector via the same
    app_settings row, so switching it in either app carries over to the
    other. Falls back to the first division (by list_divisions' own
    year/season/age_group ordering) if unset or no longer valid."""
    divisions = core.list_divisions(conn)
    valid_ids = {d["id"] for d in divisions}
    saved = core.get_setting(conn, "working_division_id")
    saved_id = int(saved) if saved and saved.isdigit() else None
    if saved_id not in valid_ids:
        saved_id = divisions[0]["id"] if divisions else None
    return {"division_id": saved_id}


class WorkingDivisionUpdate(BaseModel):
    division_id: int | None = None


@app.put("/settings/working-division", tags=["meta"])
def api_set_working_division(
    body: WorkingDivisionUpdate, conn=Depends(get_conn), user=Depends(get_current_user)
) -> dict:
    core.set_setting(conn, "working_division_id", str(body.division_id) if body.division_id is not None else "")
    return {"division_id": body.division_id}
