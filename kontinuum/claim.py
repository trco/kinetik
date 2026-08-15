"""Ownership resolution for the epoch-leased claim log (MVP §7).

Pure functions only — no GitHub, no clock, no I/O. The Effect Broker does the
appends; this module just decides who owns an issue given the ordered log.
That purity is why the single-owner invariant is testable without mocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ClaimKind(str, Enum):
    CLAIM = "claim"
    HEARTBEAT = "heartbeat"
    RELEASE = "release"


@dataclass(frozen=True)
class ClaimEntry:
    comment_id: int      # GitHub comment id: monotonic, same total order for every reader
    kind: ClaimKind
    owner: str           # instance_id, e.g. "kontinuum/uros@laptop"
    epoch: int
    lease_until: datetime


def max_epoch(log: list[ClaimEntry]) -> int:
    return max((m.epoch for m in log), default=0)


def resolve_owner(log: list[ClaimEntry], now: datetime) -> str | None:
    """Owner = the live claim at the highest epoch; at that epoch the FIRST claimer wins.

    First-claimer-wins (not the spec §7 "latest comment_id"): latest-wins lets a later
    same-epoch claim steal an already-confirmed claim — a double-claim the property test
    catches. A release only frees the issue if it matches the winning owner (owner-blind
    release would let a tie-loser release the winner and starve the issue).
    """
    entries = sorted(log, key=lambda e: e.comment_id)
    live = [m for m in entries if m.kind in (ClaimKind.CLAIM, ClaimKind.HEARTBEAT)]
    if not live:
        return None
    epoch = max(m.epoch for m in live)
    claims = [m for m in entries if m.epoch == epoch and m.kind is ClaimKind.CLAIM]
    if not claims:
        return None
    winner = claims[0].owner
    if any(m.kind is ClaimKind.RELEASE and m.epoch == epoch and m.owner == winner for m in entries):
        return None
    lease_until = max(m.lease_until for m in live if m.epoch == epoch and m.owner == winner)
    return winner if lease_until > now else None


@dataclass(frozen=True)
class ClaimPlan:
    skip: bool           # true = someone else holds a live lease; don't claim
    epoch: int           # epoch to append the claim at (reclaim ⇒ strictly higher)


def plan_claim(log: list[ClaimEntry], me: str, now: datetime) -> ClaimPlan:
    """Decide, from a fresh log read, whether/at-what-epoch to append a claim."""
    cur = resolve_owner(log, now)
    if cur is not None and cur != me:
        return ClaimPlan(skip=True, epoch=0)
    return ClaimPlan(skip=False, epoch=max_epoch(log) + 1)


def owns(log_after_readback: list[ClaimEntry], me: str, now: datetime) -> bool:
    """After appending my claim and re-reading, did I win (read-back breaks same-tick ties)?"""
    return resolve_owner(log_after_readback, now) == me
