import game_sheet_core as core


def test_replace_roster_adds_entries(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    core.replace_roster(conn, team_id, [
        {"number": "9", "name": "Sidney Crosby"},
        {"number": "4", "name": "Bobby Orr"},
    ])
    roster = core.list_roster(conn, team_id)
    assert len(roster) == 2
    numbers = {r["number"] for r in roster}
    assert numbers == {"9", "4"}


def test_replace_roster_preserves_player_link_by_number(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.replace_roster(conn, team_id, [{"number": "9", "name": "Sidney Crosby"}])
    entry = core.list_roster(conn, team_id)[0]
    core.link_roster_entry_to_player(conn, entry["id"], player_id)

    # Re-saving the roster (e.g. editing the grid) with the same number
    # should keep the existing player link, not silently drop it.
    core.replace_roster(conn, team_id, [{"number": "9", "name": "Sid Crosby"}])
    entry_after = core.list_roster(conn, team_id)[0]
    assert entry_after["player_id"] == player_id


def test_replace_roster_removes_entries_not_in_the_new_list(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    core.replace_roster(conn, team_id, [{"number": "9", "name": "Sidney Crosby"}])
    core.replace_roster(conn, team_id, [{"number": "4", "name": "Bobby Orr"}])
    roster = core.list_roster(conn, team_id)
    assert len(roster) == 1
    assert roster[0]["number"] == "4"


def test_roster_is_scoped_to_its_team(conn, division_id):
    team1 = core.add_team(conn, division_id, "Avalanche")
    team2 = core.add_team(conn, division_id, "Wild")
    core.replace_roster(conn, team1, [{"number": "9", "name": "Sidney Crosby"}])
    core.replace_roster(conn, team2, [{"number": "4", "name": "Bobby Orr"}])
    assert len(core.list_roster(conn, team1)) == 1
    assert core.list_roster(conn, team1)[0]["name"] == "Sidney Crosby"
    assert len(core.list_roster(conn, team2)) == 1


def test_roster_reverts_to_unlinked_when_player_is_soft_deleted(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.replace_roster(conn, team_id, [{"number": "9", "name": "Sidney Crosby"}])
    entry = core.list_roster(conn, team_id)[0]
    core.link_roster_entry_to_player(conn, entry["id"], player_id)
    assert core.list_roster(conn, team_id)[0]["player_id"] == player_id

    core.soft_delete_player(conn, player_id)
    entry_after = core.list_roster(conn, team_id)[0]
    assert entry_after["player_id"] is None
    assert entry_after["name"] == "Sidney Crosby"  # falls back to the roster's own scanned name

    core.restore_player(conn, player_id)
    assert core.list_roster(conn, team_id)[0]["player_id"] == player_id


def test_update_roster_entry_renumbers(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    entry_id = core.add_roster_entry(conn, team_id, "TBD1", "Sidney Crosby")
    core.update_roster_entry(conn, entry_id, number="87")
    entry = core.list_roster(conn, team_id)[0]
    assert entry["number"] == "87"
    assert entry["name"] == "Sidney Crosby"


def test_update_roster_entry_renames(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    entry_id = core.add_roster_entry(conn, team_id, "9", "Sid Crosby")
    core.update_roster_entry(conn, entry_id, name="Sidney Crosby")
    entry = core.list_roster(conn, team_id)[0]
    assert entry["name"] == "Sidney Crosby"
    assert entry["number"] == "9"


def test_update_roster_entry_raises_on_number_collision(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    core.add_roster_entry(conn, team_id, "9", "Sidney Crosby")
    entry2_id = core.add_roster_entry(conn, team_id, "87", "Bobby Orr")
    try:
        core.update_roster_entry(conn, entry2_id, number="9")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "already taken" in str(e)
    # Unchanged on failure.
    assert next(r for r in core.list_roster(conn, team_id) if r["id"] == entry2_id)["number"] == "87"


def test_update_roster_entry_allows_keeping_its_own_number(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    entry_id = core.add_roster_entry(conn, team_id, "9", "Sidney Crosby")
    core.update_roster_entry(conn, entry_id, number="9", name="Sid Crosby")
    entry = core.list_roster(conn, team_id)[0]
    assert entry["number"] == "9"
    assert entry["name"] == "Sid Crosby"


def test_update_roster_entry_raises_when_not_found(conn):
    try:
        core.update_roster_entry(conn, 999999, number="9")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "not found" in str(e)


def test_remove_roster_entry(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    entry_id = core.add_roster_entry(conn, team_id, "9", "Sidney Crosby")
    core.remove_roster_entry(conn, entry_id)
    assert core.list_roster(conn, team_id) == []


def test_remove_roster_entry_does_not_delete_the_linked_player(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    entry_id = core.add_roster_entry(conn, team_id, "9", "Sidney Crosby", player_id=player_id)
    core.remove_roster_entry(conn, entry_id)
    assert core.list_roster(conn, team_id) == []
    assert core.get_player(conn, player_id) is not None


def test_remove_roster_entry_is_a_noop_for_an_unknown_id(conn):
    core.remove_roster_entry(conn, 999999)  # doesn't raise


def test_move_player_to_team_updates_roster_entry(conn, division_id):
    team1 = core.add_team(conn, division_id, "Avalanche")
    team2 = core.add_team(conn, division_id, "Wild")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.add_roster_entry(conn, team1, "9", "Sidney Crosby", player_id=player_id)

    core.move_player_to_team(conn, player_id, division_id, team2)
    assert core.list_roster(conn, team1) == []
    assert [r["player_id"] for r in core.list_roster(conn, team2)] == [player_id]


def test_move_player_to_team_records_a_note_only_when_given(conn, division_id):
    team1 = core.add_team(conn, division_id, "Avalanche")
    team2 = core.add_team(conn, division_id, "Wild")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.add_roster_entry(conn, team1, "9", "Sidney Crosby", player_id=player_id)

    core.move_player_to_team(conn, player_id, division_id, team2)
    assert core.list_player_move_notes(conn, player_id) == []

    core.move_player_to_team(conn, player_id, division_id, team1, note="Balancing rosters")
    notes = core.list_player_move_notes(conn, player_id)
    assert len(notes) == 1
    assert notes[0]["from_team"] == "Wild"
    assert notes[0]["to_team"] == "Avalanche"
    assert notes[0]["note"] == "Balancing rosters"


def test_move_player_to_team_is_a_noop_for_the_same_team(conn, division_id):
    team1 = core.add_team(conn, division_id, "Avalanche")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.add_roster_entry(conn, team1, "9", "Sidney Crosby", player_id=player_id)
    core.move_player_to_team(conn, player_id, division_id, team1, note="Shouldn't record")
    assert core.list_player_move_notes(conn, player_id) == []


def test_move_player_to_team_leaves_other_divisions_untouched(conn, division_id):
    other_division_id = core.add_division(conn, 2026, "Summer", "Chipmunk")
    team_a = core.add_team(conn, division_id, "Avalanche")
    team_b = core.add_team(conn, division_id, "Wild")
    other_team = core.add_team(conn, other_division_id, "Blackhawks")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.add_roster_entry(conn, team_a, "9", "Sidney Crosby", player_id=player_id)
    core.add_roster_entry(conn, other_team, "9", "Sidney Crosby", player_id=player_id)

    core.move_player_to_team(conn, player_id, division_id, team_b)

    # Moved within division_id...
    assert core.list_roster(conn, team_a) == []
    assert [r["player_id"] for r in core.list_roster(conn, team_b)] == [player_id]
    # ...but their roster spot in the other division is completely untouched.
    assert [r["player_id"] for r in core.list_roster(conn, other_team)] == [player_id]


def test_move_player_to_team_raises_when_not_rostered_in_division(conn, division_id):
    team1 = core.add_team(conn, division_id, "Avalanche")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    try:
        core.move_player_to_team(conn, player_id, division_id, team1)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "isn't on a roster" in str(e)


def test_move_player_to_team_raises_when_team_is_in_a_different_division(conn, division_id):
    other_division_id = core.add_division(conn, 2026, "Summer", "Chipmunk")
    team1 = core.add_team(conn, division_id, "Avalanche")
    other_team = core.add_team(conn, other_division_id, "Blackhawks")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.add_roster_entry(conn, team1, "9", "Sidney Crosby", player_id=player_id)
    try:
        core.move_player_to_team(conn, player_id, division_id, other_team)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "isn't in this division" in str(e)


def test_list_player_move_notes_scoped_to_a_division(conn, division_id):
    other_division_id = core.add_division(conn, 2026, "Summer", "Chipmunk")
    team1 = core.add_team(conn, division_id, "Avalanche")
    team2 = core.add_team(conn, division_id, "Wild")
    other_team1 = core.add_team(conn, other_division_id, "Blackhawks")
    other_team2 = core.add_team(conn, other_division_id, "Bruins")
    player_id = core.add_player(conn, "Sidney", "Crosby")
    core.add_roster_entry(conn, team1, "9", "Sidney Crosby", player_id=player_id)
    core.add_roster_entry(conn, other_team1, "9", "Sidney Crosby", player_id=player_id)

    core.move_player_to_team(conn, player_id, division_id, team2, note="Division A move")
    core.move_player_to_team(conn, player_id, other_division_id, other_team2, note="Division B move")

    assert len(core.list_player_move_notes(conn, player_id)) == 2
    scoped = core.list_player_move_notes(conn, player_id, division_id)
    assert len(scoped) == 1
    assert scoped[0]["note"] == "Division A move"


def test_roster_table_includes_coach_birthday_grade_and_requests(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    coach_id = core.add_coach(conn, "Jared", "Bednar", "Bed")
    core.assign_coach_to_team(conn, team_id, coach_id)
    other_division_id = core.add_division(conn, 2025, "Summer", "Penguin")

    sid = core.add_player(conn, "Sidney", "Crosby", birth_date="2019-08-07")
    wayne = core.add_player(conn, "Wayne", "Gretzky", birth_date="2019-01-26")
    core.add_roster_entry(conn, team_id, "87", "sidney crosby", player_id=sid)
    core.add_roster_entry(conn, team_id, "99", "wayne gretzky", player_id=wayne)
    core.add_evaluation(conn, sid, division_id, team_id, "A")
    core.add_evaluation(conn, wayne, other_division_id, None, "B")
    core.add_player_request(conn, sid, wayne)

    rows = {r["Number"]: r for r in core.roster_table(conn, division_id)}
    assert rows["87"] == {
        "Team": "Avalanche", "Coach": 'Jared Bednar "Bed"', "Number": "87", "Name": "Sidney Crosby",
        "Birthday": "2019-08-07", "Grade": "A", "Position": None, "Goalie": None,
        "Play-with Requests": "-> Wayne Gretzky", "Parent": None, "Parent Phone": None, "Parent Email": None,
    }
    assert rows["99"]["Grade"] == "B"  # same age group (Penguin): no asterisk
    assert rows["99"]["Play-with Requests"] == "-> Sidney Crosby"  # mutual


def test_roster_table_shows_position_and_flags_goalies(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    goalie = core.add_player(conn, "Marc", "Fleury", current_division_id=division_id)
    skater = core.add_player(conn, "Sidney", "Crosby", current_division_id=division_id)
    core.set_registration_position(conn, goalie, division_id, "Goalie")
    core.set_registration_position(conn, skater, division_id, "Forward or Defense")
    core.add_roster_entry(conn, team_id, "29", "Marc Fleury", player_id=goalie)
    core.add_roster_entry(conn, team_id, "87", "Sidney Crosby", player_id=skater)
    core.set_position(conn, skater, division_id, team_id, "Center")  # team position wins

    rows = {r["Number"]: r for r in core.roster_table(conn, division_id)}
    assert (rows["29"]["Position"], rows["29"]["Goalie"]) == ("Goalie", "Yes")
    assert (rows["87"]["Position"], rows["87"]["Goalie"]) == ("Center", None)


def test_roster_table_includes_parent_contact_unless_hidden(conn, division_id):
    team_id = core.add_team(conn, division_id, "Avalanche")
    kid = core.add_player(
        conn, "Sidney", "Crosby", current_division_id=division_id,
        contact_first_name="Troy", contact_last_name="Crosby",
        contact_phone="412-555-0187", contact_email="troy@example.com",
    )
    core.add_roster_entry(conn, team_id, "87", "Sidney Crosby", player_id=kid)

    [row] = core.roster_table(conn, division_id)
    assert (row["Parent"], row["Parent Phone"], row["Parent Email"]) == ("Troy Crosby", "412-555-0187", "troy@example.com")

    [hidden] = core.roster_table(conn, division_id, include_contacts=False)
    assert hidden["Parent"] == "Troy Crosby"
    assert "Parent Phone" not in hidden and "Parent Email" not in hidden
