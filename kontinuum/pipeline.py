"""Task execution pipeline core: agent edits a worktree, gate checks it, retry -> proposal (§10).

Fake-first: any AgentRunner exposing `run(sandbox, issue) -> pr_body` works here — the real Claude
Agent SDK drops into the same seam. The Reviewer and the push/PR effects (Effect Broker) are the
next increments; this stops at a gate-passing Proposal.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from kontinuum.verify import run_gate


@dataclass
class Proposal:
    diff: str
    pr_body: str
    attempts: int


def _git(workdir: str, *args: str) -> str:
    return subprocess.run(["git", "-C", workdir, *args], check=True, capture_output=True, text=True).stdout


def propose(sandbox, issue: int, gate_cmd: str, agent, max_attempts: int = 3) -> Proposal | None:
    """Run agent -> gate up to max_attempts. Staged diff on the first pass, else None (BLOCKED)."""
    for attempt in range(1, max_attempts + 1):
        pr_body = agent.run(sandbox, issue)              # edits the worktree at sandbox.workdir
        if run_gate(sandbox, gate_cmd).passed:
            _git(sandbox.workdir, "add", "-A")
            return Proposal(_git(sandbox.workdir, "diff", "--cached"), pr_body, attempt)
        _git(sandbox.workdir, "reset", "--hard")         # discard the failed attempt, try fresh
        _git(sandbox.workdir, "clean", "-fd")
    # ponytail: bare retry cap. Accumulated feedback + oscillation guard (§10) matter only once a
    # real agent reacts to gate output — add them with the Claude SDK, not before.
    return None
