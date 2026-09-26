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
require_writer below. Fine-grained per-page visibility (a role's `pages`
list) and hide_contact_details aren't enforced here: any authenticated
user can read any resource through this API. Tighten that if this API
gets exposed beyond trusted, already-vetted league admins/coaches.
"""

import os

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials, HTTPBearer
from pydantic import BaseModel

import game_sheet_core as core

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Set the DATABASE_URL environment variable (same one app.py uses).")

app = FastAPI(
    title="GameSheetScanner API",
    description="Programmatic access to the same league data the Streamlit app manages.",
    version="1.0.0",
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
        return user
    if credentials is not None:
        user = core.verify_login(conn, credentials.username, credentials.password)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password.",
                headers={"WWW-Authenticate": "Basic"},
            )
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


def not_found(detail: str = "Not found"):
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


@app.get("/", tags=["meta"])
def root():
    return {"name": "GameSheetScanner API", "docs": "/docs"}


@app.get("/me", tags=["meta"])
def whoami(user: dict = Depends(get_current_user)) -> dict:
    """Confirms your credentials work and shows what they grant — the
    quickest way to sanity-check an API client's auth setup."""
    return {
        "email": user["email"], "display_name": user["display_name"], "is_admin": user["is_admin"],
        "read_only": user["read_only"], "coach_id": user["coach_id"], "pages": user["pages"],
    }


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


@app.get("/divisions/{division_id}/teams", tags=["divisions"])
def api_division_teams(division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_teams(conn, division_id)


@app.get("/divisions/{division_id}/players", tags=["divisions"])
def api_division_players(division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    players = core.list_players_in_division(conn, division_id)
    grades = core.get_latest_grades(conn, division_id, [p["id"] for p in players])
    notes = core.player_experience_notes(conn, division_id, [p["id"] for p in players])
    for p in players:
        p["grade"] = grades.get(p["id"])
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


@app.get("/divisions/{division_id}/schedule", tags=["divisions"])
def api_division_schedule(division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_schedule(conn, division_id)


@app.get("/divisions/{division_id}/games", tags=["divisions"])
def api_division_games(division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_games(conn, division_id)


@app.get("/divisions/{division_id}/standings", tags=["divisions"])
def api_division_standings(division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.get_standings(conn, division_id)


@app.get("/divisions/{division_id}/stats", tags=["divisions"])
def api_division_stats(division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.get_player_stats(conn, division_id)


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
def api_list_coaches(conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_coaches(conn)


@app.post("/coaches", status_code=status.HTTP_201_CREATED, tags=["coaches"])
def api_create_coach(body: CoachCreate, conn=Depends(get_conn), user=Depends(require_writer)) -> dict:
    coach_id = core.add_coach(conn, body.first_name, body.last_name, body.nickname, body.phone, body.email)
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
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if fields:
        core.update_coach(conn, coach_id, **fields)
    coach = core.get_coach(conn, coach_id)
    if coach is None:
        not_found("Coach not found.")
    return coach


@app.delete("/coaches/{coach_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["coaches"])
def api_delete_coach(coach_id: int, conn=Depends(get_conn), user=Depends(require_admin)):
    core.soft_delete_coach(conn, coach_id)


@app.get("/coaches/{coach_id}/children", tags=["coaches"])
def api_coach_children(coach_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_coach_children(conn, coach_id)


@app.post("/coaches/{coach_id}/children/{player_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["coaches"])
def api_link_coach_child(
    coach_id: int, player_id: int, conn=Depends(get_conn), user=Depends(require_writer)
):
    core.link_coach_child(conn, coach_id, player_id)


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


@app.get("/players", tags=["players"])
def api_list_players(conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_players(conn)


@app.post("/players", status_code=status.HTTP_201_CREATED, tags=["players"])
def api_create_player(body: PlayerCreate, conn=Depends(get_conn), user=Depends(require_writer)) -> dict:
    player_id = core.add_player(
        conn, body.first_name, body.last_name, body.nickname, body.birth_date, body.current_division_id,
        body.contact_first_name, body.contact_last_name, body.contact_phone, body.contact_email,
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
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if fields:
        core.update_player(conn, player_id, **fields)
    player = core.get_player(conn, player_id)
    if player is None:
        not_found("Player not found.")
    return player


@app.delete("/players/{player_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["players"])
def api_delete_player(player_id: int, conn=Depends(get_conn), user=Depends(require_admin)):
    core.soft_delete_player(conn, player_id)


@app.get("/players/{player_id}/siblings", tags=["players"])
def api_player_siblings(player_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_siblings(conn, player_id)


@app.get("/players/{player_id}/evaluations", tags=["players"])
def api_player_evaluations(player_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> list[dict]:
    return core.list_evaluations(conn, player_id)


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


# ---------------------------------------------------------------------------
# Draft
# ---------------------------------------------------------------------------

@app.get("/divisions/{division_id}/draft", tags=["draft"])
def api_get_draft(division_id: int, conn=Depends(get_conn), user=Depends(get_current_user)) -> dict | None:
    return core.get_draft(conn, division_id)


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
# Misc lookups
# ---------------------------------------------------------------------------

@app.get("/age-groups", tags=["meta"])
def api_age_groups(user: dict = Depends(get_current_user)) -> dict[str, str]:
    return core.AGE_GROUPS
