"""Effect Broker — the credentialed boundary (§8). Nothing untrusted reaches here.

For now it holds the pre-push guard: scan a proposed diff and refuse anything that introduces a
secret. The privileged writes (git push + open PR + comment + label) land here next, so the agent
never performs an outward effect — it only proposes.
"""

from __future__ import annotations

import re

from kontinuum.github import gh
from kontinuum.gitcmd import git

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


def pr_body_text(issue: int, summary: str) -> str:
    """`Closes #N` is what makes GitHub auto-close the issue when the PR merges."""
    return f"Closes #{issue}\n\n{summary}"


def open_pr(workdir: str, repo: str, branch: str, title: str, body: str, base: str) -> str:
    """Commit the worktree on `branch`, push it, open a PR. Returns the PR URL.

    All the credentialed writes live here — the sandboxed agent never reaches this.
    """
    git(workdir, "checkout", "-b", branch)
    git(workdir, "add", "-A")
    git(workdir, "-c", "user.name=kontinuum-bot", "-c", "user.email=kontinuum@users.noreply.github.com",
        "commit", "-m", title)                            # self-authored, no reliance on global git config
    git(workdir, "push", "-u", "origin", branch)
    return gh("pr", "create", "--repo", repo, "--base", base, "--head", branch,
              "--title", title, "--body", body).strip()
