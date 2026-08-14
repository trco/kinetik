"""The per-issue decision: claim a ready issue, skip a taken one, honor `hold`."""

from __future__ import annotations

from datetime import datetime, timedelta

from kontinuum.claim import Kind, resolve_owner
from kontinuum.loop import HOLD_LABEL, _ready_args, handle_issue
from tests.fakes import FakeIssueQueue

NOW = datetime(2026, 8, 13, 12, 0, 0)
LEASE = timedelta(hours=1)


def _recorder():
    calls = []
    return (lambda q: calls.append(q)), calls


def test_claims_and_executes_a_ready_issue():
    q = FakeIssueQueue()
    execute, calls = _recorder()
    assert handle_issue(q, "i0", NOW, LEASE, [], execute) == "executed"
    assert len(calls) == 1
    assert resolve_owner(q.read_claim_log(), NOW) == "i0"


def test_skips_an_issue_owned_by_another():
    q = FakeIssueQueue()
    q.append(Kind.CLAIM, "other", 1, NOW + LEASE)          # someone else holds a live lease
    execute, calls = _recorder()
    assert handle_issue(q, "i0", NOW, LEASE, [], execute) == "skip"
    assert calls == []


def test_hold_makes_the_owner_release_and_not_execute():
    q = FakeIssueQueue()
    q.append(Kind.CLAIM, "i0", 1, NOW + LEASE)             # i0 currently owns it
    execute, calls = _recorder()
    assert handle_issue(q, "i0", NOW, LEASE, [HOLD_LABEL], execute) == "hold"
    assert resolve_owner(q.read_claim_log(), NOW) is None  # released
    assert calls == []


def test_hold_prevents_a_fresh_claim():
    q = FakeIssueQueue()
    execute, calls = _recorder()
    assert handle_issue(q, "i0", NOW, LEASE, [HOLD_LABEL], execute) == "hold"
    assert resolve_owner(q.read_claim_log(), NOW) is None  # never claimed
    assert calls == []


def test_shared_queue_has_no_assignee_filter():
    assert "--assignee" not in _ready_args("owner/repo", None)


def test_personal_queue_filters_by_assignee():
    args = _ready_args("owner/repo", "uros")
    assert args[args.index("--assignee") + 1] == "uros"
