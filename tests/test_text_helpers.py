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
