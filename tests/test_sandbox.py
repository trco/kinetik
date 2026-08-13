"""Sandbox isolation probes — assert the three security properties. Require the Docker daemon."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from kontinuum.sandbox import Sandbox

IMAGE = "alpine:latest"


def _docker_ok() -> bool:
    return bool(shutil.which("docker")) and \
        subprocess.run(["docker", "info"], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not _docker_ok(), reason="docker daemon not available")


def test_host_env_does_not_leak(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_SECRET", "sk-do-not-leak")     # a secret in K's own environment
    out = Sandbox(IMAGE, str(tmp_path)).run("env").stdout
    assert "FAKE_SECRET" not in out and "sk-do-not-leak" not in out


def test_egress_is_blocked(tmp_path):
    r = Sandbox(IMAGE, str(tmp_path)).run("wget", "-T", "3", "-q", "-O", "-", "http://example.com")
    assert r.returncode != 0                                # --network none → nowhere to exfiltrate


def test_workdir_is_the_writable_surface(tmp_path):
    Sandbox(IMAGE, str(tmp_path)).run("sh", "-c", "echo hello > /work/out.txt")
    assert (tmp_path / "out.txt").read_text().strip() == "hello"   # container edits land in the worktree
