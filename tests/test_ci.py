"""CI rollup -> pass/fail/pending/none (fail > pending > pass); the live poll needs a CI repo."""

from __future__ import annotations

from kontinuum.ci import summarize_checks


def test_no_checks_is_none():
    assert summarize_checks([]) == "none"


def test_all_success_is_pass():
    assert summarize_checks([{"status": "COMPLETED", "conclusion": "SUCCESS"}]) == "pass"


def test_any_failure_is_fail():
    assert summarize_checks([
        {"status": "COMPLETED", "conclusion": "SUCCESS"},
        {"status": "COMPLETED", "conclusion": "FAILURE"},
    ]) == "fail"


def test_in_progress_is_pending():
    assert summarize_checks([{"status": "IN_PROGRESS", "conclusion": None}]) == "pending"


def test_failure_dominates_pending():
    assert summarize_checks([
        {"status": "IN_PROGRESS", "conclusion": None},
        {"status": "COMPLETED", "conclusion": "FAILURE"},
    ]) == "fail"


def test_legacy_status_context_state():
    assert summarize_checks([{"state": "PENDING"}]) == "pending"
    assert summarize_checks([{"state": "ERROR"}]) == "fail"
