"""Pipeline spine with a fake agent: gate-passing edit -> Proposal; always-failing -> BLOCKED. Docker-gated."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from kinetik.pipeline import Task, propose
from kinetik.sandbox import Sandbox
from tests.fakes import FakeAgentRunner, FakeReviewer

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


def test_blocked_after_max_attempts_when_gate_always_fails(tmp_path):
    repo = str(tmp_path)
    _init_repo(repo)
    calls = []

    class Agent:
        def run(self, sandbox, task, feedback=""):
            calls.append(1)
            (Path(sandbox.workdir) / "feature.txt").write_text(f"try {len(calls)}\n")  # varies each attempt
            return "d"

    prop = propose(Sandbox(IMAGE, repo), Task(1), gate_cmd="false", agent=Agent(), max_attempts=3)
    assert prop is None
    assert len(calls) == 3                                 # different diffs -> runs the full cap
    assert not (Path(repo) / "feature.txt").exists()       # worktree left clean


def test_secret_in_diff_is_blocked_even_when_gate_passes(tmp_path):
    repo = str(tmp_path)
    _init_repo(repo)
    agent = FakeAgentRunner(lambda wd: (Path(wd) / "conf.py").write_text('AWS_KEY = "AKIA1234567890ABCDEF"\n'))
    prop = propose(Sandbox(IMAGE, repo), Task(1), gate_cmd="true", agent=agent, max_attempts=1)
    assert prop is None                                    # gate green, but the secret scan blocks it
    assert not (Path(repo) / "conf.py").exists()           # rejected diff cleaned up


def test_agent_gets_feedback_and_fixes_on_retry(tmp_path):
    repo = str(tmp_path)
    _init_repo(repo)

    class Agent:
        def run(self, sandbox, task, feedback=""):
            (Path(sandbox.workdir) / "marker").write_text("ok" if feedback else "bad")  # fixes once told
            return "done"

    prop = propose(Sandbox(IMAGE, repo), Task(1), gate_cmd='test "$(cat marker)" = ok',
                   agent=Agent(), max_attempts=3)
    assert prop is not None
    assert prop.attempts == 2                              # failed once, fixed after feedback


def test_reviewer_rejection_blocks_the_pr(tmp_path):
    repo = str(tmp_path)
    _init_repo(repo)
    agent = FakeAgentRunner(lambda wd: (Path(wd) / "f.txt").write_text("hi"))
    prop = propose(Sandbox(IMAGE, repo), Task(1), gate_cmd="true", agent=agent,
                   reviewer=FakeReviewer(approved=False, reason="not right"), max_attempts=2)
    assert prop is None                                    # gate green, but reviewer rejects


def test_reviewer_approval_lets_it_through(tmp_path):
    repo = str(tmp_path)
    _init_repo(repo)
    agent = FakeAgentRunner(lambda wd: (Path(wd) / "f.txt").write_text("hi"))
    prop = propose(Sandbox(IMAGE, repo), Task(1), gate_cmd="true", agent=agent,
                   reviewer=FakeReviewer(approved=True), max_attempts=1)
    assert prop is not None


def test_blocked_when_diff_stops_changing(tmp_path):
    repo = str(tmp_path)
    _init_repo(repo)
    calls = []

    class Stuck:
        def run(self, sandbox, task, feedback=""):
            calls.append(1)
            (Path(sandbox.workdir) / "x").write_text("same")   # identical diff every time
            return "d"

    prop = propose(Sandbox(IMAGE, repo), Task(1), gate_cmd="false", agent=Stuck(), max_attempts=5)
    assert prop is None
    assert len(calls) == 2                                 # oscillation guard stops after the repeat
