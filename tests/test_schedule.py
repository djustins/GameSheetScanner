import game_sheet_core as core


def _schedule_row(home="Avalanche", away="Wild", date="2026-07-14", **overrides):
    row = {"order": 1, "round": "1", "game_date": date, "home_team": home, "away_team": away,
           "start_time": "19:00", "end_time": None, "location": "Rink 1", "field": None}
    row.update(overrides)
    return row


def _game(home="Avalanche", away="Wild", home_score=3, away_score=2, date="2026-07-14", **overrides):
    data = {
        "game_date": date, "division": "Penguin",
        "home_team": home, "home_color": "Red", "home_final_score": home_score,
        "away_team": away, "away_color": "Blue", "away_final_score": away_score,
        "goals": [], "penalties": [], "shootout_attempts": [],
    }
    data.update(overrides)
    return data


def test_import_schedule_saves_rows(conn, division_id):
    saved = core.import_schedule(conn, division_id, [_schedule_row(), _schedule_row(home="Blues", away="Kings")])
    assert saved == 2
    schedule = core.list_schedule(conn, division_id)
    assert len(schedule) == 2


def test_import_schedule_skips_rows_missing_required_fields(conn, division_id):
    rows = [_schedule_row(), _schedule_row(home=""), _schedule_row(away=None), {"game_date": None}]
    saved = core.import_schedule(conn, division_id, rows)
    assert saved == 1


def test_reimporting_schedule_upserts_not_duplicates(conn, division_id):
    core.import_schedule(conn, division_id, [_schedule_row(start_time="19:00")])
    core.import_schedule(conn, division_id, [_schedule_row(start_time="20:30")])  # corrected time
    schedule = core.list_schedule(conn, division_id)
    assert len(schedule) == 1
    assert schedule[0]["start_time"] == "20:30"


def test_unplayed_scheduled_game_is_not_accounted_for(conn, division_id):
    core.import_schedule(conn, division_id, [_schedule_row()])
    schedule = core.list_schedule(conn, division_id)
    assert len(schedule) == 1
    assert schedule[0]["accounted_for"] is False
    assert schedule[0]["result"] is None


def test_played_scheduled_game_is_accounted_for_with_result(conn, division_id):
    """This is the exact path that crashed production: list_schedule() on a
    division with both a schedule and at least one played game. A second
    loop over the same `stored` rows (finding "orphan"/mis-dated games) had
    fallen out of sync with a widened SELECT and crashed every call with a
    ValueError — see the "Fix crash in list_schedule()" commit."""
    core.import_schedule(conn, division_id, [_schedule_row()])
    core.insert_game(conn, _game(home_score=3, away_score=2), source_file="s1.pdf", working_division_id=division_id)

    schedule = core.list_schedule(conn, division_id)
    assert len(schedule) == 1
    row = schedule[0]
    assert row["accounted_for"] is True
    assert row["result"] is not None
    assert row["result"]["home_score"] == 3
    assert row["result"]["away_score"] == 2
    assert row["result"]["home_team"] == "Avalanche"
    assert row["result"]["away_team"] == "Wild"


def test_result_reflects_actual_recorded_sides_even_if_swapped(conn, division_id):
    """A transcribed sheet occasionally has home/away swapped relative to
    the official schedule -- list_schedule() still recognizes it as the
    same matchup (same two teams, same date) and reports the game's
    actual recorded sides in "result", not the schedule's designation."""
    core.import_schedule(conn, division_id, [_schedule_row(home="Avalanche", away="Wild")])
    core.insert_game(
        conn, _game(home="Wild", away="Avalanche", home_score=2, away_score=3),
        source_file="swapped.pdf", working_division_id=division_id,
    )
    schedule = core.list_schedule(conn, division_id)
    assert schedule[0]["accounted_for"] is True
    assert schedule[0]["result"]["home_team"] == "Wild"
    assert schedule[0]["result"]["away_team"] == "Avalanche"


def test_mis_dated_game_is_flagged_as_a_possible_match(conn, division_id):
    """A stored game between the scheduled matchup's two teams, filed under
    a date that isn't any of that matchup's scheduled dates, should surface
    as a "possible_matches" hint on the unaccounted schedule row -- this
    exercises the second (previously-broken) loop over `stored` directly."""
    core.import_schedule(conn, division_id, [_schedule_row(date="2026-07-14")])
    game_id, _ = core.insert_game(
        conn, _game(date="2026-07-15"),  # one day off from the scheduled date -- a typo'd date
        source_file="typo_date.pdf", working_division_id=division_id,
    )
    schedule = core.list_schedule(conn, division_id)
    assert len(schedule) == 1
    row = schedule[0]
    assert row["accounted_for"] is False
    assert len(row["possible_matches"]) == 1
    assert row["possible_matches"][0]["id"] == game_id


def test_clear_schedule_removes_every_row_for_the_division(conn, division_id):
    core.import_schedule(conn, division_id, [_schedule_row(), _schedule_row(home="Blues", away="Kings")])
    assert len(core.list_schedule(conn, division_id)) == 2
    core.clear_schedule(conn, division_id)
    assert core.list_schedule(conn, division_id) == []


def test_clear_schedule_does_not_touch_other_divisions(conn, division_id):
    other_division_id = core.add_division(conn, 2026, "Summer", "Chipmunk")
    core.import_schedule(conn, division_id, [_schedule_row()])
    core.import_schedule(conn, other_division_id, [_schedule_row(home="Blues", away="Kings")])
    core.clear_schedule(conn, division_id)
    assert core.list_schedule(conn, division_id) == []
    assert len(core.list_schedule(conn, other_division_id)) == 1
