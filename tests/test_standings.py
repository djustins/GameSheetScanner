import game_sheet_core as core


def _game(home, away, home_score, away_score, date, shootout=None, **overrides):
    data = {
        "game_date": date, "division": "Penguin",
        "home_team": home, "home_color": "Red", "home_final_score": home_score,
        "away_team": away, "away_color": "Blue", "away_final_score": away_score,
        "goals": [], "penalties": [], "shootout_attempts": shootout or [],
    }
    data.update(overrides)
    return data


def _row_for(table, team):
    return next(r for r in table if r["Team"] == team)


def test_regulation_win_and_loss_points(conn, division_id):
    core.insert_game(conn, _game("Avalanche", "Wild", 5, 2, "2026-07-14"), "g1.pdf", division_id)
    table = core.standings_table(conn, division_id)
    ava, wild = _row_for(table, "Avalanche"), _row_for(table, "Wild")
    assert ava["W"] == 1 and ava["L"] == 0 and ava["PTS"] == 3
    assert wild["W"] == 0 and wild["L"] == 1 and wild["PTS"] == 0
    assert ava["GF"] == 5 and ava["GA"] == 2 and ava["DIFF"] == 3


def test_shootout_win_and_loss_points(conn, division_id):
    # 4-4 in regulation, home wins the shootout -> stored as 5-4.
    core.insert_game(
        conn,
        _game("Avalanche", "Wild", 5, 4, "2026-07-14",
              shootout=[{"side": "home", "round": 1, "player_number": "9", "scored": True},
                        {"side": "away", "round": 1, "player_number": "4", "scored": False}]),
        "g1.pdf", division_id,
    )
    table = core.standings_table(conn, division_id)
    ava, wild = _row_for(table, "Avalanche"), _row_for(table, "Wild")
    assert ava["OTW"] == 1 and ava["PTS"] == 2, "OT/shootout win is worth 2 points, not 3"
    assert wild["OTL"] == 1 and wild["PTS"] == 1, "OT/shootout loss is worth 1 point, not 0"


def test_standings_ranks_by_points_across_multiple_games(conn, division_id):
    core.insert_game(conn, _game("Avalanche", "Wild", 5, 2, "2026-07-14"), "g1.pdf", division_id)
    core.insert_game(conn, _game("Avalanche", "Blues", 4, 3, "2026-07-21"), "g2.pdf", division_id)
    core.insert_game(conn, _game("Wild", "Blues", 6, 1, "2026-07-28"), "g3.pdf", division_id)

    table = core.standings_table(conn, division_id)
    assert [r["Team"] for r in table[:1]] == ["Avalanche"]  # 2 regulation wins = 6 pts, the most
    ava = _row_for(table, "Avalanche")
    assert ava["GP"] == 2 and ava["W"] == 2 and ava["PTS"] == 6


def test_standings_empty_division_has_no_rows(conn, division_id):
    assert core.standings_table(conn, division_id) == []


def test_standings_scoped_to_one_division(conn, division_id):
    other_division_id = core.add_division(conn, 2026, "Summer", "Chipmunk")
    core.insert_game(conn, _game("Avalanche", "Wild", 5, 2, "2026-07-14"), "g1.pdf", division_id)
    # insert_game files a game by its OWN division text, not just
    # working_division_id (see resolve_division_id) -- "division" must
    # actually say "Chipmunk" for this to land in other_division_id.
    core.insert_game(
        conn, _game("Blues", "Kings", 1, 0, "2026-07-14", division="Chipmunk"), "g2.pdf", other_division_id
    )

    table = core.standings_table(conn, division_id)
    assert {r["Team"] for r in table} == {"Avalanche", "Wild"}
    other_table = core.standings_table(conn, other_division_id)
    assert {r["Team"] for r in other_table} == {"Blues", "Kings"}
