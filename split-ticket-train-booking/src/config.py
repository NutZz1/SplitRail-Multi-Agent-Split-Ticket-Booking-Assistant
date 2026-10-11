"""Central simulation/search policy. Environment overrides are validated at startup."""
import math
import os


def number(name: str, default: float) -> float:
    value = float(os.environ.get('SPLITRAIL_' + name, default))
    if not math.isfinite(value) or value < 0:
        raise ValueError(f'SPLITRAIL_{name} must be finite and non-negative')
    return value


MIN_SEAT_CHANGE = number('MIN_SEAT_CHANGE', 5)
MIN_TRAIN_CHANGE = number('MIN_TRAIN_CHANGE', 20)
MAX_SHORT_WALK = 4
MAX_JOURNEY_DAYS = 4
MAX_SEARCH_EXPANSIONS = 6000
SEARCH_SECONDS = 15.0
SEAT_CANDIDATES = 48
INVENTORY_VERSION = 'segment-v2'
INVENTORY_SEED = 'splitrail-2026-physical-seats-v1'
DEFAULT_INVENTORY_PROFILE = os.environ.get('SPLITRAIL_INVENTORY_PROFILE', 'busy-v2')
if DEFAULT_INVENTORY_PROFILE not in ('standard-v1', 'busy-v2'):
    raise ValueError('SPLITRAIL_INVENTORY_PROFILE must be standard-v1 or busy-v2')
SEARCH_REGION = os.environ.get('SPLITRAIL_REGION', 'Kerala')
NETWORK_PROFILE = os.environ.get('SPLITRAIL_NETWORK_PROFILE', 'kerala-v1')
if SEARCH_REGION not in ('Kerala', 'ALL') or NETWORK_PROFILE not in ('kerala-v1', 'legacy'):
    raise ValueError('Invalid SplitRail region or network profile')
WEIGHTS = {key: number('W_' + key.upper(), default) for key, default in
           dict(time=2., fare=1., transfer=2., layover=1., rac=40., waitlist=110.).items()}
