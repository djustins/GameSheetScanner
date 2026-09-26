import game_sheet_core as core


def test_add_and_list_player(conn):
    player_id = core.add_player(conn, "Sidney", "Crosby", nickname="Sid")
    players = core.list_players(conn)
    assert len(players) == 1
    assert players[0]["id"] == player_id
    assert "Crosby" in players[0]["name"]


def test_soft_delete_and_restore_player(conn):
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.soft_delete_player(conn, player_id)
    assert core.list_players(conn) == []
    assert any(p["id"] == player_id for p in core.list_players(conn, include_deleted=True))

    core.restore_player(conn, player_id)
    assert any(p["id"] == player_id for p in core.list_players(conn))


def test_update_player_only_touches_given_fields(conn):
    player_id = core.add_player(conn, "Sidney", "Crosby", contact_phone="412-555-0100")
    core.update_player(conn, player_id, first_name="Sid")
    player = next(p for p in core.list_players(conn) if p["id"] == player_id)
    assert "Sid" in player["name"]


def test_add_and_list_coach(conn):
    coach_id = core.add_coach(conn, "Mario", "Lemieux")
    coaches = core.list_coaches(conn)
    assert len(coaches) == 1
    assert coaches[0]["id"] == coach_id


def test_soft_delete_and_restore_coach(conn):
    coach_id = core.add_coach(conn, "Mario", "Lemieux")
    core.soft_delete_coach(conn, coach_id)
    assert core.list_coaches(conn) == []
    assert any(c["id"] == coach_id for c in core.list_coaches(conn, include_deleted=True))

    core.restore_coach(conn, coach_id)
    assert any(c["id"] == coach_id for c in core.list_coaches(conn))


def test_assign_coach_to_team(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    coach_id = core.add_coach(conn, "Mario", "Lemieux")
    core.assign_coach_to_team(conn, team_id, coach_id)
    coaches = core.list_team_coaches(conn, team_id)
    assert len(coaches) == 1
    assert coaches[0]["id"] == coach_id


def test_coach_cannot_coach_two_teams_in_same_division(conn, division_id):
    team1 = core.add_team(conn, division_id, "Avalanche")
    team2 = core.add_team(conn, division_id, "Wild")
    coach_id = core.add_coach(conn, "Mario", "Lemieux")
    core.assign_coach_to_team(conn, team1, coach_id)
    try:
        core.assign_coach_to_team(conn, team2, coach_id)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "already coaches" in str(e)


def test_coach_can_coach_teams_in_different_divisions(conn, division_id):
    other_division_id = core.add_division(conn, 2026, "Summer", "Chipmunk")
    team1 = core.add_team(conn, division_id, "Avalanche")
    team2 = core.add_team(conn, other_division_id, "Avalanche")
    coach_id = core.add_coach(conn, "Mario", "Lemieux")
    core.assign_coach_to_team(conn, team1, coach_id)
    core.assign_coach_to_team(conn, team2, coach_id)  # no error -- different division
    assert len(core.list_team_coaches(conn, team1)) == 1
    assert len(core.list_team_coaches(conn, team2)) == 1


def test_remove_coach_from_team(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    coach_id = core.add_coach(conn, "Mario", "Lemieux")
    core.assign_coach_to_team(conn, team_id, coach_id)
    core.remove_coach_from_team(conn, team_id, coach_id)
    assert core.list_team_coaches(conn, team_id) == []


def test_find_coach_children_in_division_prefers_explicit_link(conn, division_id):
    coach_id = core.add_coach(conn, "Robert", "Dobson")
    player_id = core.add_player(conn, "Austin", "Dobson", current_division_id=division_id)
    core.link_coach_child(conn, coach_id, player_id)
    result = core.find_coach_children_in_division(conn, coach_id, division_id)
    assert [p["id"] for p in result] == [player_id]


def test_find_coach_children_in_division_falls_back_to_name_match_and_links_it(conn, division_id):
    coach_id = core.add_coach(conn, "Robert", "Dobson")
    player_id = core.add_player(
        conn, "Austin", "Dobson", current_division_id=division_id,
        contact_first_name="Robert", contact_last_name="Dobson",
    )
    result = core.find_coach_children_in_division(conn, coach_id, division_id)
    assert [p["id"] for p in result] == [player_id]
    # The match becomes an explicit link -- confirmed by list_coach_children
    # (which never does name-matching itself) now finding it too.
    assert [c["id"] for c in core.list_coach_children(conn, coach_id)] == [player_id]


def test_find_coach_children_in_division_name_match_is_case_insensitive(conn, division_id):
    coach_id = core.add_coach(conn, "Robert", "Dobson")
    player_id = core.add_player(
        conn, "Austin", "Dobson", current_division_id=division_id,
        contact_first_name="robert", contact_last_name="DOBSON",
    )
    result = core.find_coach_children_in_division(conn, coach_id, division_id)
    assert [p["id"] for p in result] == [player_id]


def test_find_coach_children_in_division_returns_empty_when_no_match(conn, division_id):
    coach_id = core.add_coach(conn, "Robert", "Dobson")
    core.add_player(
        conn, "Austin", "Someone", current_division_id=division_id,
        contact_first_name="Jane", contact_last_name="Someone",
    )
    assert core.find_coach_children_in_division(conn, coach_id, division_id) == []


def test_find_coach_children_in_division_ignores_matches_in_other_divisions(conn, division_id):
    other_division_id = core.add_division(conn, 2026, "Summer", "Chipmunk")
    coach_id = core.add_coach(conn, "Robert", "Dobson")
    core.add_player(
        conn, "Austin", "Dobson", current_division_id=other_division_id,
        contact_first_name="Robert", contact_last_name="Dobson",
    )
    assert core.find_coach_children_in_division(conn, coach_id, division_id) == []


def test_get_latest_grades_prefers_this_divisions_own_evaluation(conn, division_id):
    other_division_id = core.add_division(conn, 2026, "Spring", "Chipmunk")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.add_evaluation(conn, player_id, other_division_id, None, "C")
    core.add_evaluation(conn, player_id, division_id, None, "A")
    assert core.get_latest_grades(conn, division_id, [player_id]) == {player_id: "A"}


def test_get_latest_grades_falls_back_to_latest_from_another_division(conn, division_id):
    older = core.add_division(conn, 2024, "Fall", "Chipmunk")
    newer = core.add_division(conn, 2025, "Fall", "Beaver")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.add_evaluation(conn, player_id, older, None, "C")
    core.add_evaluation(conn, player_id, newer, None, "B")
    # division_id itself has no evaluation -- falls back to the most
    # recent evaluation from anywhere, not just the oldest/any one.
    assert core.get_latest_grades(conn, division_id, [player_id]) == {player_id: "B"}


def test_get_latest_grades_omits_players_with_no_evaluation_anywhere(conn, division_id):
    player_id = core.add_player(conn, "Sidney", "Crosby")
    assert core.get_latest_grades(conn, division_id, [player_id]) == {}
