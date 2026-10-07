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
