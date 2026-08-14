"""Ephemeral, credential-free, network-restricted container for a task's commands (§3, §8).

Two modes on the same locked-down container:
  • run()                 — one-off `docker run --rm` for a single command (the gate).
  • start()/exec()/stop() — a persistent container to run MANY commands sharing state (an agent
    installing deps, running tests, iterating). Use as a context manager: `with Sandbox(...) as box`.

No host env is forwarded, --network none blocks egress, --cap-drop ALL + no-new-privileges stop
escalation, and only the git worktree (mounted at /work) is visible.
"""

from __future__ import annotations

import subprocess


class Sandbox:
    def __init__(self, image: str, workdir: str, network: str = "none"):
        self.image = image        # the repo's toolchain image (a base like alpine for probes)
        self.workdir = workdir    # absolute host path (the git worktree), mounted at /work
        # ponytail: 'none' = zero egress. Swap for a proxy/allowlist when the AI (or deps) need
        # the network (#2) — don't widen it before then.
        self.network = network
        self._cid: str | None = None

    def _flags(self) -> list[str]:
        # ponytail: root-in-container writes root-owned files on Linux; add --user when we run there.
        return ["--network", self.network, "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "-v", f"{self.workdir}:/work", "-w", "/work"]

    def run(self, *cmd: str, timeout: int = 600) -> subprocess.CompletedProcess:
        """One-off: run a single command in a throwaway container."""
        return subprocess.run(["docker", "run", "--rm", *self._flags(), self.image, *cmd],
                              capture_output=True, text=True, timeout=timeout)

    def start(self) -> "Sandbox":
        """Open a persistent container (kept alive) to exec many commands into."""
        self._cid = subprocess.run(
            ["docker", "run", "-d", *self._flags(), self.image, "tail", "-f", "/dev/null"],
            check=True, capture_output=True, text=True).stdout.strip()
        return self

    def exec(self, *cmd: str, timeout: int = 600) -> subprocess.CompletedProcess:
        """Run a command in the open container — filesystem/env state persists between calls."""
        return subprocess.run(["docker", "exec", "-w", "/work", self._cid, *cmd],
                              capture_output=True, text=True, timeout=timeout)

    def stop(self) -> None:
        if self._cid:
            subprocess.run(["docker", "rm", "-f", self._cid], capture_output=True, text=True)
            self._cid = None

    @property
    def container_id(self) -> str | None:
        return self._cid

    def __enter__(self) -> "Sandbox":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
