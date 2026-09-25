import game_sheet_core as core


def test_normalize_text_trims_and_lowercases():
    assert core.normalize_text("  Connor McDavid  ") == "connor mcdavid"
    assert core.normalize_text(None) is None
    assert core.normalize_text("") == ""


def test_display_text_title_cases():
    assert core.display_text("avalanche") == "Avalanche"
    assert core.display_text("connor crosby") == "Connor Crosby"
    assert core.display_text(None) is None
    assert core.display_text("") == ""


def test_display_text_fixes_mc_surname_prefix():
    # str.title() alone gets this wrong: "mcdavid".title() == "Mcdavid".
    assert core.display_text("mcdavid") == "McDavid"
    assert core.display_text("connor mcdavid") == "Connor McDavid"
    assert core.display_text("mcdonald") == "McDonald"
    assert core.display_text("mcgregor-smith") == "McGregor-Smith"


def test_display_text_leaves_mac_alone_to_avoid_worse_guesses():
    # "Mac" isn't corrected -- unlike "Mc", it's also the start of plenty
    # of ordinary words/names where forcing a capital would be wrong.
    assert core.display_text("macy") == "Macy"
    assert core.display_text("mack") == "Mack"
    assert core.display_text("macdonald") == "Macdonald"


def test_display_text_handles_apostrophes_and_hyphens_via_title():
    assert core.display_text("o'brien") == "O'Brien"
    assert core.display_text("smith-jones") == "Smith-Jones"


def test_normalize_then_display_round_trips_a_mc_name():
    stored = core.normalize_text("Connor McDavid")
    assert core.display_text(stored) == "Connor McDavid"


def test_normalize_date_handles_plain_iso_date():
    assert core.normalize_date("2015-08-11") == "2015-08-11"


def test_normalize_date_strips_a_spreadsheet_timestamps_time_component():
    # A date cell from pandas/openpyxl often round-trips as a full
    # timestamp string even though only the date matters (birth dates, an
    # imported schedule's date column) -- this used to fail to parse at
    # all and fall through to the untouched (wrong) string.
    assert core.normalize_date("2015-08-11 00:00:00") == "2015-08-11"
    assert core.normalize_date("2015-08-11T00:00:00") == "2015-08-11"


def test_normalize_date_still_handles_slash_and_dot_formats():
    assert core.normalize_date("7/14/26") == "2026-07-14"
    assert core.normalize_date("07.14.2026") == "2026-07-14"


def test_normalize_date_falls_back_to_original_for_unrecognized_text():
    assert core.normalize_date("sometime in July") == "sometime in July"


def test_normalize_date_passes_through_none_and_empty():
    assert core.normalize_date(None) is None
    assert core.normalize_date("") == ""
