"""
Tests for cli.py: the scripted demo scenarios and the input validation,
called as functions (no shelling out, no stdout parsing).
"""

from __future__ import annotations

import datetime as dt

import pytest

import cli
from src.state import UserQuery


@pytest.fixture(scope="module")
def system(store):
    """In-process system (no worker pool) built once for this module."""
    sys_ = cli.build_system(store.db_path, use_pool=False)
    yield sys_
    sys_.close()


# --------------------------------------------------------------------------- #
# demo scenarios
# --------------------------------------------------------------------------- #
def test_demo_has_four_scenarios():
    assert len(cli.demo_labels()) == 4
    labels = [label for label, _, _ in cli.demo_labels()]
    assert labels[0].startswith("1.") and labels[3].startswith("4.")


def test_demo_scenario_1_sbc_mas(system):
    label, query, _ = cli.demo_labels()[0]
    rec, secs = cli.resolve_timed(system, query)
    assert rec.found
    transfers = sorted(o.candidate.transfer_count for o in rec.ranked_options)
    assert transfers == [0, 1]                                  # direct + BNC split
    assert rec.best.candidate.transfer_count == 0               # direct ranked first
    assert rec.best.candidate.agent_name == "SameTrainSearchAgent"
    assert rec.ranked_options[1].candidate.split_stations == ("BNC",)
    assert all(o.fare_time_score.moving_time_minutes == 340 for o in rec.ranked_options)
    print(f"\n{label}: {secs * 1000:.0f} ms")


def test_demo_scenario_2_puri_cdg(system):
    label, query, _ = cli.demo_labels()[1]
    rec, secs = cli.resolve_timed(system, query)
    assert rec.found
    assert rec.best.candidate.agent_name == "DifferentTrainSearchAgent"
    assert rec.best.candidate.signature == (("12801", "PURI", "NDLS"), ("12217", "NDLS", "CDG"))
    assert secs < 5.0
    print(f"\n{label}: {secs * 1000:.0f} ms")


def test_demo_scenario_3_hard_class_no_solution(system):
    label, query, _ = cli.demo_labels()[2]
    rec, secs = cli.resolve_timed(system, query)
    assert not rec.found and rec.ranked_options == ()
    assert "CC" in rec.failure_reason
    assert "SameTrainSearchAgent" in rec.failure_reason and "DifferentTrainSearchAgent" in rec.failure_reason
    print(f"\n{label}: {secs * 1000:.0f} ms")


def test_demo_scenario_4_wrong_day(system):
    label, query, _ = cli.demo_labels()[3]
    assert query.travel_date.strftime("%a") == "Wed"
    assert not system.store.runs_on_date("12217", query.travel_date.isoformat())
    rec, secs = cli.resolve_timed(system, query)
    # 12801 still runs on Wednesday, but its only connection (12217) does not:
    # the correct outcome is a clean found=False, not a crash or a bogus route.
    assert not rec.found and rec.ranked_options == ()
    assert "12801" in rec.failure_reason and "no connecting train" in rec.failure_reason
    print(f"\n{label}: {secs * 1000:.0f} ms")


def test_run_demo_returns_all_scenarios_in_order(system):
    results = cli.run_demo(system)
    assert [label for label, _, _ in results] == [label for label, _, _ in cli.demo_labels()]
    assert [rec.found for _, rec, _ in results] == [True, True, False, False]
    for _, rec, secs in results:
        assert secs < 5.0
        assert cli.format_recommendation(rec, secs)  # renders without error


def test_format_recommendation_marks_the_winner_and_shows_every_option(system):
    rec, secs = cli.resolve_timed(system, cli.demo_labels()[0][1])
    text = cli.format_recommendation(rec, secs)
    assert "RECOMMENDED" in text and text.count("RECOMMENDED") == 1
    assert "#1" in text and "#2" in text
    assert "transfer at BNC" in text and "night yes" in text and "coach distance" in text
    assert "resolve() took" in text
    failed, secs2 = cli.resolve_timed(system, cli.demo_labels()[2][1])
    text2 = cli.format_recommendation(failed, secs2)
    assert "NO ITINERARY FOUND" in text2 and "candidates considered" in text2


# --------------------------------------------------------------------------- #
# discoverability
# --------------------------------------------------------------------------- #
def test_demo_train_listing_comes_from_the_database(store):
    text = cli.format_demo_trains(store)
    for tn in store.get_demo_train_numbers():
        assert tn in text
    assert "12658" in text and "daily" in text and "TUE/FRI" in text
    assert "2026-09-10" in text and "2026-09-19" in text


# --------------------------------------------------------------------------- #
# input validation at the CLI boundary
# --------------------------------------------------------------------------- #
def test_parse_date_accepts_window_and_rejects_outside(store):
    assert cli.parse_date("2026-09-16", store) == dt.date(2026, 9, 16)
    assert cli.parse_date(" 2026-09-10 ", store) == dt.date(2026, 9, 10)
    assert cli.parse_date("2026-09-19", store) == dt.date(2026, 9, 19)
    for bad in ("2026-09-09", "2026-09-20", "2025-09-16", "2026-10-01"):
        with pytest.raises(cli.InputError, match="outside the 10-day booking window"):
            cli.parse_date(bad, store)


def test_parse_date_rejects_malformed(store):
    for bad in ("16/09/2026", "2026-9-16x", "tomorrow", ""):
        with pytest.raises(cli.InputError, match="YYYY-MM-DD"):
            cli.parse_date(bad, store)


def test_validate_station(store):
    assert cli.validate_station("sbc", store) == "SBC"
    assert cli.validate_station(" MAS ", store) == "MAS"
    with pytest.raises(cli.InputError, match="not a station code"):
        cli.validate_station("ZZZZZ", store)
    with pytest.raises(cli.InputError, match="empty"):
        cli.validate_station("   ", store)


def test_parse_class():
    assert cli.parse_class("") is None
    assert cli.parse_class("sl") == "SL"
    assert cli.parse_class(" 3A ") == "3A"
    with pytest.raises(cli.InputError, match="Valid:"):
        cli.parse_class("FIRST")


def test_parse_int():
    assert cli.parse_int("", 2, 0, "max transfers") == 2
    assert cli.parse_int("0", 2, 0, "max transfers") == 0
    with pytest.raises(cli.InputError):
        cli.parse_int("-1", 2, 0, "max transfers")
    with pytest.raises(cli.InputError):
        cli.parse_int("two", 2, 0, "max transfers")


def test_ask_reprompts_until_valid(monkeypatch, store):
    answers = iter(["nope", "list", "sbc"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    printed = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(" ".join(str(x) for x in a)))
    result = cli.ask("origin: ", lambda s: cli.validate_station(s, store), allow_list=store)
    assert result == "SBC"
    assert any("not a station code" in p for p in printed)      # the rejection message
    assert any("12658" in p for p in printed)                   # 'list' showed the demo trains


def test_prompt_query_builds_a_user_query(monkeypatch, store):
    answers = iter(["SBC", "MAS", "2026-09-16", "3A", "y", "1", "2"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)
    q = cli.prompt_query(store)
    assert q == UserQuery("SBC", "MAS", dt.date(2026, 9, 16), "3A", True, 1, 2)

