import game_sheet_core as core


def test_signed_up_but_unrostered_player_is_included(conn, division_id):
    core.add_player(conn, "Sidney", "Crosby", current_division_id=division_id)
    players = core.list_players_in_division(conn, division_id)
    assert len(players) == 1
    assert players[0]["name"] == "Sidney Crosby"
    assert players[0]["teams"] == []


def test_rostered_player_is_included_even_without_current_division_id(conn, division_id):
    player_id = core.add_player(conn, "Sidney", "Crosby")  # no current_division_id set
    team_id = core.add_team(conn, division_id, "Avalanche")
    entry_id = core.add_roster_entry(conn, team_id, "87", "Sidney Crosby")
    core.link_roster_entry_to_player(conn, entry_id, player_id)

    players = core.list_players_in_division(conn, division_id)
    assert len(players) == 1
    assert players[0]["id"] == player_id
    assert players[0]["teams"] == ["Avalanche"]


def test_player_signed_up_and_rostered_appears_once(conn, division_id):
    player_id = core.add_player(conn, "Sidney", "Crosby", current_division_id=division_id)
    team_id = core.add_team(conn, division_id, "Avalanche")
    entry_id = core.add_roster_entry(conn, team_id, "87", "Sidney Crosby")
    core.link_roster_entry_to_player(conn, entry_id, player_id)

    players = core.list_players_in_division(conn, division_id)
    assert len(players) == 1
    assert players[0]["teams"] == ["Avalanche"]


def test_player_rostered_on_two_teams_in_the_division_lists_both(conn, division_id):
    player_id = core.add_player(conn, "Sidney", "Crosby")
    team1 = core.add_team(conn, division_id, "Avalanche")
    team2 = core.add_team(conn, division_id, "Wild")
    core.link_roster_entry_to_player(conn, core.add_roster_entry(conn, team1, "87", "Sidney Crosby"), player_id)
    core.link_roster_entry_to_player(conn, core.add_roster_entry(conn, team2, "9", "Sidney Crosby"), player_id)

    players = core.list_players_in_division(conn, division_id)
    assert len(players) == 1
    assert players[0]["teams"] == ["Avalanche", "Wild"]


def test_player_in_a_different_division_is_excluded(conn, division_id):
    other_division_id = core.add_division(conn, 2026, "Summer", "Chipmunk")
    core.add_player(conn, "Sidney", "Crosby", current_division_id=other_division_id)
    assert core.list_players_in_division(conn, division_id) == []


def test_soft_deleted_player_is_excluded(conn, division_id):
    player_id = core.add_player(conn, "Sidney", "Crosby", current_division_id=division_id)
    core.soft_delete_player(conn, player_id)
    assert core.list_players_in_division(conn, division_id) == []


def test_unlinked_roster_entry_does_not_pull_in_a_phantom_player(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    core.add_roster_entry(conn, team_id, "87", "Sidney Crosby")  # never linked to a profile
    assert core.list_players_in_division(conn, division_id) == []
