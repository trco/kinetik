"""Read a PR's CI result (§9). K gates on CI status — it never runs e2e itself.

A repo with no CI returns 'none' immediately, so the daemon never blocks waiting on nothing.
"""

from __future__ import annotations

import json
import time

from kontinuum.github import gh


def summarize_checks(checks: list) -> str:
    """'pass' | 'fail' | 'pending' | 'none' from a gh statusCheckRollup list (fail > pending > pass)."""
    if not checks:
        return "none"
    seen = set()
    for c in checks:
        status = (c.get("status") or "").upper()          # CheckRun: QUEUED / IN_PROGRESS / COMPLETED
        conclusion = (c.get("conclusion") or "").upper()  # CheckRun result
        state = (c.get("state") or "").upper()            # StatusContext: SUCCESS / FAILURE / PENDING / ERROR
        if status in ("QUEUED", "IN_PROGRESS", "PENDING", "WAITING") or state == "PENDING":
            seen.add("pending")
        elif conclusion in ("FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE") \
                or state in ("FAILURE", "ERROR"):
            seen.add("fail")
        else:
            seen.add("pass")
    if "fail" in seen:
        return "fail"
    if "pending" in seen:
        return "pending"
    return "pass"


def ci_state(pr: str) -> str:
    out = gh("pr", "view", pr, "--json", "statusCheckRollup")
    return summarize_checks(json.loads(out).get("statusCheckRollup") or [])


def await_ci(pr: str, timeout: int = 1800, interval: int = 30) -> str:
    """Poll until CI settles (pass/fail/none) or timeout. No-CI PRs return 'none' at once.

    ponytail: blocks the serial worker while polling. The non-blocking upgrade is a separate
    PR-maintenance loop that revisits pr-open issues (§12) — add it when throughput needs it.
    """
    end = time.time() + timeout
    while True:
        state = ci_state(pr)
        if state != "pending" or time.time() >= end:
            return state
        time.sleep(interval)
