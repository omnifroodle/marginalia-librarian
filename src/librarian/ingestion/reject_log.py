"""Persistent reject log for files that fail ingestion.

Written immediately on failure so restarts don't re-burn LLM budget on known-bad files.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path


class RejectLog:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}
        if path.exists():
            with open(path) as f:
                self._entries = json.load(f)

    def is_rejected(self, doc_id: str) -> dict | None:
        """Return the rejection record if this doc_id is known-bad, else None."""
        return self._entries.get(doc_id)

    def record_failure(self, doc_id: str, filename: str, file_path: Path, error: str) -> None:
        """Record a failure immediately, flushing to disk before returning."""
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            if doc_id in self._entries:
                self._entries[doc_id]["attempts"] += 1
                self._entries[doc_id]["last_failed_at"] = now
                self._entries[doc_id]["last_error"] = error
            else:
                self._entries[doc_id] = {
                    "filename": filename,
                    "file_path": str(file_path),
                    "first_failed_at": now,
                    "last_failed_at": now,
                    "last_error": error,
                    "attempts": 1,
                }
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w") as f:
                json.dump(self._entries, f, indent=2)

    def clear(self, doc_id: str) -> None:
        """Remove a doc_id from the reject log (e.g. after --force succeeds)."""
        with self._lock:
            if doc_id in self._entries:
                del self._entries[doc_id]
                with open(self._path, "w") as f:
                    json.dump(self._entries, f, indent=2)
