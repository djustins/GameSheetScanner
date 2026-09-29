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


def test_latest_grade_is_by_season_not_entry_order(conn, division_id):
    summer = core.add_division(conn, 2025, "Summer", "Penguin")
    spring = core.add_division(conn, 2025, "Spring", "Penguin")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.add_evaluation(conn, player_id, summer, None, "A")
    core.add_evaluation(conn, player_id, spring, None, "C")  # older season, entered later

    info = core.get_latest_grades_with_source(conn, division_id, [player_id])[player_id]
    assert (info["grade"], info["division_id"]) == ("A", summer)


def test_past_division_keeps_players_graded_there_after_they_move_on(conn, division_id):
    fall = core.add_division(conn, 2026, "Fall", "Penguin")
    player_id = core.add_player(conn, "Sidney", "Crosby", current_division_id=division_id)
    core.add_evaluation(conn, player_id, division_id, None, "A")
    core.update_player(conn, player_id, current_division_id=fall)  # re-registers for Fall

    assert player_id in {p["id"] for p in core.list_players_in_division(conn, division_id)}
    assert player_id in {p["id"] for p in core.list_players_in_division(conn, fall)}


def test_deleted_or_overwritten_evaluations_are_kept_in_the_audit_log(conn, division_id):
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.set_season_grade(conn, player_id, division_id, None, "B")
    core.set_season_grade(conn, player_id, division_id, None, "A")  # overwrite
    core.set_season_grade(conn, player_id, division_id, None, "")  # clear -> delete

    log = core.list_evaluation_audit(conn, player_id)
    assert [(e["action"], e["grade"], e["new_grade"]) for e in log] == [("DELETE", "A", None), ("UPDATE", "B", "A")]
