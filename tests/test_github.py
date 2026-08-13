"""Claim-marker serialize/parse (pure). The `gh` calls need a live issue to smoke-test."""

from __future__ import annotations

from datetime import datetime

from kontinuum.claim import ClaimEntry, Kind
from kontinuum.github import format_marker, parse_claim_log, parse_marker

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


def test_forged_marker_from_another_author_is_ignored():
    marker = format_marker(Kind.CLAIM, "kontinuum/uros@laptop", 1, LEASE)
    comments = [
        {"id": 1, "user": {"login": "bot"}, "body": marker},          # legit: the bot
        {"id": 2, "user": {"login": "attacker"}, "body": marker},     # forged: same marker, wrong author
        {"id": 3, "user": {"login": "bot"}, "body": "just chatting"},  # bot, not a marker
    ]
    entries = parse_claim_log(comments, bot_login="bot")
    assert len(entries) == 1
    assert entries[0].comment_id == 1 and entries[0].owner == "kontinuum/uros@laptop"
