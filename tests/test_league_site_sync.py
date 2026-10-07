import game_sheet_core as core
import league_site_sync

LEAGUE = {"_id": "L1", "leagueName": "Penguin", "session": "Fall", "year": 2026}


def _site_game(game_id, date, home, away, score=None, status=None, **extra):
    game = {
        "_id": game_id, "leagueName": "Penguin", "session": "Fall", "year": 2026, "date": date, "time": "18:00",
        "homeTeamName": home, "homeTeamId": f"T-{home}", "visitorTeamName": away, "visitorTeamId": f"T-{away}",
        "venueName": "TEAM PITT", "itemType": "game", **extra,
    }
    if score:
        game["score"], game["status"] = score, status or "FINAL"
    return game


def _goal(team, scorer, period="1", time="4:13", a1=None):
    return {
        "teamId": f"T-{team}", "teamName": team, "period": period, "time": time,
        "scorerNumberAndName": scorer, "a1NumberAndName": a1, "a2NumberAndName": None,
    }


def _fake_site(games, details, players=()):
    """A stand-in for league_site_sync.fetch_json serving one league."""
    def fetch(path):
        if path == "admin/leagues/current":
            return {"currentLeagues": [LEAGUE, {**LEAGUE, "_id": "L2", "leagueName": "Super League"}]}
        if path == "league/schedule/L1":
            return {"allLeagueGamesArray": games}
        if path == "league/L1/scoringLeaders":
            return {"scoringLeaders": list(players)}
        if path.startswith("admin/game/"):
            game = next(g for g in games if g["_id"] == path.rsplit("/", 1)[1])
            home, away = (int(n) for n in game["score"].split(" - "))
            return {
                "game": game, "gameStats": {"homeGoalsTotal": home, "visitorGoalsTotal": away},
                "allGoalsFromThisGame": [], "allPenaltiesFromThisGame": [], **details.get(game["_id"], {}),
            }
        raise AssertionError(f"unexpected fetch: {path}")
    return fetch


def test_convert_game_maps_sides_numbers_and_shootout():
    game = _site_game("g1", "10-06-2026", "Avalanche", "Kings", score="5 - 6", status="Shootout")
    data, overtime = league_site_sync.convert_game({
        "game": game,
        "gameStats": {"homeGoalsTotal": 5, "visitorGoalsTotal": 6},
        "allGoalsFromThisGame": [
            _goal("Avalanche", "8 : Nicholas Elm", a1="12 : Benjamin Fowler"),
            _goal("Kings", "2 : Camryn Seitz", period="2"),
            _goal("Kings", None, period="SO", time=None),
        ],
        "allPenaltiesFromThisGame": [
            {"teamName": "Kings", "jerseyNumber": 5, "penaltyType": "Roughing", "period": 3, "time": "1:57"},
        ],
    })
    assert overtime is False
    assert data["game_date"] == "2026-10-06"
    assert (data["home_final_score"], data["away_final_score"]) == (5, 6)
    # The site's "SO" goal row isn't a goal here; it becomes the shootout result.
    assert [(g["side"], g["scorer_number"], g["assist1_number"], g["period"]) for g in data["goals"]] == [
        ("home", "8", "12", "1"), ("away", "2", None, "2"),
    ]
    assert data["penalties"] == [
        {"side": "away", "player_number": "5", "penalty_type": "Roughing", "period": "3", "time": "1:57"},
    ]
    assert core.shootout_winner(data["shootout_attempts"]) == "away"
    core.validate_shootout(data)


def test_schedule_rows_skip_practices():
    rows = league_site_sync.schedule_rows([
        _site_game("g1", "10-08-2026", "Admirals", "Mariners"),
        {**_site_game("p1", "10-09-2026", "", ""), "itemType": "practice"},
        _site_game("g2", "12-11-2026", "Admirals", "Mariners", playoff=True),
    ])
    assert [(r["game_date"], r["home_team"], r["away_team"], r["start_time"], r["round"]) for r in rows] == [
        ("2026-10-08", "Admirals", "Mariners", "18:00", None),
        ("2026-12-11", "Admirals", "Mariners", "18:00", "Playoff"),
    ]


