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

from kontinuum.claim import ClaimEntry, Kind

MARKER = "kontinuum-claim"


def format_marker(kind: Kind, owner: str, epoch: int, lease_until: datetime) -> str:
    # ponytail: lease_until is naive UTC by convention — every instance must use UTC.
    # Make it tz-aware if that ever stops being guaranteed.
    payload = {"kind": kind.value, "owner": owner, "epoch": epoch, "lease_until": lease_until.isoformat()}
    return f"<!-- {MARKER} {json.dumps(payload, separators=(',', ':'))} -->"


def parse_marker(comment_id: int, body: str) -> ClaimEntry | None:
    """Pull a claim marker out of a comment body; None if it isn't one / is malformed."""
    if MARKER not in body:
        return None
    try:
        data = json.loads(body[body.index("{"): body.rindex("}") + 1])
        return ClaimEntry(comment_id, Kind(data["kind"]), data["owner"],
                          int(data["epoch"]), datetime.fromisoformat(data["lease_until"]))
    except (ValueError, KeyError):
        return None


class GitHubIssueQueue:
    """One issue's claim log, backed by `gh`. Duck-types the same 3 methods as the fake."""

    def __init__(self, repo: str, number: int, bot_login: str):
        self.repo = repo            # e.g. "trco/kontinuum"
        self.number = number
        self.bot_login = bot_login  # only THIS author's markers count — a forged one is ignored

    def read_claim_log(self) -> list[ClaimEntry]:
        # --jq '.[]' flattens all pages into one JSON object per line.
        # ponytail: reads the whole log; heartbeats grow it. Compact/retain via the Janitor later.
        out = self._gh("api", f"repos/{self.repo}/issues/{self.number}/comments", "--paginate", "--jq", ".[]")
        entries = []
        for line in out.splitlines():
            if not line.strip():
                continue
            c = json.loads(line)
            if c["user"]["login"] != self.bot_login:
                continue
            entry = parse_marker(c["id"], c["body"])
            if entry is not None:
                entries.append(entry)
        return entries

    def append(self, kind: Kind, owner: str, epoch: int, lease_until: datetime) -> None:
        body = format_marker(kind, owner, epoch, lease_until)
        self._gh("api", f"repos/{self.repo}/issues/{self.number}/comments", "-f", f"body={body}")

    def set_label(self, label: str) -> None:
        self._gh("issue", "edit", str(self.number), "--repo", self.repo, "--add-label", f"kontinuum:{label}")

    def _gh(self, *args: str) -> str:
        return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout
