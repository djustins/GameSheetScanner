"""
Targeted smoke tests for api.py -- not exhaustive per-endpoint coverage
(the underlying business logic is already tested at the game_sheet_core
level), just enough to confirm the HTTP layer itself is wired correctly:
auth, one full CRUD lifecycle, permission gating, and a feature endpoint
that composes multiple core functions.
"""

import os

import pytest

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DB_URL:
    pytest.skip("TEST_DATABASE_URL not set", allow_module_level=True)
os.environ["DATABASE_URL"] = TEST_DB_URL

import game_sheet_core as core  # noqa: E402
import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(api.app)

ADMIN_EMAIL = "api_test_admin@example.com"
ADMIN_PASSWORD = "testpass123"
READONLY_EMAIL = "api_test_readonly@example.com"
READONLY_PASSWORD = "testpass456"


@pytest.fixture
def admin_auth(conn):
    core.add_user(conn, ADMIN_EMAIL, ADMIN_PASSWORD, display_name="API Admin", is_admin=True)
    return (ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture
def readonly_auth(conn):
    role_id = core.add_role(conn, "API Read Only", pages=list(core.PAGES), read_only=True)
    core.add_user(conn, READONLY_EMAIL, READONLY_PASSWORD, display_name="API Read Only", role_id=role_id)
    return (READONLY_EMAIL, READONLY_PASSWORD)


def test_unauthenticated_request_is_rejected(conn):
    response = client.get("/divisions")
    assert response.status_code == 401


def test_wrong_password_is_rejected(conn, admin_auth):
    response = client.get("/divisions", auth=(ADMIN_EMAIL, "wrong-password"))
    assert response.status_code == 401


def test_me_reflects_the_logged_in_user(conn, admin_auth):
    response = client.get("/me", auth=admin_auth)
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == ADMIN_EMAIL
    assert body["is_admin"] is True


def test_me_reports_role_pages_and_contact_hiding(conn):
    role_id = core.add_role(conn, "Scorekeeper", pages=["schedule", "standings"], hide_contact_details=True)
    core.add_user(conn, "scorekeeper@example.com", "pw-12345678", role_id=role_id)
    body = client.get("/me", auth=("scorekeeper@example.com", "pw-12345678")).json()
    assert sorted(body["pages"]) == ["schedule", "standings"]
    assert body["hide_contact_details"] is True


def test_login_returns_a_working_token(conn, admin_auth):
    response = client.post("/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == ADMIN_EMAIL
    token = body["token"]
    assert token.startswith("gst_")

    me = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == ADMIN_EMAIL


def test_login_rejects_wrong_password(conn, admin_auth):
    response = client.post("/login", json={"email": ADMIN_EMAIL, "password": "wrong-password"})
    assert response.status_code == 401


def test_team_crud_lifecycle(conn, admin_auth, division_id):
    create = client.post("/teams", json={"division_id": division_id, "name": "Avalanche"}, auth=admin_auth)
    assert create.status_code == 201
    team = create.json()
    assert team["name"] == "Avalanche"

    listed = client.get(f"/divisions/{division_id}/teams", auth=admin_auth)
    assert listed.status_code == 200
    assert [t["id"] for t in listed.json()] == [team["id"]]

    updated = client.patch(f"/teams/{team['id']}", json={"color": "#FF0000"}, auth=admin_auth)
    assert updated.status_code == 200
    assert updated.json()["color"] == "#FF0000"

    deleted = client.delete(f"/teams/{team['id']}", auth=admin_auth)
    assert deleted.status_code == 204
    assert client.get(f"/divisions/{division_id}/teams", auth=admin_auth).json() == []


def test_roster_entry_renumber_lifecycle(conn, admin_auth, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    create = client.post(
        f"/teams/{team_id}/roster", json={"number": "TBD1", "name": "Sidney Crosby"}, auth=admin_auth
    )
    assert create.status_code == 201
    entry_id = create.json()["id"]

    renumbered = client.patch(f"/teams/{team_id}/roster/{entry_id}", json={"number": "87"}, auth=admin_auth)
    assert renumbered.status_code == 200
    assert renumbered.json()["number"] == "87"

    other = client.post(f"/teams/{team_id}/roster", json={"number": "9", "name": "Bobby Orr"}, auth=admin_auth)
    conflict = client.patch(
        f"/teams/{team_id}/roster/{other.json()['id']}", json={"number": "87"}, auth=admin_auth
    )
    assert conflict.status_code == 409

    swapped = client.patch(
        f"/teams/{team_id}/roster/{other.json()['id']}",
        json={"number": "87", "swap_numbers": True}, auth=admin_auth,
    )
    assert swapped.status_code == 200
    assert swapped.json()["number"] == "87"
    numbers = {r["id"]: r["number"] for r in client.get(f"/teams/{team_id}/roster", auth=admin_auth).json()}
    assert numbers[entry_id] == "9"

    missing = client.patch(f"/teams/{team_id}/roster/999999", json={"number": "1"}, auth=admin_auth)
    assert missing.status_code == 404


def test_set_and_get_position(conn, admin_auth, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    player_id = core.add_player(conn, "Sidney", "Crosby")

    initial = client.get(
        f"/players/{player_id}/position", params={"division_id": division_id, "team_id": team_id}, auth=admin_auth
    )
    assert initial.status_code == 200
    assert initial.json() == {"position": None}

    setresp = client.put(
        f"/players/{player_id}/position",
        json={"division_id": division_id, "team_id": team_id, "position": "Goalie"},
        auth=admin_auth,
    )
    assert setresp.status_code == 200
    assert setresp.json() == {"position": "Goalie"}

    getresp = client.get(
        f"/players/{player_id}/position", params={"division_id": division_id, "team_id": team_id}, auth=admin_auth
    )
    assert getresp.json() == {"position": "Goalie"}

    cleared = client.put(
        f"/players/{player_id}/position",
        json={"division_id": division_id, "team_id": team_id, "position": ""},
        auth=admin_auth,
    )
    assert cleared.json() == {"position": None}


def test_set_and_get_season_grade(conn, admin_auth, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    player_id = core.add_player(conn, "Sidney", "Crosby")

    initial = client.get(
        f"/players/{player_id}/season-grade", params={"division_id": division_id}, auth=admin_auth
    )
    assert initial.status_code == 200
    assert initial.json() == {"grade": None}

    setresp = client.put(
        f"/players/{player_id}/season-grade",
        json={"division_id": division_id, "team_id": team_id, "grade": "A"},
        auth=admin_auth,
    )
    assert setresp.status_code == 200
    assert setresp.json() == {"grade": "A"}

    # Editing again updates the same evaluation in place rather than adding
    # a second one.
    client.put(
        f"/players/{player_id}/season-grade",
        json={"division_id": division_id, "team_id": team_id, "grade": "B"},
        auth=admin_auth,
    )
    evaluations = client.get(f"/players/{player_id}/evaluations", auth=admin_auth).json()
    assert len(evaluations) == 1
    assert evaluations[0]["grade"] == "B"


def test_roster_entry_update_relinks_player(conn, admin_auth, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    create = client.post(
        f"/teams/{team_id}/roster", json={"number": "9", "name": "Sidney Crosby"}, auth=admin_auth
    )
    entry_id = create.json()["id"]
    assert create.json()["player_id"] is None

    player_id = core.add_player(conn, "Sidney", "Crosby")
    relinked = client.patch(
        f"/teams/{team_id}/roster/{entry_id}", json={"player_id": player_id}, auth=admin_auth
    )
    assert relinked.status_code == 200
    assert relinked.json()["player_id"] == player_id


def test_roster_entry_delete(conn, admin_auth, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    create = client.post(
        f"/teams/{team_id}/roster", json={"number": "9", "name": "Sidney Crosby"}, auth=admin_auth
    )
    entry_id = create.json()["id"]

    deleted = client.delete(f"/teams/{team_id}/roster/{entry_id}", auth=admin_auth)
    assert deleted.status_code == 204
    assert client.get(f"/teams/{team_id}/roster", auth=admin_auth).json() == []

    missing = client.delete(f"/teams/{team_id}/roster/{entry_id}", auth=admin_auth)
    assert missing.status_code == 404


def test_read_only_role_can_read_but_not_write(conn, readonly_auth, division_id):
    listed = client.get(f"/divisions/{division_id}/teams", auth=readonly_auth)
    assert listed.status_code == 200

    blocked = client.post("/teams", json={"division_id": division_id, "name": "Avalanche"}, auth=readonly_auth)
    assert blocked.status_code == 403


def test_delete_requires_admin_not_just_a_writer_role(conn, division_id):
    role_id = core.add_role(conn, "API Coach", pages=list(core.PAGES), read_only=False)
    core.add_user(conn, "api_test_coach@example.com", "testpass789", role_id=role_id)
    coach_auth = ("api_test_coach@example.com", "testpass789")

    team_id = core.add_team(conn, division_id, "Avalanche")
    blocked = client.delete(f"/teams/{team_id}", auth=coach_auth)
    assert blocked.status_code == 403


def test_assign_coach_conflict_surfaces_as_409(conn, admin_auth, division_id):
    team_a = core.add_team(conn, division_id, "Avalanche")
    team_b = core.add_team(conn, division_id, "Wild")
    coach_id = core.add_coach(conn, "Mario", "Lemieux")
    core.assign_coach_to_team(conn, team_a, coach_id)

    conflict = client.post(f"/teams/{team_b}/coaches/{coach_id}", auth=admin_auth)
    assert conflict.status_code == 409


def test_delete_evaluation_endpoint(conn, admin_auth, division_id):
    player_id = core.add_player(conn, "Sidney", "Crosby")
    created = client.post(
        f"/players/{player_id}/evaluations", json={"division_id": division_id, "grade": "A"}, auth=admin_auth
    )
    assert created.status_code == 201
    evaluation_id = created.json()["id"]

    deleted = client.delete(f"/players/{player_id}/evaluations/{evaluation_id}", auth=admin_auth)
    assert deleted.status_code == 204
    assert client.get(f"/players/{player_id}/evaluations", auth=admin_auth).json() == []

    missing = client.delete(f"/players/{player_id}/evaluations/{evaluation_id}", auth=admin_auth)
    assert missing.status_code == 404


def test_division_players_endpoint_includes_grade_and_note(conn, admin_auth, division_id):
    player_id = core.add_player(conn, "Sidney", "Crosby", current_division_id=division_id)
    core.add_evaluation(conn, player_id, division_id, None, "A")

    response = client.get(f"/divisions/{division_id}/players", auth=admin_auth)
    assert response.status_code == 200
    players = response.json()
    assert len(players) == 1
    assert players[0]["id"] == player_id
    assert players[0]["grade"] == "A"


def test_division_players_endpoint_flags_carryover_grade(conn, admin_auth, division_id):
    # Flagged only when the carried-over grade is from a different age group.
    same_age_division_id = core.add_division(conn, 2025, "Summer", "Penguin")
    younger_division_id = core.add_division(conn, 2024, "Summer", "Chipmunk")
    player_id = core.add_player(conn, "Sidney", "Crosby", current_division_id=division_id)
    core.add_evaluation(conn, player_id, younger_division_id, None, "C")

    response = client.get(f"/divisions/{division_id}/players", auth=admin_auth)
    assert response.status_code == 200
    players = response.json()
    assert players[0]["grade"] == "C"
    assert players[0]["grade_is_carryover"] is True

    core.add_evaluation(conn, player_id, same_age_division_id, None, "B")
    players = client.get(f"/divisions/{division_id}/players", auth=admin_auth).json()
    assert players[0]["grade"] == "B"
    assert players[0]["grade_is_carryover"] is False

    core.add_evaluation(conn, player_id, division_id, None, "A")
    response = client.get(f"/divisions/{division_id}/players", auth=admin_auth)
    players = response.json()
    assert players[0]["grade"] == "A"
    assert players[0]["grade_is_carryover"] is False


def test_division_season_grades_endpoint(conn, admin_auth, division_id):
    p1 = core.add_player(conn, "Sidney", "Crosby")
    p2 = core.add_player(conn, "Wayne", "Gretzky")
    core.add_evaluation(conn, p1, division_id, None, "A")
    core.add_evaluation(conn, p2, division_id, None, "C")

    response = client.get(f"/divisions/{division_id}/season-grades", auth=admin_auth)
    assert response.status_code == 200
    grades = response.json()
    assert grades[str(p1)] == "A"
    assert grades[str(p2)] == "C"


def test_player_history_endpoint_includes_team_grade_and_coach(conn, admin_auth, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    coach_id = core.add_coach(conn, "Mario", "Lemieux")
    core.assign_coach_to_team(conn, team_id, coach_id)
    player_id = core.add_player(conn, "Sidney", "Crosby", current_division_id=division_id)
    core.add_roster_entry(conn, team_id, "87", "Sidney Crosby", player_id=player_id)
    core.set_position(conn, player_id, division_id, team_id, "Center")
    core.add_evaluation(conn, player_id, division_id, team_id, "A")

    response = client.get(f"/players/{player_id}/history", auth=admin_auth)
    assert response.status_code == 200
    history = response.json()
    assert len(history) == 1
    assert history[0]["team_name"] == "Avalanche"
    assert history[0]["position"] == "Center"
    assert history[0]["grade"] == "A"
    assert [c["id"] for c in history[0]["coaches"]] == [coach_id]


def test_division_players_endpoint_excludes_sub_placeholder(conn, admin_auth, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    real_player_id = core.add_player(conn, "Sidney", "Crosby", current_division_id=division_id)
    sub_player_id = core.add_player(conn, "Sub", current_division_id=division_id)
    core.add_roster_entry(conn, team_id, "99", "Sub", player_id=sub_player_id)

    response = client.get(f"/divisions/{division_id}/players", auth=admin_auth)
    assert response.status_code == 200
    ids = [p["id"] for p in response.json()]
    assert real_player_id in ids
    assert sub_player_id not in ids

    # Sub still shows up on the team's own roster.
    roster = client.get(f"/teams/{team_id}/roster", auth=admin_auth)
    assert sub_player_id in [r["player_id"] for r in roster.json()]


def test_api_token_lifecycle(conn, admin_auth):
    create = client.post("/tokens", json={"name": "My Laptop"}, auth=admin_auth)
    assert create.status_code == 201
    token = create.json()["token"]
    assert token.startswith("gst_")

    # The token authenticates just like the password did.
    me = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == ADMIN_EMAIL

    listed = client.get("/tokens", auth=admin_auth)
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert "token" not in listed.json()[0]

    revoke = client.delete(f"/tokens/{create.json()['id']}", auth=admin_auth)
    assert revoke.status_code == 204

    rejected = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert rejected.status_code == 401


def test_remove_all_players_from_division_endpoint(conn, admin_auth, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    player_id = core.add_player(conn, "Sidney", "Crosby", current_division_id=division_id)
    core.add_roster_entry(conn, team_id, "9", "Sidney Crosby", player_id=player_id)

    response = client.delete(f"/divisions/{division_id}/players", auth=admin_auth)
    assert response.status_code == 200
    assert response.json() == {"removed": 1}
    assert core.get_player(conn, player_id) is not None
    assert core.list_roster(conn, team_id) == []


def test_coach_teams_and_unlink_child_endpoints(conn, admin_auth, division_id):
    coach_id = core.add_coach(conn, "Mario", "Lemieux")
    team_id = core.add_team(conn, division_id, "Avalanche")
    core.assign_coach_to_team(conn, team_id, coach_id)
    player_id = core.add_player(conn, "Austin", "Lemieux")
    core.link_coach_child(conn, coach_id, player_id)

    teams = client.get(f"/coaches/{coach_id}/teams", auth=admin_auth)
    assert teams.status_code == 200
    assert [t["team_id"] for t in teams.json()] == [team_id]

    unlinked = client.delete(f"/coaches/{coach_id}/children/{player_id}", auth=admin_auth)
    assert unlinked.status_code == 204
    assert client.get(f"/coaches/{coach_id}/children", auth=admin_auth).json() == []


def test_draft_endpoint_includes_order_and_current_team(conn, admin_auth, division_id):
    team_a = core.add_team(conn, division_id, "Avalanche")
    team_b = core.add_team(conn, division_id, "Wild")
    core.add_player(conn, "Sidney", "Crosby", current_division_id=division_id)

    no_draft = client.get(f"/divisions/{division_id}/draft", auth=admin_auth)
    assert no_draft.status_code == 200
    assert no_draft.json() is None

    started = client.post(
        f"/divisions/{division_id}/draft/start", json={"team_ids_in_order": [team_a, team_b]}, auth=admin_auth
    )
    assert started.status_code == 201

    draft = client.get(f"/divisions/{division_id}/draft", auth=admin_auth)
    assert draft.status_code == 200
    body = draft.json()
    assert [o["team_id"] for o in body["order"]] == [team_a, team_b]
    assert body["current_team_id"] == team_a
    assert body["round"] == 1

    no_run = client.get(f"/divisions/{division_id}/draft/auto-draft-run", auth=admin_auth)
    assert no_run.status_code == 200
    assert no_run.json() is None


def test_move_player_note_is_dropped_for_non_admin(conn, division_id):
    role_id = core.add_role(conn, "API Coach 2", pages=list(core.PAGES), read_only=False)
    core.add_user(conn, "api_test_coach2@example.com", "testpass789", role_id=role_id)
    coach_auth = ("api_test_coach2@example.com", "testpass789")

    team1 = core.add_team(conn, division_id, "Avalanche")
    team2 = core.add_team(conn, division_id, "Wild")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.add_roster_entry(conn, team1, "9", "Sidney Crosby", player_id=player_id)

    response = client.post(
        f"/players/{player_id}/move",
        json={"division_id": division_id, "new_team_id": team2, "note": "Should be dropped"},
        auth=coach_auth,
    )
    assert response.status_code == 204
    assert core.list_player_move_notes(conn, player_id) == []


def test_role_lifecycle(conn, admin_auth):
    create = client.post(
        "/roles", json={"name": "Coach", "pages": ["rosters", "coaches"], "read_only": False}, auth=admin_auth
    )
    assert create.status_code == 201
    role = create.json()
    assert sorted(role["pages"]) == ["coaches", "rosters"]

    updated = client.patch(f"/roles/{role['id']}", json={"read_only": True}, auth=admin_auth)
    assert updated.status_code == 200
    assert updated.json()["read_only"] is True
    assert sorted(updated.json()["pages"]) == ["coaches", "rosters"]

    deleted = client.delete(f"/roles/{role['id']}", auth=admin_auth)
    assert deleted.status_code == 204
    assert all(r["id"] != role["id"] for r in client.get("/roles", auth=admin_auth).json())


def test_role_endpoints_require_admin(conn, readonly_auth):
    response = client.get("/roles", auth=readonly_auth)
    assert response.status_code == 403


def test_user_lifecycle(conn, admin_auth):
    coach_id = core.add_coach(conn, "Mario", "Lemieux")
    role_id = core.add_role(conn, "Coach Role", pages=["rosters"])

    create = client.post(
        "/users",
        json={"email": "new_coach@example.com", "password": "temp12345", "display_name": "New Coach"},
        auth=admin_auth,
    )
    assert create.status_code == 201
    user = create.json()
    assert user["email"] == "new_coach@example.com"
    assert "password_hash" not in user

    duplicate = client.post(
        "/users", json={"email": "new_coach@example.com", "password": "x"}, auth=admin_auth
    )
    assert duplicate.status_code == 409

    updated = client.patch(
        f"/users/{user['id']}", json={"role_id": role_id, "coach_id": coach_id}, auth=admin_auth
    )
    assert updated.status_code == 200
    assert updated.json()["role_id"] == role_id
    assert updated.json()["coach_id"] == coach_id

    cleared = client.patch(f"/users/{user['id']}", json={"role_id": None}, auth=admin_auth)
    assert cleared.json()["role_id"] is None

    login_before = client.post(
        "/login", json={"email": "new_coach@example.com", "password": "temp12345"}
    )
    assert login_before.status_code == 200

    reset = client.put(f"/users/{user['id']}/password", json={"password": "newpassword1"}, auth=admin_auth)
    assert reset.status_code == 200

    login_after_old = client.post(
        "/login", json={"email": "new_coach@example.com", "password": "temp12345"}
    )
    assert login_after_old.status_code == 401
    login_after_new = client.post(
        "/login", json={"email": "new_coach@example.com", "password": "newpassword1"}
    )
    assert login_after_new.status_code == 200

    deactivated = client.delete(f"/users/{user['id']}", auth=admin_auth)
    assert deactivated.status_code == 204
    active = client.get("/users", auth=admin_auth).json()
    assert all(u["id"] != user["id"] for u in active)
    all_users = client.get("/users", params={"include_deleted": True}, auth=admin_auth).json()
    assert any(u["id"] == user["id"] for u in all_users)

    restored = client.post(f"/users/{user['id']}/restore", auth=admin_auth)
    assert restored.status_code == 204
    active_again = client.get("/users", auth=admin_auth).json()
    assert any(u["id"] == user["id"] for u in active_again)


def test_user_endpoints_require_admin(conn, readonly_auth):
    response = client.get("/users", auth=readonly_auth)
    assert response.status_code == 403


def test_schedule_import_and_clear(conn, admin_auth, division_id):
    imported = client.post(
        f"/divisions/{division_id}/schedule",
        json={"rows": [
            {"game_date": "2026-09-01", "home_team": "Avalanche", "away_team": "Wild"},
            {"game_date": "", "home_team": "Missing date", "away_team": "Skipped"},
        ]},
        auth=admin_auth,
    )
    assert imported.status_code == 200
    assert imported.json() == {"saved": 1}

    scheduled = client.get(f"/divisions/{division_id}/schedule", auth=admin_auth).json()
    assert len(scheduled) == 1
    assert scheduled[0]["home_team"] == "Avalanche"

    cleared = client.delete(f"/divisions/{division_id}/schedule", auth=admin_auth)
    assert cleared.status_code == 204
    assert client.get(f"/divisions/{division_id}/schedule", auth=admin_auth).json() == []


def test_coach_carryover_endpoints(conn, admin_auth, division_id):
    # The `division_id` fixture is a 2026 Summer Penguin division -- an
    # earlier Penguin division should be found as "previous" for it.
    previous_division_id = core.add_division(conn, 2025, "Fall", "Penguin")
    team_id = core.add_team(conn, previous_division_id, "Avalanche")
    coach_id = core.add_coach(conn, "Mario", "Lemieux")
    core.assign_coach_to_team(conn, team_id, coach_id)

    previous = client.get(f"/divisions/{division_id}/previous", auth=admin_auth)
    assert previous.status_code == 200
    assert previous.json()["id"] == previous_division_id

    child_id = core.add_player(
        conn, "Austin", "Lemieux", current_division_id=division_id,
        contact_first_name="Mario", contact_last_name="Lemieux",
    )
    matched = client.get(
        f"/coaches/{coach_id}/children-in-division",
        params={"division_id": division_id},
        auth=admin_auth,
    )
    assert matched.status_code == 200
    assert [c["id"] for c in matched.json()] == [child_id]


def test_parent_and_siblings_endpoints(conn, admin_auth):
    p1 = core.add_player(conn, "Leah", "Pratti")
    p2 = core.add_player(conn, "Nico", "Pratti")

    parents_before = client.get("/parents", auth=admin_auth)
    assert parents_before.status_code == 200

    linked = client.put(f"/players/{p1}/parent", json={"parent_id": None}, auth=admin_auth)
    assert linked.status_code == 200

    parent_id = core.get_or_create_parent(conn, "Pratti", "Family", None, "pratti@example.com")
    client.put(f"/players/{p1}/parent", json={"parent_id": parent_id}, auth=admin_auth)
    client.put(f"/players/{p2}/parent", json={"parent_id": parent_id}, auth=admin_auth)

    siblings = client.get(f"/players/{p1}/siblings", auth=admin_auth)
    assert siblings.status_code == 200
    assert [s["id"] for s in siblings.json()] == [p2]


def test_link_siblings_endpoint_creates_shared_parent(conn, admin_auth):
    p1 = core.add_player(conn, "Leah", "Pratti")
    p2 = core.add_player(conn, "Nico", "Pratti")
    assert core.get_player(conn, p1)["parent_id"] is None

    linked = client.post(f"/players/{p1}/siblings/{p2}", auth=admin_auth)
    assert linked.status_code == 200
    parent_id = linked.json()["parent_id"]
    assert parent_id is not None
    assert core.get_player(conn, p1)["parent_id"] == parent_id
    assert core.get_player(conn, p2)["parent_id"] == parent_id

    siblings = client.get(f"/players/{p1}/siblings", auth=admin_auth)
    assert [s["id"] for s in siblings.json()] == [p2]


def test_link_siblings_endpoint_merges_existing_groups(conn, admin_auth):
    p1 = core.add_player(conn, "Leah", "Pratti")
    p2 = core.add_player(conn, "Nico", "Pratti")
    p3 = core.add_player(conn, "Mia", "Pratti")
    core.link_players_as_siblings(conn, p2, p3)  # p2 and p3 already siblings

    client.post(f"/players/{p1}/siblings/{p2}", auth=admin_auth)

    siblings = client.get(f"/players/{p1}/siblings", auth=admin_auth)
    assert sorted(s["id"] for s in siblings.json()) == sorted([p2, p3])


def test_link_siblings_endpoint_rejects_self_link(conn, admin_auth):
    p1 = core.add_player(conn, "Leah", "Pratti")
    response = client.post(f"/players/{p1}/siblings/{p1}", auth=admin_auth)
    assert response.status_code == 409


def test_player_usa_ball_hockey_id_field(conn, admin_auth):
    create = client.post(
        "/players", json={"first_name": "Sidney", "last_name": "Crosby", "usa_ball_hockey_id": "USA12345"},
        auth=admin_auth,
    )
    assert create.status_code == 201
    player_id = create.json()["id"]
    assert create.json()["usa_ball_hockey_id"] == "USA12345"

    updated = client.patch(
        f"/players/{player_id}", json={"usa_ball_hockey_id": "USA99999"}, auth=admin_auth
    )
    assert updated.status_code == 200
    assert updated.json()["usa_ball_hockey_id"] == "USA99999"


def test_recycle_bin_endpoints(conn, admin_auth, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    coach_id = core.add_coach(conn, "Mario", "Lemieux")

    client.delete(f"/teams/{team_id}", auth=admin_auth)
    client.delete(f"/players/{player_id}", auth=admin_auth)
    client.delete(f"/coaches/{coach_id}", auth=admin_auth)
    client.delete(f"/divisions/{division_id}", auth=admin_auth)

    deleted_divisions = client.get("/divisions/deleted", auth=admin_auth).json()
    assert any(d["id"] == division_id for d in deleted_divisions)

    deleted_teams = client.get("/teams/deleted", auth=admin_auth).json()
    assert any(t["id"] == team_id for t in deleted_teams)

    deleted_players = client.get("/players", params={"include_deleted": True}, auth=admin_auth).json()
    assert any(p["id"] == player_id and p["deleted_at"] for p in deleted_players)

    deleted_coaches = client.get("/coaches", params={"include_deleted": True}, auth=admin_auth).json()
    assert any(c["id"] == coach_id and c["deleted_at"] for c in deleted_coaches)

    assert client.post(f"/divisions/{division_id}/restore", auth=admin_auth).status_code == 204
    assert client.post(f"/teams/{team_id}/restore", auth=admin_auth).status_code == 204
    assert client.post(f"/players/{player_id}/restore", auth=admin_auth).status_code == 204
    assert client.post(f"/coaches/{coach_id}/restore", auth=admin_auth).status_code == 204

    assert any(d["id"] == division_id for d in client.get("/divisions", auth=admin_auth).json())
    assert any(t["id"] == team_id for t in client.get(f"/divisions/{division_id}/teams", auth=admin_auth).json())
    active_players = client.get("/players", auth=admin_auth).json()
    assert any(p["id"] == player_id for p in active_players)
    active_coaches = client.get("/coaches", auth=admin_auth).json()
    assert any(c["id"] == coach_id for c in active_coaches)


def test_player_import_plan_and_apply(conn, admin_auth, division_id):
    csv_bytes = (
        b"Name,Date of Birth,Team,Number,Coach\n"
        b"Sidney Crosby,2015-08-07,Avalanche,87,Mario Lemieux\n"
        b"Wayne Gretzky,,Avalanche,99,\n"
    )
    plan_resp = client.post(
        f"/divisions/{division_id}/players/import-plan",
        files={"file": ("players.csv", csv_bytes, "text/csv")},
        auth=admin_auth,
    )
    assert plan_resp.status_code == 200
    body = plan_resp.json()
    assert body["columns"]["name"] == "Name"
    plan = body["plan"]
    assert len(plan) == 2
    assert all(e["status"] == "create" for e in plan)
    assert all(e["resolved_action"] == "create" for e in plan)

    apply_resp = client.post(
        f"/divisions/{division_id}/players/import-apply", json={"plan": plan}, auth=admin_auth
    )
    assert apply_resp.status_code == 200
    result = apply_resp.json()
    assert result["created"] == 2
    assert result["rostered"] == 2
    assert result["coached"] == 1

    all_players = client.get("/players", auth=admin_auth).json()
    assert {"Sidney Crosby", "Wayne Gretzky"} <= {p["name"] for p in all_players}

    roster = client.get(f"/divisions/{division_id}/teams", auth=admin_auth).json()
    assert any(t["name"] == "Avalanche" for t in roster)


def test_player_import_plan_rejects_file_with_no_name_column(conn, admin_auth, division_id):
    csv_bytes = b"Team,Number\nAvalanche,87\n"
    response = client.post(
        f"/divisions/{division_id}/players/import-plan",
        files={"file": ("players.csv", csv_bytes, "text/csv")},
        auth=admin_auth,
    )
    assert response.status_code == 400


def test_player_import_plan_detects_conflict(conn, admin_auth, division_id):
    core.add_player(conn, "Sidney", "Crosby", birth_date="2015-08-07")
    csv_bytes = b"Name,Date of Birth\nSidney Crosby,2015-08-08\n"
    response = client.post(
        f"/divisions/{division_id}/players/import-plan",
        files={"file": ("players.csv", csv_bytes, "text/csv")},
        auth=admin_auth,
    )
    assert response.status_code == 200
    plan = response.json()["plan"]
    assert plan[0]["status"] == "conflict"
    assert plan[0]["resolved_action"] is None


def test_export_workbook_endpoint(conn, admin_auth, division_id):
    response = client.get(f"/divisions/{division_id}/export.xlsx", auth=admin_auth)
    assert response.status_code == 200
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert len(response.content) > 0


def _game_payload(home="Avalanche", away="Wild", home_score=3, away_score=2, **overrides):
    data = {
        "game_date": "2026-07-14", "division": "Penguin",
        "home_team": home, "home_color": "Red", "home_final_score": home_score,
        "away_team": away, "away_color": "Blue", "away_final_score": away_score,
        "goals": [], "penalties": [], "shootout_attempts": [],
    }
    data.update(overrides)
    return data


def test_game_crud_lifecycle(conn, admin_auth, division_id):
    create = client.post(
        "/games",
        json={"data": _game_payload(), "source_file": "sheet1.pdf", "division_id": division_id},
        auth=admin_auth,
    )
    assert create.status_code == 201
    game_id = create.json()["id"]
    assert create.json()["already_existed"] is False

    fetched = client.get(f"/games/{game_id}", auth=admin_auth)
    assert fetched.status_code == 200
    assert fetched.json()["source_file"] == "sheet1.pdf"
    assert fetched.json()["data"]["home_team"] == "Avalanche"

    updated = client.patch(
        f"/games/{game_id}",
        json={"data": _game_payload(home_score=5), "division_id": division_id},
        auth=admin_auth,
    )
    assert updated.status_code == 200
    assert client.get(f"/games/{game_id}", auth=admin_auth).json()["data"]["home_final_score"] == 5

    deleted = client.delete(f"/games/{game_id}", auth=admin_auth)
    assert deleted.status_code == 204
    assert client.get(f"/games/{game_id}", auth=admin_auth).status_code == 404


def test_create_game_rejects_a_tie(conn, admin_auth, division_id):
    response = client.post(
        "/games",
        json={
            "data": _game_payload(home_score=2, away_score=2), "source_file": "tie.pdf",
            "division_id": division_id,
        },
        auth=admin_auth,
    )
    assert response.status_code == 409


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]


class _FakeMessages:
    def __init__(self, text):
        self._text = text

    def create(self, **kwargs):
        return _FakeMessage(self._text)


class _FakeAnthropicClient:
    def __init__(self, text):
        self.messages = _FakeMessages(text)


def test_extract_game_sheet_endpoint(conn, admin_auth, division_id, monkeypatch):
    import json as _json

    fake_extracted = _game_payload()
    monkeypatch.setattr(api, "_anthropic_client", lambda: _FakeAnthropicClient(_json.dumps(fake_extracted)))

    response = client.post(
        "/game-sheets/extract",
        files={"file": ("sheet1.png", b"fake-image-bytes", "image/png")},
        auth=admin_auth,
    )
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["label"] == "sheet1.png"
    assert results[0]["extracted_data"]["home_team"] == "Avalanche"
    assert results[0]["duplicate"] is None


def test_extract_game_sheet_flags_duplicate(conn, admin_auth, division_id, monkeypatch):
    import json as _json

    core.insert_game(conn, _game_payload(), source_file="sheet1.png", working_division_id=division_id)
    fake_extracted = _game_payload()
    monkeypatch.setattr(api, "_anthropic_client", lambda: _FakeAnthropicClient(_json.dumps(fake_extracted)))

    response = client.post(
        "/game-sheets/extract",
        files={"file": ("sheet1.png", b"fake-image-bytes", "image/png")},
        auth=admin_auth,
    )
    assert response.status_code == 200
    assert response.json()[0]["duplicate"] is not None
    assert response.json()[0]["duplicate"]["home_team"] == "Avalanche"


def test_extract_game_sheet_requires_writer(conn, readonly_auth):
    response = client.post(
        "/game-sheets/extract",
        files={"file": ("sheet1.png", b"fake-image-bytes", "image/png")},
        auth=readonly_auth,
    )
    assert response.status_code == 403


def test_working_division_setting(conn, admin_auth, division_id):
    response = client.get("/settings/working-division", auth=admin_auth)
    assert response.status_code == 200
    assert response.json()["division_id"] == division_id

    other_division_id = core.add_division(conn, 2025, "Fall", "Penguin")
    updated = client.put(
        "/settings/working-division", json={"division_id": other_division_id}, auth=admin_auth
    )
    assert updated.status_code == 200
    assert updated.json()["division_id"] == other_division_id

    fetched_again = client.get("/settings/working-division", auth=admin_auth)
    assert fetched_again.json()["division_id"] == other_division_id


def test_player_requests_endpoints(conn, admin_auth, readonly_auth):
    p1 = core.add_player(conn, "Sidney", "Crosby")
    p2 = core.add_player(conn, "Wayne", "Gretzky")

    assert client.post(f"/players/{p1}/requests/{p2}", json={}, auth=readonly_auth).status_code == 403

    created = client.post(f"/players/{p1}/requests/{p2}", json={"note": "carpool"}, auth=admin_auth)
    assert created.status_code == 201
    assert created.json()["player_id"] == p2
    assert created.json()["direction"] == "made"

    duplicate = client.post(f"/players/{p1}/requests/{p2}", json={}, auth=admin_auth)
    assert duplicate.status_code == 409

    received = client.get(f"/players/{p2}/requests", auth=admin_auth).json()
    assert received[0]["player_id"] == p1
    assert received[0]["direction"] == "made"  # mutual: recorded on both players

    request_id = created.json()["id"]
    assert created.json()["hard"] is False
    made_hard = client.put(f"/players/{p1}/requests/{request_id}", json={"hard": True}, auth=admin_auth)
    assert made_hard.status_code == 200 and made_hard.json()["hard"] is True
    assert client.put(f"/players/{p1}/requests/{request_id}", json={"hard": False}, auth=readonly_auth).status_code == 403

    assert client.delete(f"/players/{p1}/requests/{request_id}", auth=admin_auth).status_code == 204
    assert client.get(f"/players/{p1}/requests", auth=admin_auth).json() == []
    assert client.delete(f"/players/{p1}/requests/{request_id}", auth=admin_auth).status_code == 404


def test_stats_and_standings_endpoints_title_case_and_sort(conn, admin_auth, division_id):
    from test_stats import _game_with_stats
    _game_with_stats(conn, division_id)

    stats = client.get(f"/divisions/{division_id}/stats", auth=admin_auth).json()
    assert {s["team"] for s in stats} == {"Avalanche", "Wild"}
    points = [s["points"] for s in stats]
    assert points == sorted(points, reverse=True)
    assert stats[0]["number"] == "9"  # 2G 1A leads

    standings = client.get(f"/divisions/{division_id}/standings", auth=admin_auth).json()
    assert [s["team"] for s in standings] == ["Avalanche", "Wild"]


def test_hide_contact_role_gets_contacts_redacted_and_cannot_overwrite_them(conn, admin_auth):
    role_id = core.add_role(conn, "Coach", pages=list(core.PAGES), hide_contact_details=True)
    core.add_user(conn, "coach@example.com", "pw-12345678", role_id=role_id)
    hidden = ("coach@example.com", "pw-12345678")
    player_id = core.add_player(
        conn, "Sidney", "Crosby", contact_first_name="Trina", contact_phone="412-555-0100",
        contact_email="trina@example.com",
    )
    core.add_coach(conn, "Jared", "Bednar", phone="303-555-0199", email="jb@example.com")

    player = client.get(f"/players/{player_id}", auth=hidden).json()
    assert player["contact_phone"] is None and player["contact_email"] is None
    assert player["contact_first_name"] == "Trina"
    assert all(c["phone"] is None and c["email"] is None for c in client.get("/coaches", auth=hidden).json())
    assert all(p["phone"] is None for p in client.get("/parents", auth=hidden).json())
    # Their own login email isn't someone else's contact detail.
    assert client.get("/me", auth=hidden).json()["email"] == "coach@example.com"

    # A save echoing back the redacted blank must not wipe the real value.
    client.patch(f"/players/{player_id}", json={"contact_phone": "", "nickname": "Sid"}, auth=hidden)
    stored = core.get_player(conn, player_id)
    assert stored["contact_phone"] == "412-555-0100"
    assert stored["nickname"] == "Sid"

    # Admins still see everything.
    assert client.get(f"/players/{player_id}", auth=admin_auth).json()["contact_phone"] == "412-555-0100"


def test_react_parity_endpoints(conn, admin_auth, division_id):
    team_a = core.add_team(conn, division_id, "Avalanche")
    team_b = core.add_team(conn, division_id, "Wild")
    goalie = core.add_player(conn, "Marc", "Fleury", current_division_id=division_id)
    skater = core.add_player(conn, "Sidney", "Crosby", current_division_id=division_id)
    core.set_registration_position(conn, goalie, division_id, "Goalie")
    core.add_evaluation(conn, skater, division_id, None, "A")
    core.add_player_request(conn, skater, goalie)

    reqs = client.get(f"/divisions/{division_id}/requests", auth=admin_auth).json()
    assert len(reqs) == 1 and reqs[0]["avoid"] is False

    auto = client.post(f"/divisions/{division_id}/auto-draft", auth=admin_auth)
    assert auto.status_code == 200
    results = client.get(f"/divisions/{division_id}/draft/auto-draft-results", auth=admin_auth).json()
    assert {r["name"] for r in results["rows"]} == {"Marc Fleury", "Sidney Crosby"}
    assert results["run"]["warnings"] == auto.json()["warnings"]

    overview = {t["name"]: t for t in client.get(f"/divisions/{division_id}/teams-overview", auth=admin_auth).json()}
    assert set(overview) == {"Avalanche", "Wild"}
    assert sum(t["goalies"] for t in overview.values()) == 1
    assert sum(t["grades"].get("A", 0) for t in overview.values()) == 1

    assert client.get(f"/divisions/{division_id}/draft/notes", auth=admin_auth).json() == {"notes": ""}
    saved = client.put(f"/divisions/{division_id}/draft/notes", json={"notes": "hello"}, auth=admin_auth)
    assert saved.json() == {"notes": "hello"}
    assert client.get(f"/divisions/{division_id}/draft/notes", auth=admin_auth).json() == {"notes": "hello"}

    regs = client.get(f"/players/{goalie}/registrations", auth=admin_auth).json()
    assert regs == [{"division_id": division_id, "position": "Goalie", "main": True}]
    client.put(f"/players/{goalie}/divisions/{division_id}/position", json={"position": "Forward"}, auth=admin_auth)
    goalie_team = next(r["team_id"] for r in results["rows"] if r["player_id"] == goalie)
    pos = client.get(
        f"/players/{goalie}/position?division_id={division_id}&team_id={goalie_team}", auth=admin_auth
    ).json()
    assert pos == {"position": None, "registered": "Forward"}
    assert team_a and team_b


def test_division_evaluations_endpoint(conn, admin_auth, division_id):
    graded = core.add_player(conn, "Sidney", "Crosby", current_division_id=division_id)
    core.add_player(conn, "Wayne", "Gretzky", current_division_id=division_id)
    core.add_evaluation(conn, graded, division_id, None, "A")
    body = client.get(f"/divisions/{division_id}/evaluations", auth=admin_auth).json()
    assert [(e["name"], e["grade"]) for e in body["evaluations"]] == [("Sidney Crosby", "A")]
    assert [p["name"] for p in body["not_evaluated"]] == ["Wayne Gretzky"]


def test_league_site_sync_is_admin_only(conn, admin_auth, readonly_auth, monkeypatch):
    calls = []
    monkeypatch.setattr(
        api.league_site_sync, "sync",
        lambda conn, dry_run=False, refresh_days=None: calls.append((dry_run, refresh_days)) or [{"league": "x"}],
    )
    assert client.post("/league-site/sync", auth=readonly_auth).status_code == 403
    assert calls == []

    response = client.post("/league-site/sync?dry_run=true", auth=admin_auth)
    assert response.status_code == 200
    assert response.json() == [{"league": "x"}]
    assert calls == [(True, api.league_site_sync.REFRESH_DAYS)]
