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


def test_move_player_to_team_updates_roster_entry(conn, division_id):
    team1 = core.add_team(conn, division_id, "Avalanche")
    team2 = core.add_team(conn, division_id, "Wild")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.add_roster_entry(conn, team1, "9", "Sidney Crosby", player_id=player_id)

    core.move_player_to_team(conn, player_id, division_id, team2)
    assert core.list_roster(conn, team1) == []
    assert [r["player_id"] for r in core.list_roster(conn, team2)] == [player_id]


def test_move_player_to_team_records_a_note_only_when_given(conn, division_id):
    team1 = core.add_team(conn, division_id, "Avalanche")
    team2 = core.add_team(conn, division_id, "Wild")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.add_roster_entry(conn, team1, "9", "Sidney Crosby", player_id=player_id)

    core.move_player_to_team(conn, player_id, division_id, team2)
    assert core.list_player_move_notes(conn, player_id) == []

    core.move_player_to_team(conn, player_id, division_id, team1, note="Balancing rosters")
    notes = core.list_player_move_notes(conn, player_id)
    assert len(notes) == 1
    assert notes[0]["from_team"] == "Wild"
    assert notes[0]["to_team"] == "Avalanche"
    assert notes[0]["note"] == "Balancing rosters"


def test_move_player_to_team_is_a_noop_for_the_same_team(conn, division_id):
    team1 = core.add_team(conn, division_id, "Avalanche")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.add_roster_entry(conn, team1, "9", "Sidney Crosby", player_id=player_id)
    core.move_player_to_team(conn, player_id, division_id, team1, note="Shouldn't record")
    assert core.list_player_move_notes(conn, player_id) == []


def test_move_player_to_team_raises_when_not_rostered_in_division(conn, division_id):
    team1 = core.add_team(conn, division_id, "Avalanche")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    try:
        core.move_player_to_team(conn, player_id, division_id, team1)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "isn't on a roster" in str(e)


def test_move_player_to_team_raises_when_team_is_in_a_different_division(conn, division_id):
    other_division_id = core.add_division(conn, 2026, "Summer", "Chipmunk")
    team1 = core.add_team(conn, division_id, "Avalanche")
    other_team = core.add_team(conn, other_division_id, "Blackhawks")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.add_roster_entry(conn, team1, "9", "Sidney Crosby", player_id=player_id)
    try:
        core.move_player_to_team(conn, player_id, division_id, other_team)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "isn't in this division" in str(e)


def test_list_player_move_notes_scoped_to_a_division(conn, division_id):
    other_division_id = core.add_division(conn, 2026, "Summer", "Chipmunk")
    team1 = core.add_team(conn, division_id, "Avalanche")
    team2 = core.add_team(conn, division_id, "Wild")
    other_team1 = core.add_team(conn, other_division_id, "Blackhawks")
    other_team2 = core.add_team(conn, other_division_id, "Bruins")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.add_roster_entry(conn, team1, "9", "Sidney Crosby", player_id=player_id)
    core.add_roster_entry(conn, other_team1, "9", "Sidney Crosby", player_id=player_id)

    core.move_player_to_team(conn, player_id, division_id, team2, note="Division A move")
    core.move_player_to_team(conn, player_id, other_division_id, other_team2, note="Division B move")

    assert len(core.list_player_move_notes(conn, player_id)) == 2
    scoped = core.list_player_move_notes(conn, player_id, division_id)
    assert len(scoped) == 1
    assert scoped[0]["note"] == "Division A move"
