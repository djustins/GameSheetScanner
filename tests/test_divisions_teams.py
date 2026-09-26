import game_sheet_core as core


def test_add_division_is_idempotent(conn):
    id1 = core.add_division(conn, 2026, "Summer", "Penguin")
    id2 = core.add_division(conn, 2026, "Summer", "Penguin")
    assert id1 == id2
    assert len(core.list_divisions(conn)) == 1


def test_add_division_normalizes_typo_d_age_group(conn):
    # match_age_group fuzzy-matches close-enough typos to a known name.
    div_id = core.add_division(conn, 2026, "Summer", "Pegnuin")
    divisions = core.list_divisions(conn)
    assert divisions[0]["age_group"] == "Penguin"
    assert divisions[0]["id"] == div_id


def test_soft_delete_and_restore_division(conn, division_id):
    core.soft_delete_division(conn, division_id)
    assert core.list_divisions(conn) == []
    assert any(d["id"] == division_id for d in core.list_deleted_divisions(conn))

    core.restore_division(conn, division_id)
    assert any(d["id"] == division_id for d in core.list_divisions(conn))
    assert core.list_deleted_divisions(conn) == []


def test_add_team_is_idempotent_per_division(conn, division_id):
    id1 = core.add_team(conn, division_id, "Avalanche")
    id2 = core.add_team(conn, division_id, "Avalanche")
    assert id1 == id2
    assert len(core.list_teams(conn, division_id)) == 1


def test_same_team_name_in_different_divisions_is_separate(conn, division_id):
    other_division_id = core.add_division(conn, 2026, "Summer", "Chipmunk")
    team1 = core.add_team(conn, division_id, "Avalanche")
    team2 = core.add_team(conn, other_division_id, "Avalanche")
    assert team1 != team2


def test_find_previous_division_picks_the_immediately_prior_season(conn):
    spring = core.add_division(conn, 2026, "Spring", "Penguin")
    summer = core.add_division(conn, 2026, "Summer", "Penguin")
    fall = core.add_division(conn, 2026, "Fall", "Penguin")

    assert core.find_previous_division(conn, summer)["id"] == spring
    assert core.find_previous_division(conn, fall)["id"] == summer


def test_find_previous_division_compares_year_before_season(conn):
    last_year_winter = core.add_division(conn, 2025, "Winter", "Penguin")
    this_year_spring = core.add_division(conn, 2026, "Spring", "Penguin")
    assert core.find_previous_division(conn, this_year_spring)["id"] == last_year_winter


def test_find_previous_division_ignores_other_age_groups(conn):
    core.add_division(conn, 2026, "Spring", "Chipmunk")
    summer_penguin = core.add_division(conn, 2026, "Summer", "Penguin")
    assert core.find_previous_division(conn, summer_penguin) is None


def test_find_previous_division_returns_none_for_the_earliest_division(conn, division_id):
    assert core.find_previous_division(conn, division_id) is None


def test_find_previous_division_ignores_deleted_divisions(conn):
    spring = core.add_division(conn, 2026, "Spring", "Penguin")
    summer = core.add_division(conn, 2026, "Summer", "Penguin")
    core.soft_delete_division(conn, spring)
    assert core.find_previous_division(conn, summer) is None


def _roster_player(conn, division_id, team_name, player_id):
    team_id = core.add_team(conn, division_id, team_name)
    core.add_roster_entry(conn, team_id, "1", "Test Player", player_id=player_id)


def test_player_experience_notes_none_for_first_division(conn):
    current = core.add_division(conn, 2026, "Summer", "Penguin")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    _roster_player(conn, current, "Avalanche", player_id)
    assert core.player_experience_notes(conn, current, [player_id]) == {}


def test_player_experience_notes_none_when_played_same_age_group_last_season(conn):
    last_season = core.add_division(conn, 2026, "Spring", "Penguin")
    current = core.add_division(conn, 2026, "Summer", "Penguin")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    _roster_player(conn, last_season, "Avalanche", player_id)
    _roster_player(conn, current, "Wild", player_id)
    assert core.player_experience_notes(conn, current, [player_id]) == {}


def test_player_experience_notes_moved_up_from_lower_age_group(conn):
    lower_last_season = core.add_division(conn, 2026, "Spring", "Chipmunk")
    current = core.add_division(conn, 2026, "Summer", "Penguin")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    _roster_player(conn, lower_last_season, "Avalanche", player_id)
    _roster_player(conn, current, "Wild", player_id)
    assert core.player_experience_notes(conn, current, [player_id]) == {player_id: "Moved Up"}


