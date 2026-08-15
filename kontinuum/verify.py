"""The local gate: run the repo's build/lint/test command inside the sandbox (§9).

MVP is a single gate command (from verify.yaml's `gate.run`, loaded at onboarding). Heavy e2e is
delegated to the repo's existing CI on the pushed branch — K reads CI status, it doesn't run e2e.

The gate is pass/fail only: a non-zero exit is the failure signal, and the combined output is
handed back verbatim as feedback rather than parsed.
"""

from __future__ import annotations

from dataclasses import dataclass

from kontinuum.sandbox import Sandbox


@dataclass
class GateResult:
    passed: bool
    log: str


def run_gate(sandbox: Sandbox, gate_cmd: str) -> GateResult:
    """Run the gate in the sandbox. passed = exit 0. Log is combined stdout+stderr for feedback."""
    r = sandbox.run("sh", "-c", gate_cmd)
    return GateResult(passed=(r.returncode == 0), log=r.stdout + r.stderr)
