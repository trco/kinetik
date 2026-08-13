"""Claim-marker serialize/parse (pure). The `gh` calls need a live issue to smoke-test."""

from __future__ import annotations

from datetime import datetime

from kontinuum.claim import ClaimEntry, Kind
from kontinuum.github import format_marker, parse_marker

LEASE = datetime(2026, 8, 13, 18, 40, 0)


def test_marker_round_trips():
    body = format_marker(Kind.CLAIM, "kontinuum/uros@laptop", 7, LEASE)
    assert parse_marker(42, body) == ClaimEntry(42, Kind.CLAIM, "kontinuum/uros@laptop", 7, LEASE)


def test_all_kinds_round_trip():
    for kind in (Kind.CLAIM, Kind.HEARTBEAT, Kind.RELEASE):
        assert parse_marker(1, format_marker(kind, "i0", 3, LEASE)).kind is kind


def test_non_kontinuum_comment_is_ignored():
    assert parse_marker(1, "just a human saying hello") is None


def test_malformed_marker_is_ignored():
    assert parse_marker(1, "<!-- kontinuum-claim not-json -->") is None


def test_marker_embedded_in_a_larger_comment():
    body = "K claimed this issue\n" + format_marker(Kind.CLAIM, "i0", 1, LEASE) + "\n(automated)"
    entry = parse_marker(1, body)
    assert entry.owner == "i0" and entry.epoch == 1
