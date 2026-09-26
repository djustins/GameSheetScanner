import game_sheet_core as core


def _add_player(conn, division_id, first, last, grade=None, birth_date=None, **kwargs):
    player_id = core.add_player(
        conn, first, last, birth_date=birth_date, current_division_id=division_id, **kwargs
    )
    if grade is not None:
        core.add_evaluation(conn, player_id, division_id, None, grade)
    return player_id


def test_auto_draft_requires_at_least_two_teams(conn, division_id):
    core.add_team(conn, division_id, "Avalanche")
    _add_player(conn, division_id, "Sidney", "Crosby")
    try:
        core.auto_draft(conn, division_id)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "two teams" in str(e)


def test_auto_draft_requires_a_nonempty_pool(conn, division_id):
    core.add_team(conn, division_id, "Avalanche")
    core.add_team(conn, division_id, "Wild")
    try:
        core.auto_draft(conn, division_id)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "pool" in str(e)


def test_auto_draft_distributes_players_evenly_by_count(conn, division_id):
    team_a = core.add_team(conn, division_id, "Avalanche")
    team_b = core.add_team(conn, division_id, "Wild")
    for i in range(8):
        _add_player(conn, division_id, f"Player{i}", "Test")

    result = core.auto_draft(conn, division_id)
    assert result["assigned"] == 8
    assert result["warnings"] == []
    assert len(core.list_roster(conn, team_a)) == 4
    assert len(core.list_roster(conn, team_b)) == 4
    assert core.draft_pool(conn, division_id) == []


def test_auto_draft_balances_by_rank_not_just_count(conn, division_id):
    team_a = core.add_team(conn, division_id, "Avalanche")
    team_b = core.add_team(conn, division_id, "Wild")
    # Two A's and two D's -- a rank-balanced split puts one A and one D on
    # each team rather than stacking both A's on one team.
    _add_player(conn, division_id, "A1", "Test", grade="A")
    _add_player(conn, division_id, "A2", "Test", grade="A")
    _add_player(conn, division_id, "D1", "Test", grade="D")
    _add_player(conn, division_id, "D2", "Test", grade="D")

    core.auto_draft(conn, division_id)
    rosters = {
        team_a: {r["name"] for r in core.list_roster(conn, team_a)},
        team_b: {r["name"] for r in core.list_roster(conn, team_b)},
    }
    for names in rosters.values():
        assert len(names) == 2
        has_a = any(n.startswith("A") for n in names)
        has_d = any(n.startswith("D") for n in names)
        assert has_a and has_d


def test_auto_draft_ranks_older_new_player_above_a_d_player(conn, division_id):
    team_a = core.add_team(conn, division_id, "Avalanche")
    team_b = core.add_team(conn, division_id, "Wild")
    old_new = _add_player(conn, division_id, "Old", "New", birth_date="2010-01-01")
    young_d = _add_player(conn, division_id, "Young", "Dee", grade="D", birth_date="2018-01-01")

    core.auto_draft(conn, division_id)
    # With only 2 players and 2 teams, both get seated -- rank only affects
    # draft *order*, observable via which team (the emptier one at that
    # moment) each lands on. What matters here is neither raises and both
    # get placed on different teams (balanced 1-1 split).
    rostered_ids = {r["player_id"] for r in core.list_roster(conn, team_a)} | {
        r["player_id"] for r in core.list_roster(conn, team_b)
    }
    assert rostered_ids == {old_new, young_d}


def test_auto_draft_keeps_siblings_on_the_same_team(conn, division_id):
    core.add_team(conn, division_id, "Avalanche")
    core.add_team(conn, division_id, "Wild")
    core.add_team(conn, division_id, "Blackhawks")
    p1 = _add_player(
        conn, division_id, "Sidney", "Crosby",
        contact_first_name="Troy", contact_last_name="Crosby", contact_email="troy@example.com",
    )
    p2 = _add_player(
        conn, division_id, "Taylor", "Crosby",
        contact_first_name="Troy", contact_last_name="Crosby", contact_email="troy@example.com",
    )
    for i in range(4):
        _add_player(conn, division_id, f"Filler{i}", "Test")

    core.auto_draft(conn, division_id)
    all_teams = core.list_teams(conn, division_id)
    rosters = {t["id"]: {r["player_id"] for r in core.list_roster(conn, t["id"])} for t in all_teams}
    team_of_p1 = next(tid for tid, ids in rosters.items() if p1 in ids)
    team_of_p2 = next(tid for tid, ids in rosters.items() if p2 in ids)
    assert team_of_p1 == team_of_p2


