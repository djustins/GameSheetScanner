"""
league_site_sync.py

Pulls the schedule and played games from the league's public stats site
(https://teampgh-statsandstandings.web.app) into this app. The site is a
React front end over a JSON API; this reads the same read-only endpoints
its public pages do -- nothing is scraped out of HTML.

What lands where:

  - Each of the site's current leagues ("Penguin - Fall - 2026") is this
    app's division of the same year/season/age group, created if missing.
    A league whose name isn't one of core.AGE_GROUPS is skipped.
  - Its schedule goes into schedule_games (import_schedule: upserted, so a
    re-run just refreshes times/venues).
  - Every game with a final score becomes a game here, with its goals and
    penalties, keyed by a source_file of "teampgh-site:<game id>" so a
    re-run refreshes that game instead of adding a second copy. A game
    that's already here from a scanned sheet (same date, same two teams) is
    left alone.
  - Standings and player stats aren't copied: this app computes both from
    the games, and the points rules are the same as the site's (3 for a
    win, 2/1 for an overtime or shootout win/loss).

Stats here are attributed by jersey number per team, so before a team's
games are saved its roster is lined up with the site's: a rostered player
still on a draft placeholder ("TBD3") gets their real number, and a number
nobody on the roster wears is added under the site's name for that player
(unlinked, like a number first seen on a scanned sheet).
"""

import json
import re
import urllib.request

import game_sheet_core as core

API_BASE = "https://usabh-consolidated-backend-2377de64bd11.herokuapp.com/teampitt/api/"
SOURCE_PREFIX = "teampgh-site:"


