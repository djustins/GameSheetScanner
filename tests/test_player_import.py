import game_sheet_core as core


def test_detect_player_import_columns_recognizes_the_real_spreadsheets_headers():
    headers = [
        "Player Name", "Comments", "Recent Team", "Age", "Date Of Birth", "New?", "Position",
        "Team", "Coach", "Parent FirstName", "Parent LastName", "Primary Contact Email",
        "Primary Contact Telephone",
    ]
    detected = core.detect_player_import_columns(headers)
    assert detected["name"] == "Player Name"
    assert detected["birth_date"] == "Date Of Birth"
    assert detected["team"] == "Team"
    assert detected["coach"] == "Coach"
    assert detected["contact_first_name"] == "Parent FirstName"
    assert detected["contact_last_name"] == "Parent LastName"
    assert detected["contact_email"] == "Primary Contact Email"
    assert detected["contact_phone"] == "Primary Contact Telephone"
    assert detected["position"] == "Position"


def test_detect_player_import_columns_is_flexible_about_common_variants():
    detected = core.detect_player_import_columns(["Full Name", "DOB", "Team Name", "Email", "Jersey #"])
    assert detected["name"] == "Full Name"
    assert detected["birth_date"] == "DOB"
    assert detected["team"] == "Team Name"
    assert detected["contact_email"] == "Email"
    assert detected["number"] == "Jersey #"


def test_detect_player_import_columns_supports_separate_first_last_columns():
    detected = core.detect_player_import_columns(["First Name", "Last Name"])
    assert detected["first_name"] == "First Name"
    assert detected["last_name"] == "Last Name"
    assert "name" not in detected


def test_detect_player_import_columns_handles_a_real_messy_registration_export():
    """Modeled on an actual export (U10_Fall_2026.ods) that broke the
    original exact-alias-only matcher: "Player First/Last Name" (not just
    "First Name"), "Account First/Last Name" for the parent (not "Parent
    ..."), "User Email", "Telephone"/"Cellphone", and a verbatim
    registration-form question standing in for "Position"."""
    headers = [
        "Division Name", "Player First Name", "Player Last Name", "Player Gender",
        "Player Birth Date", "Account First Name", "Account Last Name", "Street Address",
        "User Email", "Telephone", "Cellphone", "What position does your child prefer?",
        "Is your child new to Team Pittsburgh?", "Teammate Request",
    ]
    detected = core.detect_player_import_columns(headers)
    assert detected["first_name"] == "Player First Name"
    assert detected["last_name"] == "Player Last Name"
    assert detected["contact_first_name"] == "Account First Name"
    assert detected["contact_last_name"] == "Account Last Name"
    assert detected["birth_date"] == "Player Birth Date"
    assert detected["contact_email"] == "User Email"
    assert detected["contact_phone"] == "Telephone"
    assert detected["position"] == "What position does your child prefer?"
    # No team-assignment column actually exists in this file (it's a
    # signup list, not a roster) -- must NOT be fooled into matching one of
    # these, both of which genuinely contain the whole word "team".
    assert "team" not in detected
    assert detected.get("team") != "Is your child new to Team Pittsburgh?"


def test_detect_player_import_columns_does_not_confuse_player_and_parent_names():
    detected = core.detect_player_import_columns(
        ["Player First Name", "Player Last Name", "Guardian First Name", "Guardian Last Name"]
    )
    assert detected["first_name"] == "Player First Name"
    assert detected["last_name"] == "Player Last Name"
    assert detected["contact_first_name"] == "Guardian First Name"
    assert detected["contact_last_name"] == "Guardian Last Name"


def test_detect_player_import_columns_word_boundary_avoids_teammate_and_coaching():
    detected = core.detect_player_import_columns(["Teammate Request", "Coaching Notes", "Player Name"])
    assert "team" not in detected
    assert "coach" not in detected
    assert detected["name"] == "Player Name"


def test_detect_player_import_columns_matches_cellphone_as_one_word():
    detected = core.detect_player_import_columns(["Player Name", "Cellphone"])
    assert detected["contact_phone"] == "Cellphone"


def test_detect_player_import_columns_does_not_double_claim_a_header():
    # "Phone Number" could plausibly match both contact_phone ("phone") and
    # the jersey-number fallback ("number") -- whichever field claims it
    # first must make it unavailable to the other.
    detected = core.detect_player_import_columns(["Player Name", "Phone Number"])
    assert detected["contact_phone"] == "Phone Number"
    assert detected.get("number") != "Phone Number"


