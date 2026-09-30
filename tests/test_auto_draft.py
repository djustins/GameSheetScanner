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


def _teams_by_name(conn, *team_ids):
    return {t: {r["name"] for r in core.list_roster(conn, t)} for t in team_ids}


def test_auto_draft_honors_play_with_request_when_balanced(conn, division_id):
    team_a = core.add_team(conn, division_id, "Avalanche")
    team_b = core.add_team(conn, division_id, "Wild")
    a = _add_player(conn, division_id, "Alpha", "Test", grade="A")
    _add_player(conn, division_id, "Bravo", "Test", grade="B")
    c = _add_player(conn, division_id, "Charlie", "Test", grade="C")
    _add_player(conn, division_id, "Delta", "Test", grade="D")
    # Without the request, Charlie would join the weaker-so-far team (Bravo's).
    core.add_player_request(conn, c, a)

    result = core.auto_draft(conn, division_id)
    assert result["warnings"] == []
    assert _teams_by_name(conn, team_a, team_b) == {
        team_a: {"Alpha Test", "Charlie Test"},
        team_b: {"Bravo Test", "Delta Test"},
    }


def test_auto_draft_skips_play_with_request_that_would_unbalance_teams(conn, division_id):
    team_a = core.add_team(conn, division_id, "Avalanche")
    team_b = core.add_team(conn, division_id, "Wild")
    _add_player(conn, division_id, "Alpha", "Test", grade="A")
    b = _add_player(conn, division_id, "Bravo", "Test", grade="B")
    _add_player(conn, division_id, "Charlie", "Test", grade="C")
    d = _add_player(conn, division_id, "Delta", "Test", grade="D")
    # Bravo's team already has Charlie by the time Delta is placed, so
    # honoring this would leave it 3 vs 1.
    core.add_player_request(conn, d, b)

    result = core.auto_draft(conn, division_id)
    assert len(core.list_roster(conn, team_a)) == 2
    assert len(core.list_roster(conn, team_b)) == 2
    assert any("Delta Test" in w and "Bravo Test" in w for w in result["warnings"])


def test_auto_draft_skips_play_with_request_onto_a_much_stronger_team(conn, division_id):
    team_a = core.add_team(conn, division_id, "Avalanche")
    team_b = core.add_team(conn, division_id, "Wild")
    # Wild already has a weak player seated, so after Alpha1 goes to
    # Avalanche both teams have one player -- equal head count, but
    # Avalanche is an A ahead of a D.
    seated = _add_player(conn, division_id, "Seated", "Dee", grade="D")
    core.add_roster_entry(conn, team_b, "1", "Seated Dee", player_id=seated)
    a1 = _add_player(conn, division_id, "Alpha1", "Test", grade="A")
    a2 = _add_player(conn, division_id, "Alpha2", "Test", grade="A")
    core.add_player_request(conn, a2, a1)

    result = core.auto_draft(conn, division_id)
    assert {r["player_id"] for r in core.list_roster(conn, team_a)} == {a1}
    assert {r["player_id"] for r in core.list_roster(conn, team_b)} == {seated, a2}
    assert any("Alpha1 Test" in w and "Alpha2 Test" in w for w in result["warnings"])


def test_auto_draft_always_honors_a_hard_request_even_if_unbalanced(conn, division_id):
    team_a = core.add_team(conn, division_id, "Avalanche")
    team_b = core.add_team(conn, division_id, "Wild")
    a1 = _add_player(conn, division_id, "Alpha1", "Test", grade="A")
    a2 = _add_player(conn, division_id, "Alpha2", "Test", grade="A")
    _add_player(conn, division_id, "Delta", "Test", grade="D")
    core.add_player_request(conn, a2, a1, hard=True)

    result = core.auto_draft(conn, division_id)
    assert result["warnings"] == []
    rosters = {t: {r["player_id"] for r in core.list_roster(conn, t)} for t in (team_a, team_b)}
    assert any({a1, a2} <= ids for ids in rosters.values())


def test_set_player_request_hard_toggles_and_defaults_soft(conn, division_id):
    a = _add_player(conn, division_id, "Alpha", "Test")
    b = _add_player(conn, division_id, "Bravo", "Test")
    request_id = core.add_player_request(conn, a, b)
    assert core.list_player_requests(conn, a)[0]["hard"] is False
    core.set_player_request_hard(conn, request_id, True)
    assert core.list_player_requests(conn, a)[0]["hard"] is True


def _first_pick_team(conn, team_a, team_b, player_id):
    """With 2 teams and an empty draft, the first player placed lands on
    the lower-id team (team_a) -- a way to observe draft order."""
    return player_id in {r["player_id"] for r in core.list_roster(conn, team_a)}


def test_auto_draft_ranks_older_ahead_of_younger_within_a_grade(conn, division_id):
    team_a = core.add_team(conn, division_id, "Avalanche")
    team_b = core.add_team(conn, division_id, "Wild")
    young = _add_player(conn, division_id, "Young", "B", grade="B", birth_date="2017-12-01")
    old = _add_player(conn, division_id, "Old", "B", grade="B", birth_date="2017-02-01")  # same year, older

    core.auto_draft(conn, division_id)
    assert _first_pick_team(conn, team_a, team_b, old)
    assert not _first_pick_team(conn, team_a, team_b, young)


def test_auto_draft_grades_a_player_moving_up_age_groups_as_new(conn, division_id):
    team_a = core.add_team(conn, division_id, "Avalanche")
    team_b = core.add_team(conn, division_id, "Wild")
    younger_division = core.add_division(conn, 2025, "Fall", "Chipmunk")  # this division is Penguin
    moving_up = _add_player(conn, division_id, "Moving", "Up", birth_date="2016-01-01")
    core.add_evaluation(conn, moving_up, younger_division, None, "A")
    d_player = _add_player(conn, division_id, "Dee", "Player", grade="D", birth_date="2015-01-01")

    core.auto_draft(conn, division_id)
    # Ranked as New (tied with D), so the older D player goes first.
    assert _first_pick_team(conn, team_a, team_b, d_player)
    assert not _first_pick_team(conn, team_a, team_b, moving_up)
