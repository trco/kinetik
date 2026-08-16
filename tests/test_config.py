"""Config loads from YAML, flags override the file, missing required keys fail fast."""

from __future__ import annotations

import pytest

from kontinuum.config import Repo, load_config


def _write(tmp_path, text: str) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(text)
    return str(p)


def test_loads_from_yaml(tmp_path):
    cfg = load_config(_write(tmp_path, "instance_id: k/uros\nbot_login: uros\nrepos:\n  - a/b\n  - c/d\n"))
    assert cfg.instance_id == "k/uros"
    assert [r.repo for r in cfg.repos] == ["a/b", "c/d"]
    assert cfg.repos[0].plugins is None  # plain string -> all universal plugins
    assert cfg.repos[0].agent is None    # plain string -> the machine-level agent
    assert cfg.assignee is None        # omitted -> shared queue
    assert cfg.agent is None           # omitted -> stub, no PRs
    assert cfg.lease_min == 60         # default


def test_per_repo_settings(tmp_path):
    cfg = load_config(_write(tmp_path, "instance_id: k\nbot_login: uros\nrepos:\n"
                                       "  - a/b\n"
                                       "  - repo: c/d\n    plugins: [living-docs]\n    agent: demo\n"
                                       "  - repo: e/f\n    plugins: []\n"))
    assert cfg.repos[0] == Repo("a/b")                     # plain string still means all defaults
    assert cfg.repos[1] == Repo("c/d", ["living-docs"], "demo")
    assert cfg.repos[2].plugins == []                      # explicit empty -> no universal plugins


def test_bad_per_repo_settings_fail_fast(tmp_path):
    for entry in ("  - repo: a/b\n    plugins: [nope]\n",   # plugin K does not bundle
                  "  - repo: a/b\n    plugins: living-docs\n",   # not a list
                  "  - repo: a/b\n    agents: demo\n",      # misspelled key
                  "  - plugins: [living-docs]\n"):          # no repo
        path = _write(tmp_path, f"instance_id: k\nbot_login: uros\nrepos:\n{entry}")
        with pytest.raises(SystemExit):
            load_config(path)


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