def test_missing_name_column_row_is_invalid(conn, division_id):
    columns = core.detect_player_import_columns(["Comments"])
    plan = core.build_player_import_plan(conn, division_id, [{"Comments": "no name here"}], columns)
    assert plan[0]["status"] == "invalid"
    assert plan[0]["resolved_action"] == "invalid"


def test_new_player_gets_create_status(conn, division_id):
    columns = core.detect_player_import_columns(["Player Name", "Date Of Birth"])
    plan = core.build_player_import_plan(
        conn, division_id, [{"Player Name": "Sidney Crosby", "Date Of Birth": "2016-08-07"}], columns
    )
    entry = plan[0]
    assert entry["status"] == "create"
    assert entry["resolved_action"] == "create"
    assert entry["first_name"] == "Sidney"
    assert entry["last_name"] == "Crosby"
    assert entry["birth_date"] == "2016-08-07"


def test_single_unambiguous_name_match_auto_resolves_to_update(conn, division_id):
    player_id = core.add_player(conn, "Sidney", "Crosby", birth_date="2016-08-07")
    columns = core.detect_player_import_columns(["Player Name", "Date Of Birth"])
    plan = core.build_player_import_plan(
        conn, division_id, [{"Player Name": "Sidney Crosby", "Date Of Birth": "2016-08-07"}], columns
    )
    entry = plan[0]
    assert entry["status"] == "update"
    assert entry["matched_player_id"] == player_id
    assert entry["resolved_action"] == "update"
    assert entry["resolved_player_id"] == player_id


def test_matching_name_but_conflicting_birth_date_is_flagged_not_auto_merged(conn, division_id):
    player_id = core.add_player(conn, "Sidney", "Crosby", birth_date="2016-08-07")
    columns = core.detect_player_import_columns(["Player Name", "Date Of Birth"])
    plan = core.build_player_import_plan(
        conn, division_id, [{"Player Name": "Sidney Crosby", "Date Of Birth": "2015-01-01"}], columns
    )
    entry = plan[0]
    assert entry["status"] == "conflict"
    assert entry["matched_player_id"] == player_id
    assert entry["resolved_action"] is None  # needs a human decision
    assert entry["conflict_detail"] == {"existing": "2016-08-07", "incoming": "2015-01-01"}


def test_same_name_no_birth_date_info_is_not_a_conflict(conn, division_id):
    """No birth_date on either side isn't a disagreement -- nothing to
    compare, so it auto-resolves to update rather than blocking on a
    "conflict" that isn't actually one."""
    player_id = core.add_player(conn, "Sidney", "Crosby")
    columns = core.detect_player_import_columns(["Player Name"])
    plan = core.build_player_import_plan(conn, division_id, [{"Player Name": "Sidney Crosby"}], columns)
    assert plan[0]["status"] == "update"
    assert plan[0]["resolved_player_id"] == player_id


def test_ambiguous_multiple_same_name_matches_needs_review(conn, division_id):
    id1 = core.add_player(conn, "Sidney", "Crosby", birth_date="2016-08-07")
    id2 = core.add_player(conn, "Sidney", "Crosby", birth_date="2017-03-15")
    columns = core.detect_player_import_columns(["Player Name"])
    plan = core.build_player_import_plan(conn, division_id, [{"Player Name": "Sidney Crosby"}], columns)
    entry = plan[0]
    assert entry["status"] == "ambiguous"
    assert entry["resolved_action"] is None
    assert {c["id"] for c in entry["candidates"]} == {id1, id2}


def test_birth_date_disambiguates_multiple_same_name_matches(conn, division_id):
    core.add_player(conn, "Sidney", "Crosby", birth_date="2016-08-07")
    id2 = core.add_player(conn, "Sidney", "Crosby", birth_date="2017-03-15")
    columns = core.detect_player_import_columns(["Player Name", "Date Of Birth"])
    plan = core.build_player_import_plan(
        conn, division_id, [{"Player Name": "Sidney Crosby", "Date Of Birth": "2017-03-15"}], columns
    )
    entry = plan[0]
    assert entry["status"] == "update", "birth_date should have picked the unique matching candidate"
    assert entry["resolved_player_id"] == id2


