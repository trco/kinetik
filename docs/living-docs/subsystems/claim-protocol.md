---
title: Claim Protocol
type: subsystem
summary: Epoch-leased, append-only claim log in GitHub issue comments — how a K instance takes, holds, and gives up sole ownership of an issue.
sources:                   # flat single-package repo: modules, not a dir glob (kontinuum/** = whole codebase)
  - kontinuum/claim.py
  - kontinuum/protocol.py
  - kontinuum/github.py
last_verified:
  date: 2026-08-16
  sha: seed
related: [subsystems/daemon-loop, adr/0001-claim-log-in-github-issue-comments]
---

## What it does

Lets several Kontinuum instances (one per developer machine) share one GitHub issue queue without ever working the same issue twice — with no database, no lock server, no local state.

Ownership truth is an **append-only log of issue comments**, not labels. Comments are immutable and GitHub hands out monotonically increasing IDs, so every reader derives the *same* total order from the same log. Ownership is then a pure function of `(ordered log, now)`.

Two mechanisms decide it:

- **Epoch** — a generation counter. A new claim is always appended at `max_epoch + 1`, so a fresh claim supersedes every earlier one, even one whose `lease_until` has not technically passed yet. This is what makes reclaim safe rather than a fight.
- **Lease** — a wall-clock deadline the owner refreshes with heartbeats. If the owner crashes, the heartbeats stop, the lease lapses, and the issue becomes free for anyone. Recovery costs nothing because nothing durable was held.

Only the configured bot account's comments are parsed (`kontinuum/github.py:66`). That is the anti-spoof boundary: a human (or an attacker) pasting a valid-looking `<!-- kontinuum-claim ... -->` marker into the thread is ignored outright, so ownership can't be forged by commenting. `bot_login` is therefore a security-relevant config value, not cosmetic.

Labels (`kontinuum:claimed` etc.) are a **projection for humans**, never consulted for ownership. A human editing labels cannot grant or revoke a claim — only append-only log entries can.

## The three-layer split

The split exists so the correctness-critical part has no I/O and can be property-tested exhaustively.

| Layer | File | Role |
|---|---|---|
| Pure decision | `kontinuum/claim.py` | Who owns it? Should I claim, and at what epoch? No clock, no network — `now` is a parameter. |
| Protocol steps | `kontinuum/protocol.py` | Sequences read → append → read-back against a duck-typed `queue`. |
| GitHub adapter | `kontinuum/github.py` | `ClaimEntry` ↔ comment marker, plus the `gh` calls. |

`protocol.py` takes any object exposing `read_claim_log` / `append_entry` / `set_label`, which is why the whole protocol is tested against an in-memory fake (`tests/fakes.py`) with zero mocking of GitHub. Writes are the privileged half — they run orchestrator-side on ambient `gh` auth, never inside the credential-free agent sandbox.

## Key entry points

- `resolve_owner` — the whole ownership rule, ~10 lines: `kontinuum/claim.py:34`
- First-claimer-wins tie-break: `kontinuum/claim.py:50`
- Owner-matched release check: `kontinuum/claim.py:51`
- Heartbeats extend the lease (`max` over the owner's entries at that epoch): `kontinuum/claim.py:53`
- `plan_claim` (skip vs. claim at `max_epoch+1`): `kontinuum/claim.py:63`
- `attempt_claim` — the call the loop makes: `kontinuum/protocol.py:15`
- Read-back that breaks same-tick ties: `kontinuum/protocol.py:26`
- `still_owns` guard before every effect: `kontinuum/protocol.py:38`
- Marker format / parse: `kontinuum/github.py:27`, `kontinuum/github.py:37`
- Trust filter on comment author: `kontinuum/github.py:66`
- Single-owner property test (Hypothesis, random interleavings): `tests/test_claim.py:97`

## Gotchas / non-obvious

- **First claimer wins, not the latest.** MVP §7's original wording ("latest comment_id") is wrong and was corrected here: latest-wins lets a later same-epoch claim steal an *already-confirmed* claim. The Hypothesis property test found this. See the reasoning in the `resolve_owner` docstring, `kontinuum/claim.py:35`.
- **A release only counts if it matches the winning owner.** An owner-blind release would let the loser of a same-epoch tie release the winner and starve the issue — `tests/test_claim.py:46` pins this.
- **`lease_until` is naive UTC by convention**, not tz-aware. Every instance must use UTC (`datetime.utcnow()`); an instance writing local time would corrupt ownership for everyone. Flagged at `kontinuum/github.py:28`. Making it tz-aware is the fix if that guarantee ever weakens.
- **Heartbeat markers render as nothing on purpose.** Claim and release get a visible human line; heartbeat is an HTML comment only, because it fires every ~LEASE/3 for the whole task and would otherwise bury the issue thread in bot noise (`kontinuum/github.py:21`). The consequence: a claimed issue looks quiet in the UI while very much being worked.
- **Visible text is a prefix, never a suffix.** `parse_marker` reads the JSON between `MARKER` and the closing `-->`, so appending text after the marker breaks parsing (`kontinuum/github.py:34`).
- **Malformed markers are skipped, not raised.** `parse_marker` returns `None` on bad JSON or missing keys (`kontinuum/github.py:46`), so one corrupt comment degrades to "not an entry" instead of wedging the log for every reader forever.
- **Reclaiming an expired lease bumps the epoch.** The dead owner's old entries stay in the log and stay parseable — they're simply outranked by the higher epoch. Nothing is ever deleted or edited, which is what keeps the log a valid audit trail.
- **Already-ours re-poll heartbeats instead of re-claiming** (`kontinuum/protocol.py:18`). Re-claiming would inflate the epoch every pass and invite churn against other instances.
- **Losing the read-back tie appends an explicit release** (`kontinuum/protocol.py:29`) rather than staying silent, so the loser's claim doesn't linger as a live-looking entry.
- **The log is read in full, every time.** Heartbeats grow it without bound; there's no compaction yet (flagged at `kontinuum/github.py:92`, deferred to the Janitor, MVP §16). Long-lived issues get progressively slower to poll.
- **Ownership is re-derived, never cached.** `still_owns` re-reads the log before every state transition and effect. Holding a claim in a variable across an agent run would be wrong — a human `hold` or a lapsed lease must be able to stop the work mid-flight.

## See also

- `docs/kontinuum-mvp.md` §7 — the spec this implements (note the corrected tie-break above).
- `kontinuum/loop.py:71` — `LeaseHeartbeat`, the background thread that keeps the lease alive for the duration of a task; interval defaults to `lease/3` at `kontinuum/loop.py:41`.
- `kontinuum/loop.py:116` — the guard pattern: `hold`/`blocked` label **or** lost lease ⇒ release and open no PR.
