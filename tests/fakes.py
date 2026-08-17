"""In-memory IssueQueue stand-in shared across tests. The real GitHub adapter duck-types it."""

from __future__ import annotations

import itertools
from datetime import datetime

from kinetik.claim import ClaimEntry, ClaimKind
from kinetik.pipeline import Verdict


class FakeIssueQueue:
    def __init__(self, repo: str = "owner/repo", number: int = 1):
        self.repo = repo
        self.number = number
        self._log: list[ClaimEntry] = []
        self._ids = itertools.count()
        self.label: str | None = None
        self.comments: list[str] = []

    def read_claim_log(self) -> list[ClaimEntry]:
        return list(self._log)                             # snapshot, like a GET

    def append_entry(self, kind: ClaimKind, owner: str, epoch: int, lease_until: datetime) -> None:
        self._log.append(ClaimEntry(next(self._ids), kind, owner, epoch, lease_until))  # server assigns id

    def set_label(self, label: str) -> None:
        self.label = label                                 # fake is exclusive by construction (one label)

    def labels(self) -> list[str]:
        return [f"kinetik:{self.label}"] if self.label else []

    def comment(self, body: str) -> None:
        self.comments.append(body)


class FakeAgentRunner:
    """Stands in for the Claude Agent SDK: applies a canned edit to the worktree, returns a pr_body."""

    def __init__(self, edit, pr_body: str = "done"):
        self._edit = edit           # edit(workdir: str) -> None — writes/changes files in the worktree
        self._pr_body = pr_body

    def run(self, sandbox, task, feedback: str = "") -> str:
        self._edit(sandbox.workdir)
        return self._pr_body


class FakeReviewer:
    def __init__(self, approved: bool = True, reason: str = ""):
        self._verdict = Verdict(approved, reason)

    def review(self, diff, task) -> Verdict:
        return self._verdict
