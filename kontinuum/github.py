"""GitHub adapter for the claim log: ClaimEntry <-> issue-comment markers, via `gh`.

The pure marker (format/parse) is unit-tested offline; the `gh` calls are thin I/O that
needs a live issue to smoke-test. Writes (append/set_label) are the privileged half and
will run through the Effect Broker once it exists (step 3); for now they use ambient `gh`
auth so the claim log works end-to-end.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime

from kontinuum.claim import ClaimEntry, ClaimKind

MARKER = "kontinuum-claim"
STATE_LABELS = ("ready", "claimed", "pr-open", "blocked", "needs-triage")   # mutually-exclusive projection


def format_marker(kind: ClaimKind, owner: str, epoch: int, lease_until: datetime) -> str:
    # ponytail: lease_until is naive UTC by convention — every instance must use UTC.
    # Make it tz-aware if that ever stops being guaranteed.
    payload = {"kind": kind.value, "owner": owner, "epoch": epoch, "lease_until": lease_until.isoformat()}
    return f"<!-- {MARKER} {json.dumps(payload, separators=(',', ':'))} -->"


def parse_marker(comment_id: int, body: str) -> ClaimEntry | None:
    """Pull a claim marker out of a comment body; None if it isn't one / is malformed."""
    if MARKER not in body:
        return None
    try:
        payload = body.split(MARKER, 1)[1].rsplit("-->", 1)[0].strip()   # JSON between the marker and -->
        data = json.loads(payload)                        # values may contain braces; json.loads handles it
        return ClaimEntry(comment_id, ClaimKind(data["kind"]), data["owner"],
                          int(data["epoch"]), datetime.fromisoformat(data["lease_until"]))
    except (ValueError, KeyError):
        return None


def gh(*args: str) -> str:
    """Run `gh` and return stdout. Writes here are the privileged half — the Effect Broker (step 3)."""
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


LABEL_COLORS = {"ready": "0e8a16", "claimed": "fbca04", "pr-open": "1d76db", "blocked": "b60205",
                "needs-triage": "d4c5f9", "hold": "e99695", "paused": "cccccc"}


def init_labels(repo: str) -> None:
    """Create the kontinuum:* labels so set_label never fails on a fresh repo. Idempotent."""
    for name, color in LABEL_COLORS.items():
        subprocess.run(["gh", "label", "create", f"kontinuum:{name}", "--repo", repo, "--color", color],
                       capture_output=True, text=True)   # already-exists is fine, ignore


def parse_claim_log(comments: list[dict], bot_login: str) -> list[ClaimEntry]:
    """Turn raw issue comments into claim entries, trusting ONLY the bot's own comments.

    This is the anti-spoof boundary: a human posting a `<!-- kontinuum-claim -->` marker is
    ignored, so nobody can forge ownership by commenting.
    """
    entries = []
    for c in comments:
        if c["user"]["login"] != bot_login:
            continue
        entry = parse_marker(c["id"], c["body"])
        if entry is not None:
            entries.append(entry)
    return entries


class GitHubIssueQueue:
    """One issue's claim log, backed by `gh`. Duck-types the same 3 methods as the fake."""

    def __init__(self, repo: str, number: int, bot_login: str):
        self.repo = repo            # e.g. "trco/kontinuum"
        self.number = number
        self.bot_login = bot_login  # only THIS author's markers count — a forged one is ignored

    def read_claim_log(self) -> list[ClaimEntry]:
        # --jq '.[]' flattens all pages into one JSON object per line.
        # ponytail: reads the whole log; heartbeats grow it. Compact/retain via the Janitor later.
        out = gh("api", f"repos/{self.repo}/issues/{self.number}/comments", "--paginate", "--jq", ".[]")
        comments = [json.loads(line) for line in out.splitlines() if line.strip()]
        return parse_claim_log(comments, self.bot_login)

    def append_entry(self, kind: ClaimKind, owner: str, epoch: int, lease_until: datetime) -> None:
        body = format_marker(kind, owner, epoch, lease_until)
        gh("api", f"repos/{self.repo}/issues/{self.number}/comments", "-f", f"body={body}")

    def labels(self) -> list[str]:
        out = gh("issue", "view", str(self.number), "--repo", self.repo, "--json", "labels", "--jq", ".labels[].name")
        return [line for line in out.splitlines() if line.strip()]

    def set_label(self, label: str) -> None:
        """Set the exclusive state label: add it and remove any OTHER kontinuum state label.

        Without this, `ready` is never cleared and poll_workable keeps re-selecting a worked issue.
        """
        keep = f"kontinuum:{label}"
        remove = [l for l in self.labels()
                  if l.startswith("kontinuum:") and l.rsplit(":", 1)[-1] in STATE_LABELS and l != keep]
        args = ["issue", "edit", str(self.number), "--repo", self.repo, "--add-label", keep]
        for r in remove:
            args += ["--remove-label", r]
        gh(*args)

    def comment(self, body: str) -> None:
        gh("issue", "comment", str(self.number), "--repo", self.repo, "--body", body)
