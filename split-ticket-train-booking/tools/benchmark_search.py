"""Compare identical candidate search and ranking, sequentially and in processes.

Uses a temporary booking ledger. Startup is reported separately; medians are
five warm searches per mode. Timing is evidence, not a pass/fail threshold.
"""
import datetime as dt
import json
from pathlib import Path
import statistics
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.baseline_single_agent import resolve_single_agent
from src.demo_scenarios import build_system, ensure_database, resolve_timed
from src.state import UserQuery


def signature(rec):
    return [(r.candidate.signature, round(r.final_score, 6)) for r in rec.ranked_options]


def main():
    database = ensure_database()
    with tempfile.TemporaryDirectory(prefix='splitrail-benchmark-') as directory:
        system = build_system(database, use_pool=True, bookings_path=Path(directory)/'bookings.db')
        try:
            day = dt.date.fromisoformat(system.store.get_window()[0])
            results = []
            for origin, destination in [('KYJ', 'PGT'), ('SBC', 'MAS')]:
                q = UserQuery(origin, destination, day, max_transfers=1)
                resolve_single_agent(q, system.store, system.heuristic)
                resolve_timed(system, q)
                sequential, pooled = [], []
                for _ in range(5):
                    a, secs = resolve_single_agent(q, system.store, system.heuristic)
                    sequential.append(secs)
                    b, secs = resolve_timed(system, q)
                    pooled.append(secs)
                    assert signature(a) == signature(b), 'Benchmark modes returned different ranked candidates'
                seq, par = statistics.median(sequential), statistics.median(pooled)
                results.append(dict(route=f'{origin} → {destination}', sequential_seconds=round(seq,4),
                                    pooled_seconds=round(par,4), speedup=round(seq/par,3),
                                    candidates=len(a.ranked_options), equal_results=True))
            print(json.dumps({'date':str(day),'startup_seconds':round(system.setup_seconds,3),
                              'samples_per_mode':5,'results':results}, indent=2))
        finally:
            system.close()


if __name__ == '__main__':
    main()
