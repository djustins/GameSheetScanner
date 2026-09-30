import pytest

import game_sheet_core as core


def _rostered(conn, division_id, team_id, first, grade=None, birth_date=None, **kwargs):
    player_id = core.add_player(conn, first, "Test", birth_date=birth_date, current_division_id=division_id, **kwargs)
    if grade:
        core.add_evaluation(conn, player_id, division_id, None, grade)
    core.add_roster_entry(conn, team_id, str(player_id), f"{first} Test", player_id=player_id)
    return player_id


def _team_of(conn, division_id, player_id):
    return next(
        t["id"] for t in core.list_teams(conn, division_id)
        if player_id in {e["player_id"] for e in core.list_roster(conn, t["id"])}
    )


def test_trade_swaps_players_and_logs_it(conn, division_id):
    avs = core.add_team(conn, division_id, "Avalanche")
    wild = core.add_team(conn, division_id, "Wild")
    a = _rostered(conn, division_id, avs, "Alpha", grade="A")
    b = _rostered(conn, division_id, wild, "Bravo", grade="B")
    c = _rostered(conn, division_id, wild, "Charlie", grade="C")

    core.trade_players(conn, division_id, avs, [a], wild, [b, c], note="rebalance")
    assert _team_of(conn, division_id, a) == wild
    assert _team_of(conn, division_id, b) == avs and _team_of(conn, division_id, c) == avs
    [note] = core.list_player_move_notes(conn, a, division_id)
    assert note["note"] == "Trade Avalanche ↔ Wild: Alpha Test for Bravo Test, Charlie Test — rebalance"


def test_team_balance_previews_a_trade_without_changing_anything(conn, division_id):
    avs = core.add_team(conn, division_id, "Avalanche")
    wild = core.add_team(conn, division_id, "Wild")
    a = _rostered(conn, division_id, avs, "Alpha", grade="A", birth_date="2017-01-01")
    _rostered(conn, division_id, wild, "Delta", grade="D", birth_date="2018-01-01")
    core.set_registration_position(conn, a, division_id, "Goalie")

    now = core.team_balance(conn, division_id)
    after = core.team_balance(conn, division_id, {a: wild})
    assert (now[avs]["players"], now[avs]["skill"], now[avs]["goalies"]) == (1, 4, 1)
    assert (after[avs]["players"], after[wild]["players"], after[wild]["skill"], after[wild]["goalies"]) == (0, 2, 5, 1)
    assert _team_of(conn, division_id, a) == avs  # preview only


def test_trade_effects_list_requests_joined_and_split(conn, division_id):
    avs = core.add_team(conn, division_id, "Avalanche")
    wild = core.add_team(conn, division_id, "Wild")
    a = _rostered(conn, division_id, avs, "Alpha")
    b = _rostered(conn, division_id, avs, "Bravo")
    c = _rostered(conn, division_id, wild, "Charlie")
    core.add_player_request(conn, a, b)  # together now; trading a away splits them
    core.add_player_request(conn, a, c)  # apart now; trading a joins them

    effects = core.trade_effects(conn, division_id, {a: wild})
    assert {(e["other_name"] if e["name"] == "Alpha Test" else e["name"], e["joined"], e["kind"]) for e in effects} == {
        ("Bravo Test", False, "soft"), ("Charlie Test", True, "soft"),
    }


def test_trade_refuses_to_split_siblings_or_hard_requests_unless_told(conn, division_id):
    avs = core.add_team(conn, division_id, "Avalanche")
    wild = core.add_team(conn, division_id, "Wild")
    kid1 = _rostered(conn, division_id, avs, "Kid1", contact_email="parent@example.com")
    _rostered(conn, division_id, avs, "Kid2", contact_email="parent@example.com")
    other = _rostered(conn, division_id, wild, "Other")

    with pytest.raises(ValueError, match="sibling"):
        core.trade_players(conn, division_id, avs, [kid1], wild, [other])
    assert _team_of(conn, division_id, kid1) == avs

    core.trade_players(conn, division_id, avs, [kid1], wild, [other], allow_split=True)
    assert _team_of(conn, division_id, kid1) == wild


def test_trade_rejects_a_player_not_on_the_named_team(conn, division_id):
    avs = core.add_team(conn, division_id, "Avalanche")
    wild = core.add_team(conn, division_id, "Wild")
    a = _rostered(conn, division_id, avs, "Alpha")
    with pytest.raises(ValueError, match="isn't on Wild"):
        core.trade_players(conn, division_id, avs, [], wild, [a])


def test_trade_refuses_to_put_a_do_not_play_with_pair_together(conn, division_id):
    avs = core.add_team(conn, division_id, "Avalanche")
    wild = core.add_team(conn, division_id, "Wild")
    a = _rostered(conn, division_id, avs, "Alpha")
    b = _rostered(conn, division_id, wild, "Bravo")
    core.add_player_request(conn, a, b, avoid=True)

    effects = core.trade_effects(conn, division_id, {a: wild})
    assert [(e["kind"], e["joined"]) for e in effects] == [("avoid", True)]
    assert core.trade_blockers(effects) == effects
    with pytest.raises(ValueError, match="do not play with"):
        core.trade_players(conn, division_id, avs, [a], wild, [])
    core.trade_players(conn, division_id, avs, [a], wild, [], allow_split=True)
    assert _team_of(conn, division_id, a) == wild
