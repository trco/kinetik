"""Config loads from YAML, flags override the file, missing required keys fail fast."""

from __future__ import annotations

import pytest

from kontinuum.config import load_config


def _write(tmp_path, text: str) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(text)
    return str(p)


def test_loads_from_yaml(tmp_path):
    cfg = load_config(_write(tmp_path, "instance_id: k/uros\nbot_login: uros\nrepos:\n  - a/b\n  - c/d\n"))
    assert cfg.instance_id == "k/uros"
    assert cfg.repos == ["a/b", "c/d"]
    assert cfg.assignee is None        # omitted -> shared queue
    assert cfg.agent is None           # omitted -> stub, no PRs
    assert cfg.lease_min == 60         # default


def test_flags_override_file(tmp_path):
    path = _write(tmp_path, "instance_id: k/uros\nbot_login: uros\nrepos: [a/b]\npoll_sec: 300\n")
    cfg = load_config(path, {"poll_sec": 30, "assignee": "uros", "instance_id": None})
    assert cfg.poll_sec == 30          # flag wins
    assert cfg.assignee == "uros"      # flag adds
    assert cfg.instance_id == "k/uros"  # None flag ignored, file kept


def test_missing_required_fails_fast(tmp_path):
    path = _write(tmp_path, "bot_login: uros\n")   # no instance_id, no repos
    with pytest.raises(SystemExit):
        load_config(path)
