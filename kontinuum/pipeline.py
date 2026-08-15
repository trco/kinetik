"""Task execution pipeline core: agent edits a worktree, gate checks it, retry -> proposal (§10).

Fake-first: any object satisfying the `AgentRunner` / `Reviewer` contracts in `agent.py` works here
— the Claude adapter and the test fakes drop into the same seam. Opening the PR from a Proposal
happens at the credentialed effect boundary (`effects.open_pr`), not here.
"""

from __future__ import annotations

from dataclasses import dataclass

from kontinuum.effects import scan_diff
from kontinuum.gitcmd import git


@dataclass
class Task:
    number: int
    title: str = ""
    body: str = ""


@dataclass
class Verdict:
    approved: bool
    reason: str = ""


@dataclass
class Proposal:
    diff: str
    pr_body: str
    attempts: int


def propose(sandbox, task: Task, gate_cmd: str, agent, reviewer=None, max_attempts: int = 3) -> Proposal | None:
    """Run agent -> gate up to max_attempts, feeding failures back. Clean pass -> Proposal, else None.

    The agent gets the previous failure (gate log or secret findings) as `feedback` so it can fix it.
    Oscillation guard: if two attempts produce the identical diff, stop early — it isn't converging.
    """
    feedback = ""
    recent: list[str] = []                               # last few diffs, to catch A/B/A oscillation
    for attempt in range(1, max_attempts + 1):
        pr_body = agent.run(sandbox, task, feedback)     # edits the worktree at sandbox.workdir
        gate = sandbox.run("sh", "-c", gate_cmd)         # the local gate, offline in the sandbox
        gate_passed = gate.returncode == 0
        git(sandbox.workdir, "add", "-A")
        diff = git(sandbox.workdir, "diff", "--cached")
        secrets = scan_diff(diff)
        ok = bool(diff.strip()) and gate_passed and not secrets   # empty diff = the agent did nothing
        # only spend a review once the change is real, green and secret-free
        verdict = reviewer.review(diff, task) if (reviewer and ok) else Verdict(True)
        if ok and verdict.approved:
            return Proposal(diff, pr_body, attempt)

        if not diff.strip():
            feedback = "You made no changes — implement the issue by editing files."
        elif not gate_passed:
            feedback = f"The gate command failed — fix the code so it passes:\n{(gate.stdout + gate.stderr)[-1500:]}"
        elif secrets:
            feedback = f"The change adds secrets ({', '.join(secrets)}); remove them."
        else:
            feedback = f"A reviewer rejected the change: {verdict.reason}"
        stuck = diff in recent                           # seen this diff before -> not converging (incl. cycles)
        recent = (recent + [diff])[-3:]
        git(sandbox.workdir, "reset", "--hard")          # discard the failed attempt, try fresh
        git(sandbox.workdir, "clean", "-fd")
        if stuck:
            break
    return None
