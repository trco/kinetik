"""The claim protocol: turn the pure decisions in `claim.py` into read/append steps.

`attempt_claim` is what each K instance's loop calls. The `queue` is duck-typed — the
real GitHub adapter and the test fake both expose read_claim_log / append / set_label.
The Effect Broker owns the `append`/`set_label` side (credentials); this just sequences them.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from kontinuum.claim import ClaimKind, max_epoch, plan_claim, resolve_owner, owns


def attempt_claim(queue, me: str, now: datetime, lease: timedelta) -> int | None:
    """Try to own the issue. Returns the epoch we won at, or None if we didn't."""
    log = queue.read_claim_log()
    if resolve_owner(log, now) == me:                     # already ours (re-polled) — refresh, don't re-claim
        epoch = max_epoch(log)
        heartbeat(queue, me, epoch, now, lease)
        return epoch
    plan = plan_claim(log, me, now)
    if plan.skip:                                          # someone holds a live lease
        return None
    queue.append_entry(ClaimKind.CLAIM, me, plan.epoch, now + lease)
    if owns(queue.read_claim_log(), me, now):         # read-back breaks same-tick ties
        queue.set_label("claimed")
        return plan.epoch                                  # the exact epoch we own -> heartbeat at this
    queue.append_entry(ClaimKind.RELEASE, me, plan.epoch, now + lease)  # lost the tie — yield cleanly
    return None


def heartbeat(queue, me: str, epoch: int, now: datetime, lease: timedelta) -> None:
    """Extend our lease at the same epoch. The owner calls this every ~LEASE/3."""
    queue.append_entry(ClaimKind.HEARTBEAT, me, epoch, now + lease)


def still_owns(queue, me: str, now: datetime) -> bool:
    """Guard called before every state transition and effect (§7). False ⇒ abort."""
    return owns(queue.read_claim_log(), me, now)   # both are "resolve_owner == me"


def release_if_mine(queue, me: str, now: datetime) -> bool:
    """Give up ownership if we still hold it (e.g. a human added `hold`). No-op otherwise."""
    log = queue.read_claim_log()
    if resolve_owner(log, now) != me:
        return False
    queue.append_entry(ClaimKind.RELEASE, me, max_epoch(log), now)   # release ignores lease_until
    return True