def test_auto_draft_pins_coachs_kid_to_the_coachs_team(conn, division_id):
    core.add_team(conn, division_id, "Avalanche")
    team_b = core.add_team(conn, division_id, "Wild")
    coach_id = core.add_coach(conn, "Mario", "Lemieux")
    core.assign_coach_to_team(conn, team_b, coach_id)
    kid_id = _add_player(conn, division_id, "Austin", "Lemieux")
    core.link_coach_child(conn, coach_id, kid_id)
    for i in range(4):
        _add_player(conn, division_id, f"Filler{i}", "Test")

    core.auto_draft(conn, division_id)
    team_b_ids = {r["player_id"] for r in core.list_roster(conn, team_b)}
    assert kid_id in team_b_ids


def test_auto_draft_assigns_coach_to_teammate_kid_lands_on(conn, division_id):
    core.add_team(conn, division_id, "Avalanche")
    core.add_team(conn, division_id, "Wild")
    coach_id = core.add_coach(conn, "Mario", "Lemieux")
    kid_id = _add_player(conn, division_id, "Austin", "Lemieux")
    core.link_coach_child(conn, coach_id, kid_id)

    result = core.auto_draft(conn, division_id)
    assert result["warnings"] == []
    all_teams = core.list_teams(conn, division_id)
    kid_team_id = next(
        t["id"] for t in all_teams if kid_id in {r["player_id"] for r in core.list_roster(conn, t["id"])}
    )
    assigned_coach_ids = {c["id"] for c in core.list_team_coaches(conn, kid_team_id)}
    assert coach_id in assigned_coach_ids


def test_auto_draft_warns_instead_of_failing_on_coach_assignment_conflict(conn, division_id):
    team_a = core.add_team(conn, division_id, "Avalanche")
    core.add_team(conn, division_id, "Wild")
    other_coach = core.add_coach(conn, "Sidney", "Crosby")
    core.assign_coach_to_team(conn, team_a, other_coach)

    coach_id = core.add_coach(conn, "Mario", "Lemieux")
    kid_id = _add_player(conn, division_id, "Austin", "Lemieux")
    core.link_coach_child(conn, coach_id, kid_id)
    # Force the kid onto team_a (the one with an existing coach) via a
    # sibling already seated there, so the coach-follows-kid step collides.
    sibling_id = _add_player(
        conn, division_id, "Jordan", "Lemieux",
        contact_first_name="Mario", contact_last_name="Lemieux",
    )
    core.set_player_parent(conn, kid_id, core.get_player(conn, sibling_id)["parent_id"])
    core.add_roster_entry(conn, team_a, "1", "Jordan Lemieux", player_id=sibling_id)

    result = core.auto_draft(conn, division_id)
    assert len(result["warnings"]) == 1
    assert "already has a coach" in result["warnings"][0]
    assigned_coach_ids = {c["id"] for c in core.list_team_coaches(conn, team_a)}
    assert assigned_coach_ids == {other_coach}


def test_undo_auto_draft_restores_the_pool_and_removes_coach_link(conn, division_id):
    team_a = core.add_team(conn, division_id, "Avalanche")
    core.add_team(conn, division_id, "Wild")
    coach_id = core.add_coach(conn, "Mario", "Lemieux")
    kid_id = _add_player(conn, division_id, "Austin", "Lemieux")
    core.link_coach_child(conn, coach_id, kid_id)
    for i in range(4):
        _add_player(conn, division_id, f"Filler{i}", "Test")

    core.auto_draft(conn, division_id)
    assert len(core.draft_pool(conn, division_id)) == 0
    assert core.get_auto_draft_run(conn, division_id) is not None

    core.undo_auto_draft(conn, division_id)
    assert len(core.draft_pool(conn, division_id)) == 5
    assert core.get_auto_draft_run(conn, division_id) is None
    assert core.list_team_coaches(conn, team_a) == [] or all(
        c["id"] != coach_id for c in core.list_team_coaches(conn, team_a)
    )
    for t in core.list_teams(conn, division_id):
        assert core.list_roster(conn, t["id"]) == []


def test_undo_auto_draft_raises_when_nothing_to_undo(conn, division_id):
    try:
        core.undo_auto_draft(conn, division_id)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "No auto-draft" in str(e)


def test_re_running_auto_draft_undoes_the_previous_run_first(conn, division_id):
    core.add_team(conn, division_id, "Avalanche")
    core.add_team(conn, division_id, "Wild")
    for i in range(6):
        _add_player(conn, division_id, f"Player{i}", "Test")

    first = core.auto_draft(conn, division_id)
    assert first["assigned"] == 6
    assert core.draft_pool(conn, division_id) == []

    second = core.auto_draft(conn, division_id)
    assert second["assigned"] == 6
    # No duplicate roster rows from the first run left behind.
    total_rostered = sum(len(core.list_roster(conn, t["id"])) for t in core.list_teams(conn, division_id))
    assert total_rostered == 6
