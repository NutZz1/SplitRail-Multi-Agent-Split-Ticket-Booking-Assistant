"""Shared skip markers.

A clone of this repository carries the six-train demo subset, not the full
Indian Railways timetable: ``data_source/schedules_clean.json`` is ~65 MB and
is gitignored. A handful of tests assert figures that only exist with the
full network loaded -- 7,387 graph nodes, the Howrah Rajdhani, fixed
expansion counts.

Those tests used to FAIL on a fresh clone, which made a red suite the normal
state and hid real regressions in the noise. They are skipped instead, with a
reason that says exactly how to turn them on:

    python data_source/convert_schedules.py <raw schedules.json>
    python data_source/build_sqlite_db.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

#: The optional full timetable. Present -> the full-network tests run.
FULL_SCHEDULES = Path(__file__).resolve().parent.parent / "data_source" / "schedules_clean.json"

#: How many trains the full timetable has, versus six in the demo subset.
FULL_NETWORK_TRAINS = 5208

requires_full_network = pytest.mark.skipif(
    not FULL_SCHEDULES.is_file(),
    reason=(
        "needs the full timetable: data_source/schedules_clean.json is not present, "
        "so railway.db holds only the six demo trains. Build it with "
        "`python data_source/convert_schedules.py <raw>` then "
        "`python data_source/build_sqlite_db.py`."
    ),
)
