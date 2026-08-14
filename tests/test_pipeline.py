"""Pipeline spine with a fake agent: gate-passing edit -> Proposal; always-failing -> BLOCKED. Docker-gated."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from kontinuum.pipeline import Task, propose
from kontinuum.sandbox import Sandbox
from tests.fakes import FakeAgentRunner

IMAGE = "alpine:latest"


def _docker_ok() -> bool:
    return bool(shutil.which("docker")) and \
        subprocess.run(["docker", "info"], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not _docker_ok(), reason="docker daemon not available")


def _init_repo(path: str) -> None:
    subprocess.run(["git", "init", "-q", path], check=True)
    subprocess.run(["git", "-C", path, "config", "user.email", "k@k"], check=True)
    subprocess.run(["git", "-C", path, "config", "user.name", "k"], check=True)
    (Path(path) / "README.md").write_text("start\n")
    subprocess.run(["git", "-C", path, "add", "-A"], check=True)
    subprocess.run(["git", "-C", path, "commit", "-qm", "init"], check=True)


def test_proposal_returned_when_gate_passes(tmp_path):
    repo = str(tmp_path)
    _init_repo(repo)
    agent = FakeAgentRunner(lambda wd: (Path(wd) / "feature.txt").write_text("hello\n"), pr_body="Implements #1")
    prop = propose(Sandbox(IMAGE, repo), Task(1), gate_cmd="test -f feature.txt", agent=agent)
    assert prop is not None
    assert "feature.txt" in prop.diff
    assert prop.pr_body == "Implements #1"
    assert prop.attempts == 1


def test_blocked_after_retries_when_gate_always_fails(tmp_path):
    repo = str(tmp_path)
    _init_repo(repo)
    calls = []

    def edit(wd):
        calls.append(1)
        (Path(wd) / "feature.txt").write_text("x\n")

    prop = propose(Sandbox(IMAGE, repo), Task(1), gate_cmd="false", agent=FakeAgentRunner(edit), max_attempts=3)
    assert prop is None
    assert len(calls) == 3                                 # tried the cap, then gave up
    assert not (Path(repo) / "feature.txt").exists()       # failed attempt cleaned up, worktree left clean


def test_secret_in_diff_is_blocked_even_when_gate_passes(tmp_path):
    repo = str(tmp_path)
    _init_repo(repo)
    agent = FakeAgentRunner(lambda wd: (Path(wd) / "conf.py").write_text('AWS_KEY = "AKIA1234567890ABCDEF"\n'))
    prop = propose(Sandbox(IMAGE, repo), Task(1), gate_cmd="true", agent=agent, max_attempts=1)
    assert prop is None                                    # gate green, but the secret scan blocks it
    assert not (Path(repo) / "conf.py").exists()           # rejected diff cleaned up
