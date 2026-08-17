"""Effect Broker — the credentialed boundary (§8). Nothing untrusted reaches here.

For now it holds the pre-push guard: scan a proposed diff and refuse anything that introduces a
secret. The privileged writes (git push + open PR + comment + label) land here next, so the agent
never performs an outward effect — it only proposes.
"""

from __future__ import annotations

import re

from kinetik.github import gh
from kinetik.gitcmd import git

SECRET_PATTERNS = [
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("secret assignment", re.compile(r"""(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['"][^'"]{8,}['"]""")),
]


def scan_diff(diff: str) -> list[str]:
    """Policy violations for a proposed diff: secrets introduced on ADDED lines. Empty = safe to push."""
    violations = []
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):   # only added content, skip file headers
            continue
        for name, pattern in SECRET_PATTERNS:
            if pattern.search(line):
                violations.append(f"{name} in added line")
                break
    return violations


def scan_worktree(workdir: str) -> list[str]:
    """Same guard for a push that skips `propose()`: scan everything pending in the worktree.

    The plan-first PR is agent-written content that never passes through the pipeline's scan, and
    no push may leave unscanned. Stages as `open_pr` does, so it sees exactly what would be pushed.
    """
    git(workdir, "add", "-A")
    return scan_diff(git(workdir, "diff", "--cached"))


def open_pr(workdir: str, repo: str, branch: str, title: str, body: str, base: str) -> str:
    """Commit the worktree (detached HEAD) and push it to `branch`, open a PR. Returns the PR URL.

    Commits on the detached worktree HEAD and pushes HEAD -> refs/heads/{branch}, so NO local branch
    is created in the shared cache clone (which otherwise leaks and makes a re-run crash on checkout).
    Force is safe: it's K's own ephemeral per-issue branch. All credentialed writes live here.
    """
    git(workdir, "add", "-A")
    git(workdir, "-c", "user.name=kinetik-bot", "-c", "user.email=kinetik@users.noreply.github.com",
        "commit", "-m", title)                            # self-authored, no reliance on global git config
    git(workdir, "push", "--force", "origin", f"HEAD:refs/heads/{branch}")
    return gh("pr", "create", "--repo", repo, "--base", base, "--head", branch,
              "--title", title, "--body", body).strip()