def fetch_json(path: str) -> dict:
    with urllib.request.urlopen(API_BASE + path, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def site_date_to_iso(date: str | None) -> str | None:
    """The site's "MM-DD-YYYY" as ISO "YYYY-MM-DD"."""
    return core.normalize_date(date)


def _jersey(number_and_name: str | None) -> str | None:
    """The jersey number out of the site's "8 : Nicholas Elm"."""
    if not number_and_name:
        return None
    number = str(number_and_name).split(":", 1)[0].strip()
    return number or None


def _name_key(name: str | None) -> str:
    return re.sub(r"\s+", " ", name or "").strip().lower()


def schedule_rows(site_games: list[dict]) -> list[dict]:
    """The site's schedule entries as import_schedule rows. Practices and
    other non-game entries are left out."""
    rows = []
    for g in site_games:
        if g.get("itemType", "game") != "game" or not g.get("homeTeamName") or not g.get("visitorTeamName"):
            continue
        rows.append({
            "game_date": site_date_to_iso(g.get("date")),
            "home_team": g["homeTeamName"],
            "away_team": g["visitorTeamName"],
            "start_time": g.get("time") or None,
            "location": g.get("venueName") or None,
            "round": "Championship" if g.get("championship") else "Playoff" if g.get("playoff") else None,
        })
    return rows


def convert_game(detail: dict) -> tuple[dict, bool]:
    """One game from the site (its admin/game/<id> payload) as this app's
    game data, plus whether it was decided in overtime -- which this app has
    no field for on its own (see _mark_overtime).

    The site records a shootout as one extra goal with period "SO" and no
    scorer, and nothing about the individual attempts. This app keeps
    shootouts out of the goals list, so that row becomes a one-round
    shootout the winner scored in and the loser didn't, shooters unknown --
    enough for the game to count as a shootout win/loss."""
    game = detail["game"]
    stats = detail.get("gameStats") or {}
    home_score, away_score = stats.get("homeGoalsTotal"), stats.get("visitorGoalsTotal")
    if home_score is None or away_score is None:
        home_score, away_score = (int(n) for n in re.findall(r"\d+", game.get("score") or "")[:2])

    goals = []
    for g in detail.get("allGoalsFromThisGame") or []:
        if str(g.get("period")).upper() == "SO":
            continue
        goals.append({
            "side": "home" if g.get("teamId") == game.get("homeTeamId") else "away",
            "scorer_number": _jersey(g.get("scorerNumberAndName")) or "?",
            "assist1_number": _jersey(g.get("a1NumberAndName")),
            "assist2_number": _jersey(g.get("a2NumberAndName")),
            "period": str(g["period"]) if g.get("period") is not None else None,
            "time": g.get("time"),
        })

    home_key = _name_key(game.get("homeTeamName"))
    penalties = [
        {
            "side": "home" if _name_key(p.get("teamName")) == home_key else "away",
            "player_number": str(p["jerseyNumber"]) if p.get("jerseyNumber") is not None else "?",
            "penalty_type": p.get("penaltyType"),
            "period": str(p["period"]) if p.get("period") is not None else None,
            "time": p.get("time"),
        }
        for p in detail.get("allPenaltiesFromThisGame") or []
    ]

    status = (game.get("status") or stats.get("status") or "").lower()
    shootout = []
    if status == "shootout" and home_score != away_score:
        winner = "home" if home_score > away_score else "away"
        loser = "away" if winner == "home" else "home"
        shootout = [
            {"side": winner, "round": 1, "player_number": "?", "scored": True},
            {"side": loser, "round": 1, "player_number": "?", "scored": False},
        ]

    data = {
        "game_date": site_date_to_iso(game.get("date")),
        "division": game.get("leagueName"),
        "home_team": game.get("homeTeamName"),
        "home_color": None,
        "home_final_score": home_score,
        "away_team": game.get("visitorTeamName"),
        "away_color": None,
        "away_final_score": away_score,
        "goals": goals,
        "penalties": penalties,
        "shootout_attempts": shootout,
    }
    return data, status == "overtime"


def _mark_overtime(conn, game_id: int, data: dict) -> None:
    """Counts a game as an overtime win/loss (2 points / 1 point). The game
    form only derives that from shootout attempts, so it's set directly."""
    winner = core.resolve_winner(data)
    if winner not in ("home", "away"):
        return
    conn.execute(
        "UPDATE games SET ot_winner = %s, ot_loser = %s WHERE id = %s",
        (winner, "away" if winner == "home" else "home", game_id),
    )
    conn.commit()


def _line_up_roster(conn, division_id: int, team_name: str, site_players: list[dict], numbers: set[str],
                    report: dict, dry_run: bool) -> None:
    """Gets one team's roster ready for stats keyed by jersey number: see the
    module docstring. `numbers` is every number this team has in the games
    being saved; `site_players` is the site's roster for it."""
    teams = {_name_key(t["name"]): t["id"] for t in core.list_teams(conn, division_id)}
    matched = core.normalize_team_name(conn, division_id, team_name)
    team_id = teams.get(_name_key(matched))
    roster = core.list_roster(conn, team_id) if team_id is not None else []
    by_name = {_name_key(e["name"]): e for e in roster}
    taken = {e["number"] for e in roster}

    for p in site_players:
        number = str(p.get("number") or "").strip()
        name = " ".join(part for part in (p.get("firstName"), p.get("lastName")) if part).strip()
        if not number or not name:
            continue
        entry = by_name.get(_name_key(name))
        if entry is not None:
            if entry["number"] == number:
                continue
            if entry["number"].isdigit():
                # A real number that disagrees with the site's: someone has to decide which is right.
                report["number_mismatches"].append(
                    f"{team_name}: {name} is #{entry['number']} here, #{number} on the site"
                )
                continue
            report["numbers_set"] += 1
            if not dry_run:
                core.update_roster_entry(conn, entry["id"], swap_numbers=True, number=number)
            taken.add(number)
        elif number in numbers and number not in taken:
            report["roster_added"].append(f"{team_name}: #{number} {name}")
            if not dry_run:
                core.add_roster_entry(conn, core.add_team(conn, division_id, team_name), number, name)
            taken.add(number)


def sync_league(conn, league: dict, fetch=fetch_json, dry_run: bool = False) -> dict:
    """Syncs one of the site's leagues (an entry of admin/leagues/current)
    into its division here. Returns a report of what was (or, with dry_run,
    would be) done."""
    label = f"{league['leagueName']} - {league['session']} - {league['year']}"
    report = {
        "league": label, "skipped": None, "scheduled": 0, "added": [], "refreshed": [],
        "already_here": [], "failed": [], "numbers_set": 0, "number_mismatches": [], "roster_added": [],
    }
    age_group = next((n for n in core.AGE_GROUPS if n.lower() == league["leagueName"].strip().lower()), None)
    if age_group is None:
        report["skipped"] = f"\"{league['leagueName']}\" isn't one of this app's age groups"
        return report

    year, season = int(league["year"]), core.normalize_text(league["session"])
    existing = conn.execute(
        "SELECT id FROM divisions WHERE year = %s AND season = %s AND age_group = %s", (year, season, age_group)
    ).fetchone()
    if existing is None and dry_run:
        report["new_division"] = True
    division_id = existing[0] if existing else None if dry_run else core.add_division(conn, year, season, age_group)

    site_games = fetch(f"league/schedule/{league['_id']}").get("allLeagueGamesArray") or []
    rows = schedule_rows(site_games)
    report["scheduled"] = len(rows)
    if rows and not dry_run:
        core.import_schedule(conn, division_id, rows)

    played = [g for g in site_games if g.get("itemType", "game") == "game" and g.get("score")]
    if not played:
        return report

    site_roster: dict[str, list[dict]] = {}
    for p in fetch(f"league/{league['_id']}/scoringLeaders").get("scoringLeaders") or []:
        site_roster.setdefault(_name_key(p.get("teamName")), []).append(p)

    stored = [] if division_id is None else conn.execute(
        "SELECT id, game_date, home_team, away_team, source_file FROM games WHERE division_id = %s", (division_id,)
    ).fetchall()
    by_source = {row[4]: row[0] for row in stored}

    converted = []
    for g in played:
        data, overtime = convert_game(fetch(f"admin/game/{g['_id']}"))
        converted.append((g, data, overtime))

    # Every number each team has in these games, so rosters are lined up once per team.
    numbers: dict[str, set[str]] = {}
    for _, data, _ in converted:
        for side, used in core._collect_numbers_by_side(data).items():
            numbers.setdefault(data[f"{side}_team"], set()).update(used)
    if division_id is not None:
        for team_name, used in numbers.items():
            _line_up_roster(conn, division_id, team_name, site_roster.get(_name_key(team_name), []), used,
                            report, dry_run)

    for g, data, overtime in converted:
        source = SOURCE_PREFIX + g["_id"]
        title = (
            f"{data['game_date']} {data['home_team']} {data['home_final_score']}"
            f"-{data['away_final_score']} {data['away_team']}"
        )
        game_id = by_source.get(source)
        if game_id is None and division_id is not None:
            teams = {
                core.normalize_team_name(conn, division_id, data["home_team"]),
                core.normalize_team_name(conn, division_id, data["away_team"]),
            }
            if any(row[1] == data["game_date"] and {row[2], row[3]} == teams for row in stored):
                report["already_here"].append(title)
                continue
        try:
            if dry_run:
                if core.resolve_winner(data) == "tie":
                    raise ValueError("Games can't end in a tie here.")
                core.validate_shootout(data)
            elif game_id is None:
                game_id, _ = core.insert_game(conn, data, source, division_id)
            else:
                core.update_game(conn, game_id, data, division_id)
            if overtime and not dry_run:
                _mark_overtime(conn, game_id, data)
        except ValueError as e:
            report["failed"].append(f"{title}: {e}")
            continue
        report["refreshed" if source in by_source else "added"].append(title)
    return report


def sync(conn, league_names: list[str] | None = None, fetch=fetch_json, dry_run: bool = False) -> list[dict]:
    """Syncs the site's current leagues -- all of them, or just those named."""
    wanted = {n.strip().lower() for n in league_names or []}
    leagues = fetch("admin/leagues/current").get("currentLeagues") or []
    return [
        sync_league(conn, league, fetch=fetch, dry_run=dry_run)
        for league in leagues
        if not wanted or league["leagueName"].strip().lower() in wanted
    ]


def format_report(report: dict, dry_run: bool = False) -> str:
    """A report from sync_league as plain text, for the command line."""
    if report["skipped"]:
        return f"{report['league']}: skipped -- {report['skipped']}"
    would = "would be " if dry_run else ""
    lines = [f"{report['league']}:"]
    if report.get("new_division"):
        lines.append("  division doesn't exist here yet -- it would be created")
    lines.append(f"  {report['scheduled']} scheduled games {would}saved")
    lines.append(
        f"  games: {len(report['added'])} {would}added, {len(report['refreshed'])} {would}refreshed, "
        f"{len(report['already_here'])} already here from a scoresheet, {len(report['failed'])} failed"
    )
    lines.append(
        f"  rosters: {report['numbers_set']} placeholder numbers {would}set, "
        f"{len(report['roster_added'])} unlisted players {would}added"
    )
    for heading, key in (
        ("added", "added"), ("already here, left alone", "already_here"), ("FAILED", "failed"),
        ("number differs from the site (not changed)", "number_mismatches"),
        ("added to a roster, not linked to a player", "roster_added"),
    ):
        for item in report[key]:
            lines.append(f"    {heading}: {item}")
    return "\n".join(lines)
