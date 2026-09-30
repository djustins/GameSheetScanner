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
    core.set_season_grade(conn, player_id, division_id, None, "")  # a blank input never erases
    assert core.get_season_grade(conn, player_id, division_id) == "A"

    evaluation_id = core.list_evaluations(conn, player_id)[0]["id"]
    core.delete_evaluation(conn, evaluation_id)  # the only way to remove one -- and it's logged

    log = core.list_evaluation_audit(conn, player_id)
    assert [(e["action"], e["grade"], e["new_grade"]) for e in log] == [("DELETE", "A", None), ("UPDATE", "B", "A")]


def test_list_division_evaluations_is_only_this_division(conn, division_id):
    other = core.add_division(conn, 2025, "Summer", "Penguin")
    team_id = core.add_team(conn, division_id, "Avalanche")
    sid = core.add_player(conn, "Sidney", "Crosby")
    core.add_roster_entry(conn, team_id, "87", "sidney crosby", player_id=sid)
    core.add_evaluation(conn, sid, division_id, None, "A")
    core.add_evaluation(conn, sid, other, None, "C")

    evals = core.list_division_evaluations(conn, division_id)
    assert [(e["name"], e["grade"], e["team_name"], e["number"]) for e in evals] == [("Sidney Crosby", "A", "Avalanche", "87")]


def test_player_can_be_registered_in_two_age_groups_in_the_same_season(conn, division_id):
    beaver = core.add_division(conn, 2026, "Summer", "Beaver")
    player_id = core.add_player(conn, "Bradley", "Matthews", current_division_id=division_id)
    core.register_player_in_division(conn, player_id, beaver)

    player = core.get_player(conn, player_id)
    assert player["current_division_id"] == division_id  # main division unchanged
    assert player["division_ids"] == [division_id, beaver]
    for d in (division_id, beaver):
        assert [p["id"] for p in core.draft_pool(conn, d)] == [player_id]
        assert [p["id"] for p in core.list_players_in_division(conn, d)] == [player_id]


def test_registering_in_another_season_replaces_old_registrations(conn, division_id):
    beaver = core.add_division(conn, 2026, "Summer", "Beaver")
    fall = core.add_division(conn, 2026, "Fall", "Penguin")
    player_id = core.add_player(conn, "Bradley", "Matthews", current_division_id=division_id)
    core.register_player_in_division(conn, player_id, beaver)

    core.register_player_in_division(conn, player_id, fall)
    player = core.get_player(conn, player_id)
    assert player["division_ids"] == [fall]
    assert player["current_division_id"] == fall
    assert core.draft_pool(conn, beaver) == []


def test_changing_main_division_keeps_the_other_age_group(conn, division_id):
    beaver = core.add_division(conn, 2026, "Summer", "Beaver")
    freshman = core.add_division(conn, 2026, "Summer", "Freshman")
    player_id = core.add_player(conn, "Theodore", "Davis", current_division_id=division_id)
    core.register_player_in_division(conn, player_id, beaver)

    core.update_player(conn, player_id, current_division_id=freshman)  # move Penguin -> Freshman
    player = core.get_player(conn, player_id)
    assert player["current_division_id"] == freshman
    assert sorted(player["division_ids"]) == sorted([freshman, beaver])

    core.unregister_player_from_division(conn, player_id, freshman)
    player = core.get_player(conn, player_id)
    assert player["current_division_id"] == beaver and player["division_ids"] == [beaver]

    core.update_player(conn, player_id, current_division_id=None)
    player = core.get_player(conn, player_id)
    assert player["current_division_id"] is None and player["division_ids"] == []


def test_importing_into_a_second_age_group_adds_rather_than_moves(conn, division_id):
    beaver = core.add_division(conn, 2026, "Summer", "Beaver")
    player_id = core.add_player(conn, "Brooks", "Karpuszka", current_division_id=division_id)
    columns = core.detect_player_import_columns(["Player Name"])
    plan = core.build_player_import_plan(conn, beaver, [{"Player Name": "Brooks Karpuszka"}], columns)
    core.apply_player_import_plan(conn, beaver, plan)

    player = core.get_player(conn, player_id)
    assert player["current_division_id"] == division_id
    assert player["division_ids"] == [division_id, beaver]


def test_remove_all_players_from_division_keeps_their_other_registration(conn, division_id):
    beaver = core.add_division(conn, 2026, "Summer", "Beaver")
    player_id = core.add_player(conn, "Bradley", "Matthews", current_division_id=division_id)
    core.register_player_in_division(conn, player_id, beaver)

    core.remove_all_players_from_division(conn, division_id)
    player = core.get_player(conn, player_id)
    assert player["current_division_id"] == beaver and player["division_ids"] == [beaver]
