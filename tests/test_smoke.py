import game_sheet_core as core


def test_conn_fixture_is_clean(conn):
    assert core.list_divisions(conn) == []


def test_division_fixture_creates_a_division(conn, division_id):
    divisions = core.list_divisions(conn)
    assert len(divisions) == 1
    assert divisions[0]["id"] == division_id
    assert divisions[0]["year"] == 2026
    assert divisions[0]["season"] == "Summer"
    assert divisions[0]["age_group"] == "Penguin"


def test_truncation_between_tests_actually_isolates(conn):
    # If the previous test's division leaked through, this would see it.
    assert core.list_divisions(conn) == []
