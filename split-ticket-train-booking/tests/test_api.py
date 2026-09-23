"""
API-layer tests via the ASGI test client (no server, no network).

Deliberately small: the engine is covered by its own suites, so these only
check the boundary -- validation, serialization completeness, and that the
demo scenarios round-trip through HTTP with the same results as cli.py.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx2")

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:   # runs the lifespan: engine built once for this module
        yield c


def test_health(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["graph_nodes"] == 7387 and h["graph_edges"] == 24715
    assert h["run_date_range"] == ["2026-09-10", "2026-09-25"]


def test_station_autocomplete(client):
    hits = client.get("/api/stations", params={"query": "chennai"}).json()
    assert {"code", "name", "state"} <= set(hits[0])
    assert any(s["code"] == "MAS" for s in hits)
    assert client.get("/api/stations", params={"query": "sbc"}).json()[0]["code"] == "SBC"
    assert client.get("/api/stations", params={"query": ""}).json() == []
    assert len(client.get("/api/stations", params={"query": "a", "limit": 7}).json()) == 7


def test_demo_trains_match_the_store(client, store):
    trains = client.get("/api/demo-trains").json()
    assert [t["number"] for t in trains] == store.get_demo_train_numbers()
    assert next(t for t in trains if t["number"] == "12217")["runs_on"] == ["TUE", "FRI"]


def test_demo_scenarios_are_the_cli_scenarios(client):
    import cli
    scenarios = client.get("/api/demo-scenarios").json()
    assert len(scenarios) == len(cli.DEMO_SCENARIOS) == 4
    for api_sc, (label, query, _) in zip(scenarios, cli.DEMO_SCENARIOS):
        assert label.endswith(api_sc["name"])
        r = api_sc["request"]
        assert (r["origin_station"], r["destination_station"], r["travel_date"]) == (
            query.origin_station, query.destination_station, query.travel_date.isoformat())


def test_search_puri_cdg_is_complete_and_matches_the_engine(client):
    body = client.get("/api/demo-scenarios").json()[1]["request"]
    r = client.post("/api/search", json=body)
    assert r.status_code == 200
    res = r.json()
    assert res["found"] and res["failure_reason"] is None
    best = res["ranked_options"][0]
    assert best["position"] == 1 and best["candidate"]["agent_name"] == "DifferentTrainSearchAgent"
    legs = best["candidate"]["tickets"]
    assert [(t["train_number"], t["from_station"], t["to_station"], t["status"]) for t in legs] == [
        ("12801", "PURI", "NDLS", "RAC"), ("12217", "NDLS", "CDG", "WAITLIST")]
    assert legs[0]["is_risky"] and best["candidate"]["is_risky"]
    assert best["fare_time_score"]["total_fare"] == pytest.approx(1060.28, abs=0.01)
    assert best["fare_time_score"]["layover_minutes"] == pytest.approx(533)
    assert best["transfer_score"]["per_transfer_penalties"][0]["buffer_minutes"] == 400
    # 5597.28 four-term score + W_RISK (60) x 2 risky legs = 5717.28
    assert best["candidate"]["risky_leg_count"] == 2
    assert best["final_score"] == pytest.approx(5717.28, abs=0.01)
    assert {"W_TIME", "W_FARE", "W_TRANSFER", "W_LAYOVER", "W_RISK"} == set(res["weights"])
    assert res["resolve_seconds"] > 0
    assert [e["agent_name"] for e in res["search_effort_summary"]] == ["SameTrainSearchAgent", "DifferentTrainSearchAgent"]


def test_search_sbc_mas_returns_both_candidates(client):
    body = client.get("/api/demo-scenarios").json()[0]["request"]
    res = client.post("/api/search", json=body).json()
    assert [o["candidate"]["transfer_count"] for o in res["ranked_options"]] == [0, 1]
    assert res["duplicate_candidates_merged"] == 1


def test_search_no_solution_carries_reason_and_effort(client):
    body = client.get("/api/demo-scenarios").json()[2]["request"]
    res = client.post("/api/search", json=body).json()
    assert res["found"] is False and res["ranked_options"] == []
    assert "CC" in res["failure_reason"]
    assert all(e["nodes_expanded"] >= 1 and not e["found"] for e in res["search_effort_summary"])


@pytest.mark.parametrize("patch,expect", [
    ({"travel_date": "2026-10-01"}, "outside the demo window"),
    ({"travel_date": "2026-09-09"}, "outside the demo window"),
    ({"origin_station": "ZZZZ"}, "not a station code"),
    ({"travel_class_preference": "FIRST"}, "not a class code"),
    ({"travel_class_preference": None, "class_is_hard_constraint": True}, "needs a class preference"),
])
def test_bad_input_is_a_clean_400(client, patch, expect):
    body = {**client.get("/api/demo-scenarios").json()[0]["request"], **patch}
    r = client.post("/api/search", json=body)
    assert r.status_code == 400
    assert expect in r.json()["detail"]


def test_malformed_body_is_422(client):
    r = client.post("/api/search", json={"origin_station": "SBC"})
    assert r.status_code == 422
