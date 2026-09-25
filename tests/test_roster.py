import game_sheet_core as core


def test_replace_roster_adds_entries(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    core.replace_roster(conn, team_id, [
        {"number": "9", "name": "Sidney Crosby"},
        {"number": "4", "name": "Bobby Orr"},
    ])
    roster = core.list_roster(conn, team_id)
    assert len(roster) == 2
    numbers = {r["number"] for r in roster}
    assert numbers == {"9", "4"}


def test_replace_roster_preserves_player_link_by_number(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.replace_roster(conn, team_id, [{"number": "9", "name": "Sidney Crosby"}])
    entry = core.list_roster(conn, team_id)[0]
    core.link_roster_entry_to_player(conn, entry["id"], player_id)

    # Re-saving the roster (e.g. editing the grid) with the same number
    # should keep the existing player link, not silently drop it.
    core.replace_roster(conn, team_id, [{"number": "9", "name": "Sid Crosby"}])
    entry_after = core.list_roster(conn, team_id)[0]
    assert entry_after["player_id"] == player_id


def test_replace_roster_removes_entries_not_in_the_new_list(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    core.replace_roster(conn, team_id, [{"number": "9", "name": "Sidney Crosby"}])
    core.replace_roster(conn, team_id, [{"number": "4", "name": "Bobby Orr"}])
    roster = core.list_roster(conn, team_id)
    assert len(roster) == 1
    assert roster[0]["number"] == "4"


def test_roster_is_scoped_to_its_team(conn, division_id):
    team1 = core.add_team(conn, division_id, "Avalanche")
    team2 = core.add_team(conn, division_id, "Wild")
    core.replace_roster(conn, team1, [{"number": "9", "name": "Sidney Crosby"}])
    core.replace_roster(conn, team2, [{"number": "4", "name": "Bobby Orr"}])
    assert len(core.list_roster(conn, team1)) == 1
    assert core.list_roster(conn, team1)[0]["name"] == "Sidney Crosby"
    assert len(core.list_roster(conn, team2)) == 1


def test_roster_reverts_to_unlinked_when_player_is_soft_deleted(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.replace_roster(conn, team_id, [{"number": "9", "name": "Sidney Crosby"}])
    entry = core.list_roster(conn, team_id)[0]
    core.link_roster_entry_to_player(conn, entry["id"], player_id)
    assert core.list_roster(conn, team_id)[0]["player_id"] == player_id

    core.soft_delete_player(conn, player_id)
    entry_after = core.list_roster(conn, team_id)[0]
    assert entry_after["player_id"] is None
    assert entry_after["name"] == "Sidney Crosby"  # falls back to the roster's own scanned name

    core.restore_player(conn, player_id)
    assert core.list_roster(conn, team_id)[0]["player_id"] == player_id
