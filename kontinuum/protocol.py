"""The claim protocol: turn the pure decisions in `claim.py` into read/append steps.

`attempt_claim` is what each K instance's loop calls. The `queue` is duck-typed — the
real GitHub adapter and the test fake both expose read_claim_log / append / set_label.
The Effect Broker owns the `append`/`set_label` side (credentials); this just sequences them.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from kontinuum.claim import Kind, plan_claim, won_claim


def attempt_claim(queue, me: str, now: datetime, lease: timedelta) -> bool:
    """Try to own the issue. Returns True iff we hold it after read-back."""
    plan = plan_claim(queue.read_claim_log(), me, now)
    if plan.skip:                                          # someone holds a live lease
        return False
    queue.append(Kind.CLAIM, me, plan.epoch, now + lease)
    if won_claim(queue.read_claim_log(), me, now):         # read-back breaks same-tick ties
        queue.set_label("claimed")
        return True
    queue.append(Kind.RELEASE, me, plan.epoch, now + lease)  # lost the tie — yield cleanly
    return False


def heartbeat(queue, me: str, epoch: int, now: datetime, lease: timedelta) -> None:
    """Extend our lease at the same epoch. The owner calls this every ~LEASE/3."""
    queue.append(Kind.HEARTBEAT, me, epoch, now + lease)
