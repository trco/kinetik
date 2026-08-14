"""Task execution pipeline core: agent edits a worktree, gate checks it, retry -> proposal (§10).

Fake-first: any AgentRunner exposing `run(sandbox, issue) -> pr_body` works here — the real Claude
Agent SDK drops into the same seam. The Reviewer is the next increment; opening the PR from a
Proposal is the Effect Broker (effects.open_pr).
"""

from __future__ import annotations

from dataclasses import dataclass

from kontinuum.effects import scan_diff
from kontinuum.gitcmd import git
from kontinuum.verify import run_gate


@dataclass
class Task:
    number: int
    title: str = ""
    body: str = ""


@dataclass
class Proposal:
    diff: str
    pr_body: str
    attempts: int


def propose(sandbox, task: Task, gate_cmd: str, agent, max_attempts: int = 3) -> Proposal | None:
    """Run agent -> gate up to max_attempts, feeding failures back. Clean pass -> Proposal, else None.

    The agent gets the previous failure (gate log or secret findings) as `feedback` so it can fix it.
    Oscillation guard: if two attempts produce the identical diff, stop early — it isn't converging.
    """
    feedback = ""
    last_diff = None
    for attempt in range(1, max_attempts + 1):
        pr_body = agent.run(sandbox, task, feedback)     # edits the worktree at sandbox.workdir
        gate = run_gate(sandbox, gate_cmd)
        git(sandbox.workdir, "add", "-A")
        diff = git(sandbox.workdir, "diff", "--cached")
        if gate.passed and not scan_diff(diff):          # gate green AND no secret in the diff
            return Proposal(diff, pr_body, attempt)

        secrets = scan_diff(diff)
        feedback = (f"The change adds secrets ({', '.join(secrets)}); remove them." if gate.passed
                    else f"The gate command failed — fix the code so it passes:\n{gate.log[-1500:]}")
        stuck = diff == last_diff                        # same diff again -> not converging
        last_diff = diff
        git(sandbox.workdir, "reset", "--hard")          # discard the failed attempt, try fresh
        git(sandbox.workdir, "clean", "-fd")
        if stuck:
            break
    return None
