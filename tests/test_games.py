import pytest

import game_sheet_core as core


def _game(home="Avalanche", away="Wild", home_score=3, away_score=2, date="2026-07-14", **overrides):
    data = {
        "game_date": date, "division": "Penguin",
        "home_team": home, "home_color": "Red", "home_final_score": home_score,
        "away_team": away, "away_color": "Blue", "away_final_score": away_score,
        "goals": [], "penalties": [], "shootout_attempts": [],
    }
    data.update(overrides)
    return data


def test_insert_and_list_game(conn, division_id):
    game_id, already_existed = core.insert_game(conn, _game(), source_file="sheet1.pdf", working_division_id=division_id)
    assert already_existed is False
    assert isinstance(game_id, int)

    games = core.list_games(conn, division_id)
    assert len(games) == 1
    g = games[0]
    assert g["home_team"] == "Avalanche"
    assert g["away_team"] == "Wild"
    assert g["home_final_score"] == 3
    assert g["away_final_score"] == 2
    assert g["winner"] == "home"


def test_insert_game_rejects_a_tie(conn, division_id):
    with pytest.raises(ValueError, match="tie"):
        core.insert_game(conn, _game(home_score=2, away_score=2), source_file="tie.pdf", working_division_id=division_id)


def test_insert_game_is_idempotent_on_same_source_file(conn, division_id):
    game_id_1, already_existed_1 = core.insert_game(
        conn, _game(), source_file="dup.pdf", working_division_id=division_id
    )
    game_id_2, already_existed_2 = core.insert_game(
        conn, _game(), source_file="dup.pdf", working_division_id=division_id
    )
    assert already_existed_1 is False
    assert already_existed_2 is True
    assert game_id_1 == game_id_2
    assert len(core.list_games(conn, division_id)) == 1


def test_shootout_game_winner_and_ot_result(conn, division_id):
    # Regulation ends 4-4, home wins the shootout -> stored as 5-4 with an
    # explicit ot_winner/ot_loser (a shootout win/loss, not a plain one).
    data = _game(
        home_score=5, away_score=4,
        shootout_attempts=[
            {"side": "home", "round": 1, "player_number": "9", "scored": True},
            {"side": "away", "round": 1, "player_number": "4", "scored": False},
        ],
    )
    game_id, _ = core.insert_game(conn, data, source_file="so.pdf", working_division_id=division_id)
    loaded, source_file = core.load_game(conn, game_id)
    assert source_file == "so.pdf"
    assert loaded["winner"] == "home"
    assert len(loaded["shootout_attempts"]) == 2


def test_shootout_with_mismatched_score_is_rejected(conn, division_id):
    # Shootout recorded, but score is more than one goal apart -> invalid.
    data = _game(
        home_score=6, away_score=4,
        shootout_attempts=[{"side": "home", "round": 1, "player_number": "9", "scored": True}],
    )
    with pytest.raises(ValueError, match="[Ss]hootout"):
        core.insert_game(conn, data, source_file="bad_so.pdf", working_division_id=division_id)


def test_load_game_round_trips_goals_and_penalties(conn, division_id):
    data = _game(
        goals=[{"side": "home", "scorer_number": "9", "assist1_number": "4", "assist2_number": None,
                "period": "1", "time": "5:00"}],
        penalties=[{"side": "away", "player_number": "7", "penalty_type": "Tripping", "period": "2", "time": "10:00"}],
    )
    game_id, _ = core.insert_game(conn, data, source_file="stats.pdf", working_division_id=division_id)
    loaded, _ = core.load_game(conn, game_id)
    assert len(loaded["goals"]) == 1
    assert loaded["goals"][0]["scorer_number"] == "9"
    assert len(loaded["penalties"]) == 1
    assert loaded["penalties"][0]["player_number"] == "7"


def test_update_game_replaces_stats(conn, division_id):
    game_id, _ = core.insert_game(
        conn, _game(goals=[{"side": "home", "scorer_number": "9", "assist1_number": None,
                             "assist2_number": None, "period": "1", "time": "1:00"}]),
        source_file="update_me.pdf", working_division_id=division_id,
    )
    updated_data = _game(home_score=1, away_score=0, goals=[])
    core.update_game(conn, game_id, updated_data, working_division_id=division_id)

    loaded, _ = core.load_game(conn, game_id)
    assert loaded["home_final_score"] == 1
    assert loaded["away_final_score"] == 0
    assert loaded["goals"] == []


def test_delete_game_removes_it_and_its_stats(conn, division_id):
    game_id, _ = core.insert_game(
        conn, _game(goals=[{"side": "home", "scorer_number": "9", "assist1_number": None,
                             "assist2_number": None, "period": "1", "time": "1:00"}]),
        source_file="delete_me.pdf", working_division_id=division_id,
    )
    core.delete_game(conn, game_id)
    assert core.list_games(conn, division_id) == []
    loaded, source_file = core.load_game(conn, game_id)
    assert loaded is None
    assert source_file is None


def test_find_game_by_source_file(conn, division_id):
    assert core.find_game_by_source_file(conn, "findme.pdf") is None
    game_id, _ = core.insert_game(conn, _game(), source_file="findme.pdf", working_division_id=division_id)
    found = core.find_game_by_source_file(conn, "findme.pdf")
    assert found["id"] == game_id
    assert found["home_team"] == "Avalanche"
