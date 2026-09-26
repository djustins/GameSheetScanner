import game_sheet_core as core


def test_add_player_auto_links_parent_by_contact_info(conn):
    p1 = core.add_player(
        conn, "Sidney", "Crosby",
        contact_first_name="Troy", contact_last_name="Crosby",
        contact_phone="412-555-0100", contact_email="troy@example.com",
    )
    p2 = core.add_player(
        conn, "Taylor", "Crosby",
        contact_first_name="Troy", contact_last_name="Crosby",
        contact_phone="412-555-0100", contact_email="troy@example.com",
    )
    players = {p["id"]: p for p in core.list_players(conn)}
    assert players[p1]["parent_id"] is not None
    assert players[p1]["parent_id"] == players[p2]["parent_id"]
    assert len(core.list_parents(conn)) == 1


def test_get_or_create_parent_matches_by_email_regardless_of_case(conn):
    a = core.get_or_create_parent(conn, "Troy", "Crosby", None, "Troy@Example.com")
    b = core.get_or_create_parent(conn, None, None, None, "troy@example.com")
    assert a == b
    assert len(core.list_parents(conn)) == 1


def test_get_or_create_parent_matches_by_phone_regardless_of_formatting(conn):
    a = core.get_or_create_parent(conn, "Troy", "Crosby", "(412) 555-0100", None)
    b = core.get_or_create_parent(conn, None, None, "412-555-0100", None)
    assert a == b
    assert len(core.list_parents(conn)) == 1


def test_get_or_create_parent_matches_by_name_when_no_phone_or_email(conn):
    a = core.get_or_create_parent(conn, "Troy", "Crosby", None, None)
    b = core.get_or_create_parent(conn, "troy", "crosby", None, None)
    assert a == b


def test_get_or_create_parent_returns_none_with_no_identifying_info(conn):
    assert core.get_or_create_parent(conn, None, None, None, None) is None
    assert core.list_parents(conn) == []


def test_get_or_create_parent_creates_distinct_parents_for_distinct_contacts(conn):
    a = core.get_or_create_parent(conn, "Troy", "Crosby", None, "troy@example.com")
    b = core.get_or_create_parent(conn, "Mario", "Lemieux", None, "mario@example.com")
    assert a != b
    assert len(core.list_parents(conn)) == 2


def test_list_siblings(conn):
    p1 = core.add_player(
        conn, "Sidney", "Crosby",
        contact_first_name="Troy", contact_last_name="Crosby", contact_email="troy@example.com",
    )
    p2 = core.add_player(
        conn, "Taylor", "Crosby",
        contact_first_name="Troy", contact_last_name="Crosby", contact_email="troy@example.com",
    )
    other = core.add_player(conn, "Mario", "Lemieux")

    siblings = core.list_siblings(conn, p1)
    assert [s["id"] for s in siblings] == [p2]
    assert core.list_siblings(conn, other) == []


def test_set_player_parent_links_and_unlinks(conn):
    p1 = core.add_player(conn, "Sidney", "Crosby")
    p2 = core.add_player(conn, "Taylor", "Crosby")
    assert core.list_siblings(conn, p1) == []

    parent_id = core.get_or_create_parent(conn, "Troy", "Crosby", None, None)
    core.set_player_parent(conn, p1, parent_id)
    core.set_player_parent(conn, p2, parent_id)
    assert [s["id"] for s in core.list_siblings(conn, p1)] == [p2]

    core.set_player_parent(conn, p2, None)
    assert core.list_siblings(conn, p1) == []


def test_update_player_re_derives_parent_using_existing_contact_fields(conn):
    # Editing only contact_phone still re-derives parent_id using the name
    # already on file (merged in from the player's current record) -- it
    # re-matches the *same* parent by name rather than losing the link or
    # spuriously creating a new one just because the phone no longer
    # matches that parent's original phone.
    p1 = core.add_player(
        conn, "Sidney", "Crosby",
        contact_first_name="Troy", contact_last_name="Crosby", contact_phone="412-555-0100",
    )
    original_parent = next(p for p in core.list_players(conn) if p["id"] == p1)["parent_id"]
    assert original_parent is not None

    core.update_player(conn, p1, contact_phone="412-555-0199")
    updated = next(p for p in core.list_players(conn) if p["id"] == p1)
    assert updated["parent_id"] == original_parent
    assert len(core.list_parents(conn)) == 1

    # Changing the name to someone unrelated, though, does re-derive onto
    # a different (new) parent.
    core.update_player(conn, p1, contact_first_name="Mario", contact_last_name="Lemieux")
    changed = next(p for p in core.list_players(conn) if p["id"] == p1)
    assert changed["parent_id"] != original_parent
    assert len(core.list_parents(conn)) == 2


def test_backfill_player_parents_links_existing_players_and_is_idempotent(conn):
    cur = conn.execute(
        "INSERT INTO players (first_name, last_name, contact_first_name, contact_last_name, contact_email) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        ("Sidney", "Crosby", "Troy", "Crosby", "troy@example.com"),
    )
    player_id = cur.fetchone()[0]
    conn.commit()

    linked = core.backfill_player_parents(conn)
    assert linked == 1
    player = next(p for p in core.list_players(conn) if p["id"] == player_id)
    assert player["parent_id"] is not None

    assert core.backfill_player_parents(conn) == 0
