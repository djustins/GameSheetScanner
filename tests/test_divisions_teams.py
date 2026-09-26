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
