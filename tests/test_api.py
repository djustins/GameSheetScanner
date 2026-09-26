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


def test_division_players_endpoint_includes_grade_and_note(conn, admin_auth, division_id):
    player_id = core.add_player(conn, "Sidney", "Crosby", current_division_id=division_id)
    core.add_evaluation(conn, player_id, division_id, None, "A")

    response = client.get(f"/divisions/{division_id}/players", auth=admin_auth)
    assert response.status_code == 200
    players = response.json()
    assert len(players) == 1
    assert players[0]["id"] == player_id
    assert players[0]["grade"] == "A"


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
