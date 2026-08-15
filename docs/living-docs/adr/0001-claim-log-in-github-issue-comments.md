---
title: "ADR 0001: Claim log in GitHub issue comments"
type: adr
summary: Mutual exclusion between Kontinuum instances is an append-only, epoch-leased claim log built from GitHub issue comments — no database, no lock service, no local state.
sources:
  - kontinuum/claim.py
  - kontinuum/protocol.py
  - kontinuum/github.py
last_verified:
  date: 2026-08-16
  sha: seed
related: [subsystems/claim-protocol]
---

**Status:** Accepted (MVP §7)

## Context

Several Kontinuum instances run on different developers' machines and poll the same repos. Two of them picking up the same issue means two agents, two branches, two PRs for one ticket — so instances need mutual exclusion.

They share nothing: private laptops, no server, no shared network, no operational appetite for a lock service. The one store all of them already reach and are already authenticated against is GitHub, via ambient `gh` auth. Issue comments there are immutable, serialized by GitHub, and carry monotonically increasing IDs — a total order every reader agrees on without coordination (`kontinuum/claim.py:24`).

## Decision

Ownership truth is an **append-only log of issue comments**. Each entry is a hidden `<!-- kontinuum-claim {...} -->` marker carrying `kind` (claim/heartbeat/release), `owner` (`instance_id`), `epoch`, and `lease_until` (`kontinuum/github.py:27`).

- Ownership is a **pure function** of the ordered log plus `now` — no I/O, no clock injection (`kontinuum/claim.py:34`). This is what makes the single-owner invariant property-testable without mocks (`tests/test_claim.py:97`).
- Claiming is read → append at `max_epoch+1` → **read back**; a loser appends a release and yields (`kontinuum/protocol.py:15`).
- The owner heartbeats at the same epoch to extend the lease; reclaim after expiry always happens at a strictly higher epoch.
- Only the configured `bot_login`'s comments are parsed, so a human pasting a marker cannot forge ownership (`kontinuum/github.py:66`).
- Labels (`kontinuum:claimed`, …) are a **projection** for humans, never the lock (`kontinuum/github.py:105`).

There is no database and no local state: every ownership decision re-reads the log (`still_owns`, `kontinuum/protocol.py:38`).

## Consequences

What it buys:

- **Crash recovery is free.** Process dies → heartbeats stop → lease lapses → any instance reclaims at `epoch+1`. No cleanup path, no orphan reaper, no state to reconcile on restart.
- **The audit trail is the product.** Claim and release render human text in the thread (`kontinuum/github.py:21`); "who was working on this and when" is just the issue.
- **A human can always take over.** Ownership and the `hold`/`blocked` labels are re-checked before every effect, and the instance releases cleanly instead of opening a PR.
- **Zero new infrastructure and zero new secrets.** Adding an instance is `gh auth login`.

What it costs — real, accepted limits:

- **Eventual consistency.** Correctness of the tie-break rests on the read-back seeing concurrent same-epoch claims. Under GitHub replica lag it may not, and two instances could both believe they won until a lease expires. Untested against real lag; the 2-instance soak has not been run (MVP §17).
- **Ordering assumption.** Comment IDs are assumed monotonic per issue and identically ordered for all readers. If that ever stops holding, the winner is undefined.
- **API rate limits and log growth.** Every guard re-reads the *whole* paginated comment list (`kontinuum/github.py:90`), and heartbeats append forever. Compaction is deferred to the Janitor (MVP §16) — noted as a `ponytail:` ceiling in the code.
- **Comment noise.** Heartbeats are marker-only (deliberately no visible text) but are still comments; a long task leaves a trail of them on the issue.
- **Trust is only as narrow as one bot login.** Anyone holding that token can append any `owner`. Worse, two instances configured with *different* `bot_login` values read disjoint logs and will not exclude each other at all.
- **Clock assumptions.** `lease_until` is naive UTC compared against each reader's local clock (`kontinuum/github.py:28`). Skew silently shortens or lengthens the effective lease; nothing enforces sync.

## Deliberately rejected

- **A local DB** (the old `state.db`) — dropped so there is nothing local to corrupt or reconcile (MVP §2).
- **Labels as the lock** — humans edit labels, and label writes are not ordered; labels stayed a projection.
- **Spec §7's "latest comment_id wins" tie-break** — a later same-epoch claim could steal an already-confirmed one. The Hypothesis property test found the double-claim; first-claimer-wins replaced it (`kontinuum/claim.py:35`).
- **Owner-blind release** — a tie *loser* releasing would free the winner's issue and starve it, so a release only counts when it matches the winning owner (`tests/test_claim.py:46`).
- **Visible heartbeat comments** — one per lease tick would spam the thread (`kontinuum/github.py:20`).

## See also

- `docs/kontinuum-mvp.md` §7 (protocol), §4 (deployment model), §16 (Janitor / compaction)
- `docs/living-docs/subsystems/claim-protocol.md`
