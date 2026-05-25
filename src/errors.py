from __future__ import annotations


class JobRejectedError(Exception):
    """Raised when a scheduler permanently rejects a job (e.g. memory exceeds partition size)."""
