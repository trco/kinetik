"""In-memory IssueQueue stand-in shared across tests. The real GitHub adapter duck-types it."""

from __future__ import annotations

import itertools
from datetime import datetime

from kontinuum.claim import ClaimEntry, Kind


class FakeIssueQueue:
    def __init__(self):
        self._log: list[ClaimEntry] = []
        self._ids = itertools.count()
        self.label: str | None = None

    def read_claim_log(self) -> list[ClaimEntry]:
        return list(self._log)                             # snapshot, like a GET

    def append(self, kind: Kind, owner: str, epoch: int, lease_until: datetime) -> None:
        self._log.append(ClaimEntry(next(self._ids), kind, owner, epoch, lease_until))  # server assigns id

    def set_label(self, label: str) -> None:
        self.label = label
