"""Summarize a PR's CI rollup (§9). K gates on CI status — it never runs e2e itself."""

from __future__ import annotations


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
