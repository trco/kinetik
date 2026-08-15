"""The per-issue decision: claim a ready issue, skip a taken one, honor `hold`."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from kontinuum.claim import Kind, max_epoch, resolve_owner
from kontinuum.loop import HOLD_LABEL, Heartbeater, _list_args, handle_issue
from kontinuum.protocol import attempt_claim
from tests.fakes import FakeIssueQueue

NOW = datetime(2026, 8, 13, 12, 0, 0)
LEASE = timedelta(hours=1)


def _recorder():
    calls = []

    def execute(q, me):
        calls.append(q)
        return "https://pr"          # truthy = real work happened

    return execute, calls


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


def test_bails_when_execute_does_nothing():
    q = FakeIssueQueue()
    assert handle_issue(q, "i0", NOW, LEASE, [], lambda q, me: None) == "bailed"   # execute returned None


def test_reclaim_of_own_issue_does_not_inflate_epoch():
    q = FakeIssueQueue()
    e1 = attempt_claim(q, "i0", NOW, LEASE)                # claim epoch 1
    before = len(q.read_claim_log())
    e2 = attempt_claim(q, "i0", NOW, LEASE)                # re-poll: already ours
    assert e1 == e2 == 1                                   # same epoch, not inflated
    assert len(q.read_claim_log()) == before + 1          # only a heartbeat appended, not a new claim


def test_run_once_skips_a_bail_and_works_the_next(monkeypatch):
    from kontinuum import loop
    qs = {1: FakeIssueQueue("r", 1), 2: FakeIssueQueue("r", 2)}
    monkeypatch.setattr(loop, "poll_ready", lambda repo, assignee=None: [1, 2])
    monkeypatch.setattr(loop, "issue_labels", lambda repo, n: [])
    monkeypatch.setattr(loop, "GitHubIssueQueue", lambda repo, n, bot: qs[n])
    worked = loop.run_once("r", "me", LEASE, "bot",
                           lambda q, me: None if q.number == 1 else "https://pr", None, {})
    assert worked == 2                                    # #1 bailed -> moved on and worked #2


def test_run_once_blocks_only_after_repeated_errors(monkeypatch):
    from kontinuum import loop
    q = FakeIssueQueue("r", 1)
    monkeypatch.setattr(loop, "poll_ready", lambda repo, assignee=None: [1])
    monkeypatch.setattr(loop, "issue_labels", lambda repo, n: [])
    monkeypatch.setattr(loop, "GitHubIssueQueue", lambda repo, n, bot: q)

    def boom(qq, me):
        raise RuntimeError("gh blip")

    errors: dict = {}
    loop.run_once("r", "me", LEASE, "bot", boom, None, errors)
    assert q.label != "blocked"                           # transient: released, not blocked yet
    for _ in range(loop.MAX_ERRORS):
        loop.run_once("r", "me", LEASE, "bot", boom, None, errors)
    assert q.label == "blocked"                           # blocked only after MAX_ERRORS


def test_heartbeater_refreshes_the_lease():
    q = FakeIssueQueue()
    attempt_claim(q, "i0", NOW, LEASE)
    epoch = max_epoch(q.read_claim_log())
    before = len(q.read_claim_log())
    with Heartbeater(q, "i0", epoch, LEASE, interval=0.02):
        time.sleep(0.12)
    assert len(q.read_claim_log()) > before               # background heartbeats were appended


def test_shared_queue_has_no_assignee_filter():
    assert "--assignee" not in _list_args("owner/repo", "kontinuum:ready", None)


def test_personal_queue_filters_by_assignee():
    args = _list_args("owner/repo", "kontinuum:ready", "uros")
    assert args[args.index("--assignee") + 1] == "uros"