def test_apply_creates_player_profile_only_when_no_team_given(conn, division_id):
    columns = core.detect_player_import_columns(["Player Name", "Date Of Birth"])
    plan = core.build_player_import_plan(
        conn, division_id, [{"Player Name": "Sidney Crosby", "Date Of Birth": "2016-08-07"}], columns
    )
    result = core.apply_player_import_plan(conn, division_id, plan)
    assert result == {"created": 1, "updated": 0, "skipped": 0, "rostered": 0, "coached": 0, "warnings": []}
    players = core.list_players(conn)
    assert len(players) == 1
    assert players[0]["current_division_id"] == division_id
    assert core.list_teams(conn, division_id) == []


def test_apply_assigns_team_and_roster_when_given(conn, division_id):
    columns = core.detect_player_import_columns(["Player Name", "Team", "Number"])
    plan = core.build_player_import_plan(
        conn, division_id, [{"Player Name": "Sidney Crosby", "Team": "Avalanche", "Number": "87"}], columns
    )
    result = core.apply_player_import_plan(conn, division_id, plan)
    assert result["created"] == 1
    assert result["rostered"] == 1

    teams = core.list_teams(conn, division_id)
    assert len(teams) == 1 and teams[0]["name"] == "Avalanche"
    roster = core.list_roster(conn, teams[0]["id"])
    assert len(roster) == 1
    assert roster[0]["number"] == "87"
    assert roster[0]["name"] == "Sidney Crosby"
    assert roster[0]["player_id"] is not None


def test_apply_assigns_coach_when_given_with_team(conn, division_id):
    columns = core.detect_player_import_columns(["Player Name", "Team", "Coach"])
    plan = core.build_player_import_plan(
        conn, division_id, [{"Player Name": "Sidney Crosby", "Team": "Avalanche", "Coach": "Mario Lemieux"}], columns
    )
    result = core.apply_player_import_plan(conn, division_id, plan)
    assert result["coached"] == 1
    assert result["warnings"] == []

    team_id = core.list_teams(conn, division_id)[0]["id"]
    coaches = core.list_team_coaches(conn, team_id)
    assert len(coaches) == 1
    assert coaches[0]["name"] == "Mario Lemieux"


def test_apply_assigns_a_second_coach_to_the_same_team_without_conflict(conn, division_id):
    # A team can have more than one coach (e.g. an assistant) -- assigning
    # a new one alongside an existing one is not a conflict.
    team_id = core.add_team(conn, division_id, "Avalanche")
    existing_coach_id = core.add_coach(conn, "Robert", "Dobson")
    core.assign_coach_to_team(conn, team_id, existing_coach_id)

    columns = core.detect_player_import_columns(["Player Name", "Team", "Coach"])
    plan = core.build_player_import_plan(
        conn, division_id,
        [{"Player Name": "Sidney Crosby", "Team": "Avalanche", "Coach": "Mario Lemieux"}], columns,
    )
    result = core.apply_player_import_plan(conn, division_id, plan)
    assert result["coached"] == 1
    assert result["warnings"] == []
    assert {c["name"] for c in core.list_team_coaches(conn, team_id)} == {"Robert Dobson", "Mario Lemieux"}


def test_apply_warns_instead_of_failing_when_coach_already_coaches_a_different_team(conn, division_id):
    # The real one-per-division constraint (assign_coach_to_team) is on the
    # *coach*: they can't coach two different teams in the same division.
    team_a = core.add_team(conn, division_id, "Avalanche")
    team_b = core.add_team(conn, division_id, "Wild")
    coach_id = core.add_coach(conn, "Mario", "Lemieux")
    core.assign_coach_to_team(conn, team_a, coach_id)

    columns = core.detect_player_import_columns(["Player Name", "Team", "Coach"])
    plan = core.build_player_import_plan(
        conn, division_id,
        [{"Player Name": "Sidney Crosby", "Team": "Wild", "Coach": "Mario Lemieux"}], columns,
    )
    result = core.apply_player_import_plan(conn, division_id, plan)
    assert result["created"] == 1  # the player import itself still succeeds
    assert result["coached"] == 0
    assert len(result["warnings"]) == 1
    assert "already coaches" in result["warnings"][0]
    # Neither assignment is disturbed by the failed attempt.
    assert [c["name"] for c in core.list_team_coaches(conn, team_a)] == ["Mario Lemieux"]
    assert core.list_team_coaches(conn, team_b) == []


