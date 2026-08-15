"""Read a PR's CI result (§9). K gates on CI status — it never runs e2e itself.

A single read per PR, checked each loop pass (non-blocking); the worker never waits on CI.
"""

from __future__ import annotations

import json

from kontinuum.github import gh


def summarize_checks(checks: list) -> str:
    """'pass' | 'fail' | 'pending' | 'none' from a gh statusCheckRollup list (fail > pending > pass)."""
    if not checks:
        return "none"
    seen = set()
    for c in checks:
        conclusion = (c.get("conclusion") or "").upper()  # CheckRun result
        state = (c.get("state") or "").upper()            # StatusContext: SUCCESS / FAILURE / PENDING / ERROR / EXPECTED
        if conclusion in ("FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE") \
                or state in ("FAILURE", "ERROR"):
            seen.add("fail")                              # only explicit failures block
        elif conclusion in ("SUCCESS", "SKIPPED", "NEUTRAL") or state == "SUCCESS":
            seen.add("pass")
        else:
            seen.add("pending")   # queued/in-progress/EXPECTED/empty/unknown -> wait, re-check next pass
    if "fail" in seen:
        return "fail"
    if "pending" in seen:
        return "pending"
    return "pass"


def ci_state(repo: str, ref: str) -> str:
    """One read of the PR's checks. ref is the branch/number/url of the PR."""
    out = gh("pr", "view", ref, "--repo", repo, "--json", "statusCheckRollup")
    return summarize_checks(json.loads(out).get("statusCheckRollup") or [])
