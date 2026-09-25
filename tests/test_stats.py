import game_sheet_core as core


def _game_with_stats(conn, division_id, source_file="stats.pdf"):
    data = {
        "game_date": "2026-07-14", "division": "Penguin",
        "home_team": "Avalanche", "home_color": "Red", "home_final_score": 3,
        "away_team": "Wild", "away_color": "Blue", "away_final_score": 1,
        "goals": [
            {"side": "home", "scorer_number": "9", "assist1_number": "4", "assist2_number": None,
             "period": "1", "time": "5:00"},
            {"side": "home", "scorer_number": "9", "assist1_number": None, "assist2_number": None,
             "period": "2", "time": "8:00"},
            {"side": "home", "scorer_number": "4", "assist1_number": "9", "assist2_number": None,
             "period": "3", "time": "1:00"},
            {"side": "away", "scorer_number": "7", "assist1_number": None, "assist2_number": None,
             "period": "1", "time": "10:00"},
        ],
        "penalties": [
            {"side": "home", "player_number": "9", "penalty_type": "Tripping", "period": "1", "time": "6:00"},
        ],
        "shootout_attempts": [],
    }
    return core.insert_game(conn, data, source_file=source_file, working_division_id=division_id)


def _stat_for(stats, team, number):
    return next(s for s in stats if s["team"] == team and s["number"] == number)


def test_goals_assists_and_points_tally_per_player(conn, division_id):
    _game_with_stats(conn, division_id)
    stats = core.get_player_stats(conn, division_id)

    number9 = _stat_for(stats, "avalanche", "9")
    assert number9["goals"] == 2
    assert number9["assists"] == 1
    assert number9["points"] == 3
    assert number9["penalties"] == 1

    number4 = _stat_for(stats, "avalanche", "4")
    assert number4["goals"] == 1
    assert number4["assists"] == 1
    assert number4["points"] == 2

    number7 = _stat_for(stats, "wild", "7")
    assert number7["goals"] == 1
    assert number7["assists"] == 0


def test_stats_aggregate_across_multiple_games(conn, division_id):
    _game_with_stats(conn, division_id, source_file="g1.pdf")
    _game_with_stats(conn, division_id, source_file="g2.pdf")
    stats = core.get_player_stats(conn, division_id)
    number9 = _stat_for(stats, "avalanche", "9")
    assert number9["goals"] == 4  # 2 goals/game x 2 games
    assert number9["points"] == 6


def test_stats_show_unlinked_player_with_no_roster_entry(conn, division_id):
    _game_with_stats(conn, division_id)
    stats = core.get_player_stats(conn, division_id)
    number9 = _stat_for(stats, "avalanche", "9")
    assert number9["player_id"] is None


def test_stats_use_roster_name_once_linked(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.replace_roster(conn, team_id, [{"number": "9", "name": "Sidney Crosby"}])
    roster = core.list_roster(conn, team_id)
    entry = next(r for r in roster if r["number"] == "9")
    assert entry["player_id"] is None  # not linked to a profile yet

    core.link_roster_entry_to_player(conn, entry["id"], player_id)

    _game_with_stats(conn, division_id)
    stats = core.get_player_stats(conn, division_id)
    number9 = _stat_for(stats, "avalanche", "9")
    assert number9["player_id"] == player_id
    assert number9["name"] == "Sidney Crosby"


def test_empty_division_has_no_stats(conn, division_id):
    assert core.get_player_stats(conn, division_id) == []
