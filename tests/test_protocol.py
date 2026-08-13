"""End-to-end claim protocol against a fake queue: claim, skip, reclaim, heartbeat."""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta

from kontinuum.claim import ClaimEntry, Kind, resolve_owner
from kontinuum.protocol import attempt_claim, heartbeat

NOW = datetime(2026, 8, 13, 12, 0, 0)
LEASE = timedelta(hours=1)


class FakeIssueQueue:
    """In-memory stand-in for the GitHub claim log. The real adapter duck-types these."""

    def __init__(self):
        self._log: list[ClaimEntry] = []
        self._ids = itertools.count()
        self.label: str | None = None

    def read_claim_log(self) -> list[ClaimEntry]:
        return list(self._log)                             # snapshot, like a GET

    def append(self, kind: Kind, owner: str, epoch: int, lease_until: datetime) -> None:
        self._log.append(ClaimEntry(next(self._ids), kind, owner, epoch, lease_until))  # server assigns id

    def set_label(self, label: str) -> None:
        self.label = label


def test_first_instance_claims_and_labels():
    q = FakeIssueQueue()
    assert attempt_claim(q, "i0", NOW, LEASE) is True
    assert q.label == "claimed"
    assert resolve_owner(q.read_claim_log(), NOW) == "i0"


def test_second_instance_skips_a_live_claim():
    q = FakeIssueQueue()
    attempt_claim(q, "i0", NOW, LEASE)
    assert attempt_claim(q, "i1", NOW, LEASE) is False
    assert resolve_owner(q.read_claim_log(), NOW) == "i0"


def test_reclaim_after_lease_expiry():
    q = FakeIssueQueue()
    attempt_claim(q, "i0", NOW, LEASE)                     # i0 owns, then crashes (no heartbeats)
    later = NOW + LEASE + timedelta(minutes=1)             # lease lapses
    assert resolve_owner(q.read_claim_log(), later) is None
    assert attempt_claim(q, "i1", later, LEASE) is True    # i1 reclaims at epoch+1
    assert resolve_owner(q.read_claim_log(), later) == "i1"


def test_heartbeat_keeps_ownership_past_original_lease():
    q = FakeIssueQueue()
    attempt_claim(q, "i0", NOW, LEASE)                     # claim epoch 1, lease NOW+1h
    mid = NOW + timedelta(minutes=40)
    heartbeat(q, "i0", epoch=1, now=mid, lease=LEASE)      # extend to mid+1h before expiry
    assert resolve_owner(q.read_claim_log(), NOW + timedelta(minutes=90)) == "i0"
