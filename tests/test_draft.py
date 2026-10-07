import game_sheet_core as core


def test_pool_and_picks_carry_grade_and_position_for_the_draft_board(conn, division_id):
    avs = core.add_team(conn, division_id, "Avalanche")
    wild = core.add_team(conn, division_id, "Wild")
    graded = core.add_player(conn, "Alpha", "Test", current_division_id=division_id)
    core.add_evaluation(conn, graded, division_id, None, "A")
    ungraded = core.add_player(conn, "Bravo", "Test", current_division_id=division_id)

    pool = {p["id"]: p for p in core.draft_pool(conn, division_id)}
    assert pool[graded]["grade"] == "A"
    assert pool[ungraded]["grade"] is None

    draft_id = core.start_draft(conn, division_id, [avs, wild])
    core.submit_draft_pick(conn, draft_id, graded)
    core.submit_draft_pick(conn, draft_id, ungraded)

    picks = core.list_draft_picks(conn, draft_id)
    assert [(p["pick_number"], p["team_id"], p["player_id"], p["grade"]) for p in picks] == [
        (1, avs, graded, "A"), (2, wild, ungraded, None),
    ]
    assert all("position" in p for p in picks)
    assert core.draft_pool(conn, division_id) == []


def _division_with_players(conn, division_id, count=5):
    teams = [core.add_team(conn, division_id, name) for name in ("Avalanche", "Wild")]
    players = []
    for i, grade in enumerate(["C", "A", None, "B", "A*"][:count]):
        player_id = core.add_player(conn, f"P{i}", "Test", current_division_id=division_id)
        if grade:
            core.add_evaluation(conn, player_id, division_id, None, grade)
        players.append(player_id)
    return teams, players


def test_mock_draft_changes_no_roster_and_runs_beside_the_real_one(conn, division_id):
    (avs, wild), players = _division_with_players(conn, division_id)
    # Already on a real team: still available to a mock, which ignores rosters.
    core.add_roster_entry(conn, avs, "9", "P0 Test", player_id=players[0])

    mock_id = core.start_draft(conn, division_id, [avs, wild], mock=True)
    real_id = core.start_draft(conn, division_id, [wild, avs])
    assert core.get_draft(conn, division_id, mock=True)["id"] == mock_id
    assert core.get_draft(conn, division_id)["id"] == real_id
    mock = core.get_draft_by_id(conn, mock_id)
    assert len(core.draft_pool_for(conn, mock)) == 5
    assert len(core.draft_pool(conn, division_id)) == 4

    assert core.submit_draft_pick(conn, mock_id, players[0]) is None
    # Auto-pick takes the best grade left: A, then A*, then B.
    assert [core.auto_pick(conn, mock_id) for _ in range(3)] == [players[1], players[4], players[3]]
    assert len(core.list_draft_picks(conn, mock_id)) == 4
    assert len(core.list_roster(conn, avs)) == 1 and core.list_roster(conn, wild) == []
    assert len(core.draft_pool(conn, division_id)) == 4  # the real draft's pool is untouched

    core.undo_last_pick(conn, mock_id)
    assert len(core.list_draft_picks(conn, mock_id)) == 3
    core.delete_draft(conn, mock_id)
    assert core.get_draft(conn, division_id, mock=True) is None
    assert core.get_draft(conn, division_id)["id"] == real_id

    try:
        core.auto_pick(conn, real_id)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "mock" in str(e)


def test_draft_settings_linear_order_rounds_and_changes_mid_draft(conn, division_id):
    (avs, wild), players = _division_with_players(conn, division_id)
    draft_id = core.start_draft(
        conn, division_id, [avs, wild], mock=True, settings={"order_type": "linear", "rounds": 1, "pick_seconds": 60}
    )
    assert core.get_draft_by_id(conn, draft_id)["settings"] == {
        "order_type": "linear", "rounds": 1, "pick_seconds": 60, "who_picks": "coaches",
    }
    core.submit_draft_pick(conn, draft_id, players[0])
    core.submit_draft_pick(conn, draft_id, players[1])
    # One round of two teams: done, with players still in the pool.
    assert core.get_draft_by_id(conn, draft_id)["status"] == "completed"

    # Raising the rounds reopens it; linear means Avalanche leads off round 2 as well.
    core.update_draft_settings(conn, draft_id, {"rounds": 2, "pick_seconds": None})
    draft = core.get_draft_by_id(conn, draft_id)
    assert draft["status"] == "in_progress" and draft["settings"]["pick_seconds"] is None
    assert core.current_pick_team_id(conn, draft_id) == avs

    for bad in ({"order_type": "snake"}, {"rounds": 0.5}, {"who_picks": "anyone"}):
        try:
            core.update_draft_settings(conn, draft_id, bad)
            assert False, f"expected ValueError for {bad}"
        except ValueError:
            pass
