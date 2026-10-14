"""
The explanation the browser shows must come from the engine, not from text.

Two guarantees:

1. ``/api/algorithms`` answers the SAME query ``/api/search`` answers -- one
   validator, one region, one budget source.
2. Every number the "How SplitRail decided" view and the PEAS document state
   (weights, thresholds, limits) is the number in ``src/config.py``. Change
   the config without updating the document and a test fails here, rather
   than the presentation quietly contradicting the code.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import web
from cli import build_system
from src import config
from src.explain import EXPLAIN_EXPANSIONS, EXPLAIN_SECONDS

DOCS = Path(__file__).resolve().parent.parent.parent / "docs"


@pytest.fixture(scope="module")
def web_system():
    system = build_system(use_pool=False)
    yield system
    system.close()


@pytest.fixture(scope="module")
def payload(web_system):
    lo, _ = web_system.store.get_run_date_range()
    return {"origin": "PGT", "destination": "KYJ", "date": lo, "travel_class": "",
            "max_transfers": 1, "mode": "same_train"}


# --------------------------------------------------------------------------- #
# /api/algorithms
# --------------------------------------------------------------------------- #
def test_algorithms_endpoint_returns_strict_json(web_system, payload):
    result = web.algorithms(web_system, payload)
    json.dumps(result, allow_nan=False)
    assert [r["algorithm"] for r in result["runs"]] == ["BFS", "UCS", "A*"]
    assert result["budget"] == {"expansions": EXPLAIN_EXPANSIONS, "seconds": EXPLAIN_SECONDS}


def test_algorithms_uses_the_search_validator(web_system, payload):
    """Same rules as /api/search: an unknown station is refused the same way."""
    with pytest.raises(ValueError):
        web.algorithms(web_system, {**payload, "origin": "ZZZZ"})
    with pytest.raises(ValueError):
        web.search(web_system, {**payload, "origin": "ZZZZ"})


def test_algorithms_rejects_an_unknown_agent(web_system, payload):
    with pytest.raises(ValueError, match="mode"):
        web.algorithms(web_system, {**payload, "mode": "everything"})


def test_algorithms_and_search_answer_the_same_query(web_system, payload):
    """The comparison is about the user's query, not a lookalike of it."""
    assert web.query_from_payload(web_system, payload) == web.query_from_payload(
        web_system, {k: v for k, v in payload.items() if k != "mode"})


# --------------------------------------------------------------------------- #
# The model block in /api/meta is config, verbatim
# --------------------------------------------------------------------------- #
def test_meta_model_reports_the_live_config(web_system):
    model = web.meta(web_system)["model"]
    assert model["weights"] == config.WEIGHTS
    assert model["min_seat_change_minutes"] == config.MIN_SEAT_CHANGE
    assert model["min_train_change_minutes"] == config.MIN_TRAIN_CHANGE
    assert model["agent_search_limit"] == {"expansions": config.MAX_SEARCH_EXPANSIONS,
                                           "seconds": config.SEARCH_SECONDS}
    assert model["comparison_search_limit"] == {"expansions": EXPLAIN_EXPANSIONS,
                                                "seconds": EXPLAIN_SECONDS}
    assert model["search_agents"] == ["SameTrainSearchAgent", "DifferentTrainSearchAgent"]


def test_search_response_scores_with_the_same_weights_meta_reports(web_system, payload):
    result = web.search(web_system, {k: v for k, v in payload.items() if k != "mode"})
    assert result["weights"] == web.meta(web_system)["model"]["weights"]


# --------------------------------------------------------------------------- #
# The documents cannot drift from the code
# --------------------------------------------------------------------------- #
PROJECT = Path(__file__).resolve().parent.parent


def test_every_module_the_build_step_names_exists():
    """'5 · The build' tells the examiner where each concept lives. If a file
    is renamed or moved, this fails instead of the page pointing at nothing."""
    source = (PROJECT / "web" / "explain.js").read_text(encoding="utf-8")
    block = re.search(r"const MODULES = \[(.*?)\];", source, re.S)
    assert block, "MODULES list not found in web/explain.js"
    paths = re.findall(r"\['([^']+)',", block.group(1))
    assert len(paths) >= 8
    missing = [p for p in paths if not (PROJECT / p).exists()]
    assert not missing, f"web/explain.js names files that do not exist: {missing}"


def test_explain_script_is_served():
    assert web.STATIC_FILES["/explain.js"][0] == "explain.js"
    assert (PROJECT / "web" / "explain.js").is_file()

def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def test_peas_document_states_the_configured_weights():
    """docs/peas.md spells out the scoring formula; it must match config."""
    text = (DOCS / "peas.md").read_text(encoding="utf-8")
    formula = re.search(r"```text\n(.*?)```", text, re.S)
    assert formula, "docs/peas.md no longer contains the scoring formula block"
    formula = formula.group(1)
    w = config.WEIGHTS
    expected = {
        f"{_number(w['time'])} × moving minutes": "time",
        f"{_number(w['transfer'])} × transfer penalty": "transfer",
        f"{_number(w['rac'])} × RAC legs": "rac",
        f"{_number(w['waitlist'])} × waitlist legs": "waitlist",
    }
    for phrase, weight in expected.items():
        assert phrase in formula, (
            f"docs/peas.md disagrees with config.WEIGHTS['{weight}']: expected '{phrase}'")
