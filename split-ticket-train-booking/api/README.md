# Web API + React UI

Two layers on top of the existing, unmodified engine:

```
React (client/)  -->  FastAPI (api/)  -->  CoordinatorAgent + agents  -->  railway.db (read-only)
```

FastAPI is the only backend. It serves the JSON API and, once the React app
is built, the static files too -- so the whole system runs as **one Python
process**. There is no database other than `railway.db`, and no auth,
accounts, booking, or persistence.

All commands below run from the project root (`split-ticket-train-booking/`)
with the venv active.

## Install

```powershell
pip install -r api/requirements.txt          # engine deps + fastapi + uvicorn
cd client; npm install; cd ..                # once, for the React build / dev server
```

## Run mode A -- single process (what to run on stage)

```powershell
cd client; npm run build; cd ..              # produces client/dist (~160 kB); repeat after UI changes
uvicorn api.main:app --port 8000
```

Open http://localhost:8000. FastAPI serves `client/dist` at `/` and the API
under `/api/...`; the browser calls relative `/api/...` paths, same origin,
no CORS involved. Startup builds the station graph + heuristic once
(~1-2 s; logged as "Ready in N s"). Interactive API docs: http://localhost:8000/docs.

Set `SPLIT_TICKET_POOL=1` to run searches in the 2-process worker pool
(adds ~1 s startup; searches on the demo queries take 40-250 ms either way).

## Run mode B -- development, two terminals

```powershell
# terminal 1: API with auto-reload (API only; "/" just says the client isn't built)
uvicorn api.main:app --reload --port 8000

# terminal 2: Vite dev server with hot reload
cd client; npm run dev                       # http://localhost:5173
```

`client/.env.development` sets `VITE_API_BASE=http://localhost:8000`, so the
dev UI calls the API cross-origin; `api/main.py` allows exactly the origins
`http://localhost:5173` and `http://127.0.0.1:5173` (no wildcard). In a
production build `VITE_API_BASE` is unset and paths are relative.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | status, setup time, graph size, run-date window |
| GET | `/api/stations?query=chen` | autocomplete: code/name substring, exact code first, max 50 |
| GET | `/api/demo-trains` | the 6 trains with real availability / run-date data (same data as `cli.py --list-trains`) |
| GET | `/api/demo-scenarios` | the 4 scripted scenarios of `cli.py --demo`, as ready-to-POST requests |
| POST | `/api/search` | validate (HTTP 400 on bad station / date / class), then `CoordinatorAgent.resolve()`; returns the complete `FinalRecommendation` |

`POST /api/search` body mirrors `UserQuery`:

```json
{"origin_station": "PURI", "destination_station": "CDG", "travel_date": "2026-09-15",
 "travel_class_preference": null, "class_is_hard_constraint": false, "max_transfers": 2, "passenger_count": 1}
```

The response is not trimmed for display: every ranked option carries its
full ticket sequence (with per-leg RAC/WAITLIST/CONFIRMED status), both
score objects with the per-transfer penalty breakdown, `final_score`, the
`search_effort_summary` per Stage-1 agent, `resolve_seconds`, and the
coordinator's weights. Shaping for display happens in the React components.

## Shared code

Station/date/class validation, the demo-train listing, the four scenarios
and system construction live in `src/demo_scenarios.py` and are imported by
both `cli.py` and `api/main.py` -- one source of truth. Serialization of the
engine's dataclasses is in `api/schemas.py` (Pydantic models with
`from_domain` constructors), nowhere else.

## Notes

* Endpoints that touch `RailDataStore` are `async def` deliberately: the
  store's sqlite3 connection is bound to the event-loop thread, and FastAPI
  would run a plain `def` endpoint in a threadpool.
* Exceptions inside the agents are not caught by the API (HTTP 500 with a
  traceback in the server log) -- a genuine bug should be visible.
* `client/dist` and `client/node_modules` are git-ignored; rebuild after
  cloning.
