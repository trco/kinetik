"""Local clone + throwaway worktree per task — never touches the user's checkout."""

from __future__ import annotations

import os
import shutil
import tempfile

from kinetik.github import gh
from kinetik.gitcmd import git

CACHE_DIR = os.path.expanduser("~/.kinetik/cache")


def _ensure_clone(repo: str) -> str:
    """One local clone per repo, kept up to date — reused across tasks instead of re-cloning."""
    path = os.path.join(CACHE_DIR, repo.replace("/", "__"))
    if os.path.isdir(path):
        git(path, "fetch", "--quiet", "origin")
    else:
        os.makedirs(CACHE_DIR, exist_ok=True)
        gh("repo", "clone", repo, path, "--", "-q")
    return path


def _new_worktree(repo: str, base: str) -> tuple[str, str]:
    """A fresh, isolated worktree off the latest base — no re-download, never touches your checkout."""
    cache = _ensure_clone(repo)
    workdir = os.path.join(tempfile.mkdtemp(prefix="kinetik-wt-"), "wt")
    git(cache, "worktree", "add", "--quiet", "--force", "--detach", workdir, f"origin/{base}")
    return workdir, cache


def _remove_worktree(cache: str, workdir: str) -> None:
    git(cache, "worktree", "remove", "--force", workdir)
    shutil.rmtree(os.path.dirname(workdir), ignore_errors=True)


def _untracked_paths(workdir: str) -> set[str]:
    # -z = NUL-separated, unquoted -> handles paths with spaces/special chars
    out = git(workdir, "status", "--porcelain", "-z", "--untracked-files=normal")
    return {e[3:] for e in out.split("\0") if e.startswith("?? ")}


def _git_exclude_path(workdir: str) -> str:
    # in a worktree, workdir/.git is a FILE; ask git for the real (shared) exclude path
    p = git(workdir, "rev-parse", "--git-path", "info/exclude").strip()
    return p if os.path.isabs(p) else os.path.join(workdir, p)
