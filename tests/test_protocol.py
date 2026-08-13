"""End-to-end claim protocol against the fake queue: claim, skip, reclaim, heartbeat, guards."""

from __future__ import annotations

from datetime import datetime, timedelta

from kontinuum.claim import Kind, resolve_owner
from kontinuum.protocol import assert_owner, attempt_claim, heartbeat, release_if_mine
from tests.fakes import FakeIssueQueue

NOW = datetime(2026, 8, 13, 12, 0, 0)
LEASE = timedelta(hours=1)


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


def test_assert_owner_reflects_the_log():
    q = FakeIssueQueue()
    assert assert_owner(q, "i0", NOW) is False             # nobody owns it
    attempt_claim(q, "i0", NOW, LEASE)
    assert assert_owner(q, "i0", NOW) is True
    assert assert_owner(q, "i1", NOW) is False             # not the owner


def test_release_if_mine_frees_only_my_claim():
    q = FakeIssueQueue()
    attempt_claim(q, "i0", NOW, LEASE)
    assert release_if_mine(q, "i1", NOW) is False          # not mine → no-op
    assert resolve_owner(q.read_claim_log(), NOW) == "i0"
    assert release_if_mine(q, "i0", NOW) is True           # mine → released
    assert resolve_owner(q.read_claim_log(), NOW) is None
