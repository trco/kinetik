"""Claude agent runner (§2, option #2): drives the `claude` CLI on the host.

The login stays on the host. File edits go to the worktree (cwd); the agent's *commands* run in the
sandbox via the `run` tool (host Bash disabled), so a hijacked command is contained.

Auth: on your laptop it uses the existing Claude Code login (no key). On a server/cron with no
interactive login, set ANTHROPIC_API_KEY in the environment — the CLI picks it up automatically
(subprocess inherits the env), so nothing else changes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

from kontinuum.pipeline import Task, Verdict

_MCP_SERVER = os.path.join(os.path.dirname(__file__), "sandbox_mcp.py")
_NO_TOOLS = ["Bash", "Edit", "Write", "Read", "Glob", "Grep"]


def _prompt(task: Task) -> str:
    return (
        f"Implement this GitHub issue by editing files in your working directory.\n\n"
        f"Issue #{task.number}: {task.title}\n\n{task.body}\n\n"
        "To run commands (tests, scripts, checks), use the `run` tool from the kontinuum-sandbox "
        "MCP server — it executes in a sandbox where the repo is at /work and there is NO network. "
        "Do not use any other shell. When finished, reply with a one-paragraph summary of what you "
        "changed — it becomes the pull-request description."
    )


class ClaudeAgentRunner:
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
            r = subprocess.run(
                ["claude", "-p", prompt, "--mcp-config", cfgpath, "--strict-mcp-config",
                 "--allowedTools", "Read", "Edit", "Write", "mcp__kontinuum-sandbox__run",
                 "--disallowedTools", "Bash", "--permission-mode", "acceptEdits"],
                cwd=sandbox.workdir, capture_output=True, text=True, timeout=self.timeout)
            return (r.stdout or "").strip() or f"Implements #{task.number}"
        finally:
            sandbox.stop()


class ClaudeReviewer:
    """Independent, read-only second opinion on the diff. One input, not the trust anchor (§10)."""

    def __init__(self, timeout: int = 600):
        self.timeout = timeout

    def review(self, diff: str, task: Task) -> Verdict:
        prompt = (
            f"Review this diff for issue #{task.number}: {task.title}\n\n{task.body}\n\n"
            f"DIFF:\n{diff}\n\n"
            "Does it correctly and safely address the issue? Answer with APPROVE or REJECT on the "
            "first line, then a one-sentence reason."
        )
        r = subprocess.run(
            ["claude", "-p", prompt, "--disallowedTools", *_NO_TOOLS, "--permission-mode", "acceptEdits"],
            capture_output=True, text=True, timeout=self.timeout)
        out = (r.stdout or "").strip()
        approved = "REJECT" not in out.split("\n", 1)[0].upper()   # default to approve unless it clearly rejects
        return Verdict(approved, out[:300])
