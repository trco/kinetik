"""Living-docs maintenance (§16.1): K computes the diff, the agent drafts, failures never block."""

from __future__ import annotations

import subprocess
from pathlib import Path

from kontinuum.livingdocs import changed_paths, has_living_docs, maintain, seed


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

    class Agent:                                          # no run_command -> graceful skip
        pass

    maintain(Agent(), r)                                  # must not raise


def test_maintain_invokes_docs_update_with_changed_paths(tmp_path):
    r = _repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n")
    seen = {}

    class Agent:
        def run_command(self, workdir, command):
            seen["command"] = command

    maintain(Agent(), r)
    assert seen["command"].startswith("/docs-update")    # invokes the injected command, not a .py prompt
    assert "a.py" in seen["command"]                      # K passes the changed paths as arguments


def test_maintain_skips_when_nothing_changed(tmp_path):
    r = _repo(tmp_path)
    called = []

    class Agent:
        def run_command(self, *a):
            called.append(1)

    maintain(Agent(), r)                                  # clean tree -> no command
    assert not called


def test_maintain_swallows_errors(tmp_path):
    r = _repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n")

    class Agent:
        def run_command(self, *a):
            raise RuntimeError("boom")

    maintain(Agent(), r)                                  # docs failure never blocks the code PR


def test_has_living_docs(tmp_path):
    assert not has_living_docs(str(tmp_path))
    (tmp_path / "docs" / "living-docs").mkdir(parents=True)
    assert has_living_docs(str(tmp_path))


def test_seed_noops_without_capability(tmp_path):
    class Agent:                                          # no run_command -> graceful skip, no docs
        pass

    assert seed(Agent(), str(tmp_path)) is False


def test_seed_invokes_docs_seed_and_reports_result(tmp_path):
    seen = {}

    class Agent:
        def run_command(self, workdir, command):
            seen["command"] = command
            (Path(workdir) / "docs" / "living-docs").mkdir(parents=True)
            (Path(workdir) / "docs" / "living-docs" / "INDEX.md").write_text("i")

    assert seed(Agent(), str(tmp_path)) is True           # produced docs -> True
    assert seen["command"] == "/docs-seed"                # invokes the injected command


def test_seed_swallows_errors(tmp_path):
    class Agent:
        def run_command(self, workdir, command):
            raise RuntimeError("boom")

    assert seed(Agent(), str(tmp_path)) is False          # error swallowed, nothing produced -> False