def test_apply_skips_rows_with_no_resolution(conn, division_id):
    id1 = core.add_player(conn, "Sidney", "Crosby", birth_date="2016-08-07")
    id2 = core.add_player(conn, "Sidney", "Crosby", birth_date="2017-03-15")
    columns = core.detect_player_import_columns(["Player Name"])
    plan = core.build_player_import_plan(conn, division_id, [{"Player Name": "Sidney Crosby"}], columns)
    assert plan[0]["status"] == "ambiguous"

    result = core.apply_player_import_plan(conn, division_id, plan)  # nobody resolved it
    assert result == {"created": 0, "updated": 0, "skipped": 1, "rostered": 0, "coached": 0, "warnings": []}
    # Unchanged -- neither existing candidate touched, no new player created.
    player_ids = {p["id"] for p in core.list_players(conn)}
    assert player_ids == {id1, id2}
    assert all(p["current_division_id"] is None for p in core.list_players(conn))


def test_apply_honors_ui_resolution_of_an_ambiguous_row(conn, division_id):
    id1 = core.add_player(conn, "Sidney", "Crosby", birth_date="2016-08-07")
    core.add_player(conn, "Sidney", "Crosby", birth_date="2017-03-15")
    columns = core.detect_player_import_columns(["Player Name"])
    plan = core.build_player_import_plan(conn, division_id, [{"Player Name": "Sidney Crosby"}], columns)
    plan[0]["resolved_action"] = "use_existing"
    plan[0]["resolved_player_id"] = id1

    result = core.apply_player_import_plan(conn, division_id, plan)
    assert result["updated"] == 1
    updated_player = next(p for p in core.list_players(conn) if p["id"] == id1)
    assert updated_player["current_division_id"] == division_id


def test_apply_honors_ui_choosing_create_new_over_a_conflict(conn, division_id):
    core.add_player(conn, "Sidney", "Crosby", birth_date="2016-08-07")
    columns = core.detect_player_import_columns(["Player Name", "Date Of Birth"])
    plan = core.build_player_import_plan(
        conn, division_id, [{"Player Name": "Sidney Crosby", "Date Of Birth": "2015-01-01"}], columns
    )
    assert plan[0]["status"] == "conflict"
    plan[0]["resolved_action"] = "create"

    result = core.apply_player_import_plan(conn, division_id, plan)
    assert result["created"] == 1
    assert len(core.list_players(conn)) == 2


def test_row_with_only_first_and_last_name_columns_is_matched_and_created(conn, division_id):
    columns = core.detect_player_import_columns(["First Name", "Last Name"])
    plan = core.build_player_import_plan(
        conn, division_id, [{"First Name": "Sidney", "Last Name": "Crosby"}], columns
    )
    assert plan[0]["name"] == "Sidney Crosby"
    assert plan[0]["status"] == "create"


def test_empty_cells_from_pandas_are_treated_as_missing_not_the_string_nan(conn, division_id):
    """Reading a CSV/Excel file with pandas (dtype=str) still comes back
    with an empty cell as float('nan'), not None or "" -- str(float('nan'))
    is literally the text "nan", so a naive `value is None` check lets it
    through as if the cell said "nan". Caught by actually running the
    import against a real file with blank cells, which created a bogus
    "Nan" team with a bogus "nan" coach before this was fixed."""
    nan = float("nan")
    columns = core.detect_player_import_columns(["Player Name", "Team", "Number", "Coach", "Date Of Birth"])
    plan = core.build_player_import_plan(
        conn, division_id,
        [{"Player Name": "Sidney Crosby", "Team": nan, "Number": nan, "Coach": nan, "Date Of Birth": nan}],
        columns,
    )
    entry = plan[0]
    assert entry["team_name"] is None
    assert entry["number"] is None
    assert entry["coach_name"] is None
    assert entry["birth_date"] is None

    result = core.apply_player_import_plan(conn, division_id, plan)
    assert result["created"] == 1
    assert result["rostered"] == 0
    assert result["coached"] == 0
    assert core.list_teams(conn, division_id) == []  # no bogus "Nan" team created
    assert core.list_coaches(conn) == []  # no bogus "nan" coach created
