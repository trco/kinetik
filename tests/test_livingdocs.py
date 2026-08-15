"""Living-docs maintenance (§16.1): K computes the diff, the agent drafts, failures never block."""

from __future__ import annotations

import subprocess

from kontinuum.livingdocs import changed_paths, maintain
from kontinuum.pipeline import Task


def _repo(tmp_path):
    r = str(tmp_path)
    subprocess.run(["git", "init", "-q", r], check=True)
    subprocess.run(["git", "-C", r, "config", "user.email", "k@k"], check=True)
    subprocess.run(["git", "-C", r, "config", "user.name", "k"], check=True)
    (tmp_path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", r, "add", "-A"], check=True)
    subprocess.run(["git", "-C", r, "commit", "-qm", "init"], check=True)
    return r


def test_changed_paths_reports_edits_and_new_files(tmp_path):
    r = _repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n")             # modified
    (tmp_path / "b.py").write_text("y = 1\n")             # new
    assert set(changed_paths(r)) == {"a.py", "b.py"}


def test_changed_paths_excludes_docs_and_plugins(tmp_path):
    r = _repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n")
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "x.md").write_text("k")
    (tmp_path / "docs" / "living-docs").mkdir(parents=True)
    (tmp_path / "docs" / "living-docs" / "INDEX.md").write_text("i")
    assert set(changed_paths(r)) == {"a.py"}              # .claude/ and docs/living-docs/ dropped


def test_maintain_noops_without_capability(tmp_path):
    r = _repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n")

    class Agent:                                          # no update_docs -> graceful skip
        pass

    maintain(Agent(), r, Task(1))                         # must not raise


def test_maintain_passes_changed_paths_to_the_agent(tmp_path):
    r = _repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n")
    seen = {}

    class Agent:
        def update_docs(self, workdir, task, paths):
            seen["paths"] = paths

    maintain(Agent(), r, Task(1))
    assert "a.py" in seen["paths"]


def test_maintain_skips_when_nothing_changed(tmp_path):
    r = _repo(tmp_path)
    called = []

    class Agent:
        def update_docs(self, *a):
            called.append(1)

    maintain(Agent(), r, Task(1))                         # clean tree -> agent not called
    assert not called


def test_maintain_swallows_agent_errors(tmp_path):
    r = _repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n")

    class Agent:
        def update_docs(self, *a):
            raise RuntimeError("boom")

    maintain(Agent(), r, Task(1))                         # docs failure never blocks the code PR
