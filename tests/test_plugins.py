"""Universal-plugin injection (§16.1): bundled plugins reach the worktree but never the commit."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from kinetik.plugins import available, inject

SEED = os.path.join(".claude", "skills", "kinetik-context.md")
DOCS = os.path.join(".claude", "skills", "living-docs.md")


def test_living_docs_is_the_toggleable_plugin():
    assert available() == ["living-docs"]                     # core is not listed — it always ships


def test_inject_lands_the_bundle_and_returns_paths(tmp_path):
    injected = inject(str(tmp_path))
    assert SEED in injected                                   # the bundled seed skill was placed
    assert (tmp_path / ".claude" / "skills" / "kinetik-context.md").is_file()


def test_plugin_selection_narrows_what_lands(tmp_path):
    assert inject(str(tmp_path / "off"), []) == [SEED]         # core only — living-docs is off here
    assert DOCS in inject(str(tmp_path / "on"), ["living-docs"])
    assert sorted(inject(str(tmp_path / "all"))) == sorted(inject(str(tmp_path / "named"),
                                                                 ["living-docs"]))   # None = all


def test_inject_never_overwrites_repo_native(tmp_path):
    native = tmp_path / ".claude" / "skills" / "kinetik-context.md"
    native.parent.mkdir(parents=True)
    native.write_text("REPO OWNS THIS")
    injected = inject(str(tmp_path))
    assert native.read_text() == "REPO OWNS THIS"             # repo-native wins — untouched
    assert SEED not in injected                               # skipped, so not offered for exclusion


def test_injected_files_stay_out_of_the_commit(tmp_path):
    repo = str(tmp_path)
    subprocess.run(["git", "init", "-q", repo], check=True)
    injected = inject(repo)
    excl = Path(repo) / ".git" / "info" / "exclude"           # what execute() appends to
    excl.write_text(excl.read_text() + "\n".join(injected) + "\n")
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    staged = subprocess.run(["git", "-C", repo, "diff", "--cached", "--name-only"],
                            capture_output=True, text=True).stdout
    assert ".claude" not in staged                            # injected plugins are not staged for the PR
