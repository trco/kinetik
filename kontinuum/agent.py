"""The agent seam: the contract a backend implements, plus the Claude adapter (§2, option #2).

Two ports, duck-typed like the rest of K — the Protocols below are that contract written where a
type checker (and the next adapter author) can read it:

    AgentRunner.run(sandbox, task, feedback="") -> str   # edits the worktree, returns the PR body
    Reviewer.review(diff, task) -> Verdict               # read-only opinion on the staged diff

The adapter's side of it: edit only files under `sandbox.workdir`, run every command through the
sandbox rather than on the host, do nothing outward — push/PR/comment/label are orchestrator-only
(`effects.py`) — and return a short markdown PR body.

CONTAINMENT — what K enforces vs what it trusts:

  Enforced, whatever the backend does: every command routed into `Sandbox` runs with no host env
  forwarded, `--network none`, `--cap-drop ALL` and only the worktree mounted, so it has no
  credentials to read and nowhere to send them. Only the worktree diff can reach a PR, and it is
  secret-scanned first; outward effects stay orchestrator-side.

  Not enforced: the adapter process itself. It runs on the host and inherits K's environment —
  which on a headless install holds ANTHROPIC_API_KEY (see Auth below). Tool flags such as
  `--disallowedTools Bash` keep a well-behaved CLI on the contract; they are a convenience, not the
  boundary. A backend that ignores them can read host files and its own key.

  So: the sandbox is the boundary, the CLI's flags are not. Run K with a host env carrying no
  secrets beyond the agent's own credential, and register only adapters trusted to keep the
  contract. Containing a less-trusted backend means boxing its process too — deferred (§16 trust
  tiers), and a prerequisite for adopting one, not something the flags already give us.

Adding a backend: implement the Protocol(s) here, then map a name to it in `cli.py`, where the
config's `agent:` selector picks the runner. (A (role, task) router is §16 Later; an `if` per name
is plenty while there are two.)

Auth: on a laptop the CLI uses the existing Claude Code login (no key). On a server/cron with no
interactive login, set ANTHROPIC_API_KEY — the CLI picks it up from the inherited env.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from typing import Protocol, runtime_checkable

from kontinuum.pipeline import Task, Verdict

_MCP_SERVER = os.path.join(os.path.dirname(__file__), "sandbox_mcp.py")
_NO_TOOLS = ["Bash", "Edit", "Write", "Read", "Glob", "Grep"]


@runtime_checkable
class AgentRunner(Protocol):
    """Implements the issue by editing the worktree at `sandbox.workdir`; returns the PR body.

    `feedback` is the previous attempt's failure (gate log, secret findings or reviewer reason) —
    empty on the first try. Return even when the attempt went badly: the gate judges the worktree,
    and a raise aborts the whole task instead (`poll_once` counts the error and drops the claim).
    """

    def run(self, sandbox, task: Task, feedback: str = "") -> str: ...


@runtime_checkable
class Reviewer(Protocol):
    """Second opinion on the staged diff. Read-only: it must not touch the worktree (§10)."""

    def review(self, diff: str, task: Task) -> Verdict: ...


def _prompt(task: Task) -> str:
    return (
        f"Implement this GitHub issue by editing files in your working directory.\n\n"
        f"Issue #{task.number}: {task.title}\n\n{task.body}\n\n"
        "To run commands (tests, scripts, checks), use the `run` tool from the kontinuum-sandbox "
        "MCP server — it executes in a sandbox where the repo is at /work and there is NO network. "
        "Do not use any other shell. When finished, reply with a concise markdown summary of what "
        "you changed — a sentence of intent, then short bullets for the notable changes. It is "
        "placed under a `## Summary` heading in the pull request, so add no heading of your own."
    )


class ClaudeAgentRunner:
    """`AgentRunner` over the `claude` CLI: reasoning + edits on the host, commands via the box.

    The login stays on the host; commands reach the sandbox through the `run` MCP tool. Restricting
    the CLI's own tools (`--allowedTools` / `--disallowedTools Bash`) keeps it on the contract —
    containment itself comes from the sandbox (see module docstring).
    """

    def __init__(self, python_exe: str = sys.executable, timeout: int = 1200):
        self.python_exe = python_exe    # runs the MCP server (pure stdlib; any python works)
        self.timeout = timeout

    def run(self, sandbox, task: Task, feedback: str = "") -> str:
        sandbox.start()                 # persistent box; the agent's commands exec into it
        try:
            cfg = {"mcpServers": {"kontinuum-sandbox": {
                "command": self.python_exe, "args": [_MCP_SERVER],
                "env": {"KONTINUUM_SANDBOX_CID": sandbox.container_id}}}}
            cfgpath = os.path.join(tempfile.mkdtemp(), "mcp.json")
            with open(cfgpath, "w") as f:
                json.dump(cfg, f)
            prompt = _prompt(task)
            if feedback:
                prompt += f"\n\nA previous attempt failed. Fix it based on this:\n{feedback}"
            try:
                r = subprocess.run(
                    ["claude", "-p", prompt, "--mcp-config", cfgpath, "--strict-mcp-config",
                     "--allowedTools", "Read", "Edit", "Write", "mcp__kontinuum-sandbox__run",
                     "--disallowedTools", "Bash", "--permission-mode", "acceptEdits"],
                    cwd=sandbox.workdir, capture_output=True, text=True, timeout=self.timeout)
                out = (r.stdout or "").strip()
            except subprocess.TimeoutExpired:
                out = f"(agent timed out after {self.timeout}s)"   # gate/empty-diff check handles the result
            return out or f"Implements #{task.number}"
        finally:
            sandbox.stop()

    def run_command(self, workdir: str, command: str) -> None:
        """Optional capability: run an injected slash command headless in the worktree (Read/Edit/Write).

        Drives the living-docs plugin (`/docs-update`, `/docs-seed`) — the prompt lives in the bundled
        command file, not here. A docs-only pass: no commands to run, so no sandbox/MCP; no Bash, since
        K owns git and passes any paths in the command's arguments. Best-effort: a timeout is swallowed
        by the caller, which never lets docs block the code PR.
        """
        try:
            subprocess.run(
                ["claude", "-p", command, "--allowedTools", "Read", "Edit", "Write", "Glob", "Grep",
                 "--disallowedTools", "Bash", "--permission-mode", "acceptEdits"],
                cwd=workdir, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            pass                                          # best-effort; the caller continues


class ClaudeReviewer:
    """`Reviewer` over the CLI: independent, read-only. One input, not the trust anchor (§10)."""

    def __init__(self, timeout: int = 600):
        self.timeout = timeout

    def review(self, diff: str, task: Task) -> Verdict:
        prompt = (
            f"Review this diff for issue #{task.number}: {task.title}\n\n{task.body}\n\n"
            f"DIFF:\n{diff}\n\n"
            "Does it correctly and safely address the issue? Answer with APPROVE or REJECT on the "
            "first line, then a one-sentence reason."
        )
        try:
            r = subprocess.run(
                ["claude", "-p", prompt, "--disallowedTools", *_NO_TOOLS, "--permission-mode", "acceptEdits"],
                capture_output=True, text=True, timeout=self.timeout)
            out = (r.stdout or "").strip()
        except subprocess.TimeoutExpired:
            return Verdict(False, "reviewer timed out")
        approved = out.lstrip().upper().startswith("APPROVE")   # verdict is the first token; empty -> reject
        return Verdict(approved, out[:300] or "reviewer produced no output")