def test_player_experience_notes_played_before_for_a_single_other_division(conn):
    unrelated = core.add_division(conn, 2024, "Fall", "Beaver")
    current = core.add_division(conn, 2026, "Summer", "Penguin")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    _roster_player(conn, unrelated, "Avalanche", player_id)
    _roster_player(conn, current, "Wild", player_id)
    assert core.player_experience_notes(conn, current, [player_id]) == {player_id: "Played Before"}


def test_player_experience_notes_has_experience_for_multiple_other_divisions(conn):
    older1 = core.add_division(conn, 2023, "Fall", "Chipmunk")
    older2 = core.add_division(conn, 2024, "Fall", "Beaver")
    current = core.add_division(conn, 2026, "Summer", "Penguin")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    _roster_player(conn, older1, "Avalanche", player_id)
    _roster_player(conn, older2, "Wild", player_id)
    _roster_player(conn, current, "Blackhawks", player_id)
    assert core.player_experience_notes(conn, current, [player_id]) == {player_id: "Has Experience"}


def test_player_experience_notes_omits_players_with_no_history(conn, division_id):
    player_id = core.add_player(conn, "Sidney", "Crosby", current_division_id=division_id)
    assert core.player_experience_notes(conn, division_id, [player_id]) == {}


def test_remove_all_players_from_division_clears_this_divisions_links(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    team2_id = core.add_team(conn, division_id, "Wild")
    player_id = core.add_player(conn, "Sidney", "Crosby", current_division_id=division_id)
    core.add_roster_entry(conn, team_id, "9", "Sidney Crosby", player_id=player_id)
    core.add_evaluation(conn, player_id, division_id, team_id, "A")
    core.set_position(conn, player_id, division_id, team_id, "Forward")
    core.move_player_to_team(conn, player_id, division_id, team2_id, note="Balancing rosters")

    removed = core.remove_all_players_from_division(conn, division_id)
    assert removed == 1

    # The player record itself is untouched.
    player = core.get_player(conn, player_id)
    assert player is not None
    assert player["current_division_id"] is None

    assert core.list_roster(conn, team2_id) == []
    assert core.list_evaluations(conn, player_id) == []
    assert core.get_positions_for_team(conn, division_id, team_id) == {}
    assert core.list_player_move_notes(conn, player_id) == []


def test_remove_all_players_from_division_keeps_the_player_in_other_divisions(conn, division_id):
    other_division_id = core.add_division(conn, 2026, "Fall", "Chipmunk")
    team_id = core.add_team(conn, division_id, "Avalanche")
    other_team_id = core.add_team(conn, other_division_id, "Blackhawks")
    player_id = core.add_player(conn, "Sidney", "Crosby", current_division_id=division_id)
    core.add_roster_entry(conn, team_id, "9", "Sidney Crosby", player_id=player_id)
    core.add_roster_entry(conn, other_team_id, "9", "Sidney Crosby", player_id=player_id)
    core.add_evaluation(conn, player_id, division_id, None, "A")
    core.add_evaluation(conn, player_id, other_division_id, None, "B")

    core.remove_all_players_from_division(conn, division_id)

    assert core.get_player(conn, player_id) is not None
    assert core.list_roster(conn, team_id) == []
    assert [r["player_id"] for r in core.list_roster(conn, other_team_id)] == [player_id]
    assert [e["grade"] for e in core.list_evaluations(conn, player_id)] == ["B"]


def test_remove_all_players_from_division_returns_zero_when_nothing_to_remove(conn, division_id):
    assert core.remove_all_players_from_division(conn, division_id) == 0


def test_update_team_rejects_duplicate_name_in_same_division(conn, division_id):
    core.add_team(conn, division_id, "Avalanche")
    wild_id = core.add_team(conn, division_id, "Wild")
    try:
        core.update_team(conn, wild_id, name="Avalanche")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "already exists" in str(e)


def test_update_team_color(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    core.update_team(conn, team_id, color="#FF0000")
    team = next(t for t in core.list_teams(conn, division_id) if t["id"] == team_id)
    assert team["color"] == "#FF0000"


def test_soft_delete_and_restore_team(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    core.soft_delete_team(conn, team_id)
    assert core.list_teams(conn, division_id) == []
    assert any(t["id"] == team_id for t in core.list_teams(conn, division_id, include_deleted=True))

    core.restore_team(conn, team_id)
    assert any(t["id"] == team_id for t in core.list_teams(conn, division_id))
