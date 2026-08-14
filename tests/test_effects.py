"""The pre-push diff policy: secrets on added lines are flagged, everything else passes."""

from __future__ import annotations

from kontinuum.effects import pr_body_text, scan_diff


def test_clean_diff_has_no_violations():
    assert scan_diff("+def add(a, b):\n+    return a + b\n") == []


def test_aws_key_flagged():
    assert scan_diff('+AWS_KEY = "AKIA1234567890ABCDEF"\n')


def test_private_key_flagged():
    assert scan_diff("+-----BEGIN RSA PRIVATE KEY-----\n")


def test_generic_secret_assignment_flagged():
    assert scan_diff('+api_key: "s3cr3tblahblah"\n')


def test_removed_secret_is_not_flagged():
    assert scan_diff('-password = "hunter2000!"\n') == []   # deleting a secret is good, not a violation


def test_file_header_line_is_not_flagged():
    assert scan_diff("+++ b/secret_token.py\n") == []       # the +++ header is a filename, not content


def test_pr_body_links_the_issue():
    body = pr_body_text(7, "did the thing")
    assert "Closes #7" in body and "did the thing" in body   # Closes #N auto-closes the issue on merge