def test_sync_imports_schedule_games_and_standings_and_is_repeatable(conn):
    games = [
        _site_game("g1", "10-06-2026", "Avalanche", "Kings", score="2 - 1", status="Overtime"),
        _site_game("g2", "10-13-2026", "Kings", "Avalanche"),
    ]
    details = {"g1": {"allGoalsFromThisGame": [
        _goal("Avalanche", "8 : Nicholas Elm"),
        _goal("Kings", "2 : Camryn Seitz", period="3"),
        _goal("Avalanche", "8 : Nicholas Elm", period="OT", time="0:01"),
    ]}}
    players = [
        {"teamName": "Avalanche", "number": "8", "firstName": "Nicholas", "lastName": "Elm"},
        {"teamName": "Kings", "number": "2", "firstName": "Camryn", "lastName": "Seitz"},
        {"teamName": "Kings", "number": "9", "firstName": "Bench", "lastName": "Warmer"},
        {"teamName": "Avalanche", "number": "14", "firstName": "Teddy", "lastName": "Davis"},
    ]
    fetch = _fake_site(games, details, players)

    # Nicholas Elm is already rostered, still on his draft placeholder number.
    division_id = core.add_division(conn, 2026, "Fall", "Penguin")
    avalanche = core.add_team(conn, division_id, "Avalanche")
    core.add_roster_entry(conn, avalanche, "TBD1", "Nicholas Elm", player_id=core.add_player(conn, "Nicholas", "Elm"))
    # The site lists Theodore Davis by his nickname.
    teddy = core.add_player(conn, "Theodore", "Davis")
    core.update_player(conn, teddy, nickname="Teddy")
    core.add_roster_entry(conn, avalanche, "TBD2", "Theodore Davis", player_id=teddy)

    reports = league_site_sync.sync(conn, fetch=fetch)
    report, skipped = reports
    assert skipped["skipped"]  # "Super League" isn't an age group here
    assert report["scheduled"] == 2 and len(report["added"]) == 1 and not report["failed"]
    assert report["numbers_set"] == 2
    assert report["roster_added"] == ["Kings: #2 Camryn Seitz"]  # only numbers that appear in a game

    assert len(core.list_schedule(conn, division_id)) == 2
    assert [(r["number"], r["name"]) for r in core.list_roster(conn, avalanche)] == [
        ("14", "Theodore Davis"), ("8", "Nicholas Elm"),
    ]
    # Overtime: 2 points to the winner, 1 to the loser.
    standings = {s["team"]: s["points"] for s in core.get_standings(conn, division_id)}
    assert standings == {"avalanche": 2, "kings": 1}

    # Running it again refreshes the same game rather than adding another.
    again = league_site_sync.sync(conn, fetch=fetch, refresh_days=None)[0]
    assert (len(again["added"]), len(again["refreshed"])) == (0, 1)
    assert len(core.list_games(conn, division_id)) == 1
    assert {s["team"]: s["points"] for s in core.get_standings(conn, division_id)} == standings

    # Past the refresh window, an imported game isn't re-read at all.
    old = league_site_sync.sync(conn, fetch=fetch, refresh_days=-1)[0]
    assert (len(old["added"]), len(old["refreshed"]), old["unchanged"]) == (0, 0, 1)


def test_sync_leaves_a_scanned_game_alone_and_dry_run_saves_nothing(conn):
    games = [_site_game("g1", "10-06-2026", "Avalanche", "Kings", score="4 - 3")]
    fetch = _fake_site(games, {})

    dry = league_site_sync.sync(conn, fetch=fetch, dry_run=True)[0]
    assert len(dry["added"]) == 1 and dry["new_division"]
    assert core.list_divisions(conn) == []

    division_id = core.add_division(conn, 2026, "Fall", "Penguin")
    core.insert_game(conn, {
        "game_date": "10/6/26", "division": "Penguin", "home_team": "Kings", "home_final_score": 3,
        "away_team": "Avalanche", "away_final_score": 4, "goals": [], "penalties": [], "shootout_attempts": [],
    }, "scan_p1.pdf", division_id)

    report = league_site_sync.sync(conn, fetch=fetch)[0]
    assert len(report["already_here"]) == 1 and not report["added"]
    assert len(core.list_games(conn, division_id)) == 1
