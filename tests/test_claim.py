"""Proves the single-owner invariant (MVP §0.1) on the pure claim core."""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from kontinuum.claim import ClaimEntry, Kind, plan_claim, resolve_owner, won_claim

NOW = datetime(2026, 8, 13, 12, 0, 0)
LIVE = NOW + timedelta(hours=1)
DEAD = NOW - timedelta(minutes=1)


# --- resolve_owner: one rule per test -------------------------------------------------

def test_expired_lease_frees_the_issue():
    log = [ClaimEntry(0, Kind.CLAIM, "i0", 1, DEAD)]
    assert resolve_owner(log, NOW) is None


def test_live_claim_is_owned():
    log = [ClaimEntry(0, Kind.CLAIM, "i0", 1, LIVE)]
    assert resolve_owner(log, NOW) == "i0"


def test_higher_epoch_supersedes_stale_owner():
    log = [
        ClaimEntry(0, Kind.CLAIM, "i0", 1, LIVE),   # dead owner's stale-but-unexpired claim
        ClaimEntry(1, Kind.CLAIM, "i1", 2, LIVE),   # reclaim at epoch+1
    ]
    assert resolve_owner(log, NOW) == "i1"


def test_matching_release_frees_the_issue():
    log = [
        ClaimEntry(0, Kind.CLAIM, "i0", 1, LIVE),
        ClaimEntry(1, Kind.RELEASE, "i0", 1, LIVE),
    ]
    assert resolve_owner(log, NOW) is None


def test_tie_loser_release_preserves_winner():
    # i0 and i1 both claim epoch 1; i0 (first comment_id) wins; i1 is the loser and releases
    # ITS epoch-1 claim. Owner-blind release would return None and starve i0 (the winner).
    log = [
        ClaimEntry(0, Kind.CLAIM, "i0", 1, LIVE),
        ClaimEntry(1, Kind.CLAIM, "i1", 1, LIVE),
        ClaimEntry(2, Kind.RELEASE, "i1", 1, LIVE),
    ]
    assert resolve_owner(log, NOW) == "i0"


def test_heartbeat_extends_lease():
    log = [
        ClaimEntry(0, Kind.CLAIM, "i0", 1, DEAD),
        ClaimEntry(1, Kind.HEARTBEAT, "i0", 1, LIVE),
    ]
    assert resolve_owner(log, NOW) == "i0"


# --- the property: no two instances ever own the same epoch ---------------------------

def _run_schedule(actors: list[str]) -> list[tuple[int, str]]:
    """Interleave READ/APPEND/READBACK across instances; return confirmed (epoch, owner)."""
    log: list[ClaimEntry] = []
    ids = itertools.count()
    phase: dict[str, int] = {}
    epoch: dict[str, int] = {}
    skip: dict[str, bool] = {}
    owned: list[tuple[int, str]] = []

    for a in actors:
        p = phase.get(a, 0)
        if p == 0:                                            # READ latest snapshot
            plan = plan_claim(log, a, NOW)
            skip[a], epoch[a] = plan.skip, plan.epoch
            phase[a] = 1
        elif p == 1:                                          # APPEND my claim
            if not skip[a]:
                log.append(ClaimEntry(next(ids), Kind.CLAIM, a, epoch[a], LIVE))
            phase[a] = 2
        elif p == 2:                                          # READ BACK, decide
            if not skip[a]:
                if won_claim(log, a, NOW):
                    owned.append((epoch[a], a))
                else:
                    log.append(ClaimEntry(next(ids), Kind.RELEASE, a, epoch[a], LIVE))
            phase[a] = 3                                       # done
    return owned


@given(st.lists(st.integers(min_value=0, max_value=3), max_size=40))
def test_single_owner_per_epoch(actor_indices):
    owned = _run_schedule([f"i{i}" for i in actor_indices])
    by_epoch: dict[int, set[str]] = {}
    for epoch, owner in owned:
        by_epoch.setdefault(epoch, set()).add(owner)
    for epoch, owners in by_epoch.items():
        assert len(owners) == 1, f"epoch {epoch} confirmed by {owners}"
