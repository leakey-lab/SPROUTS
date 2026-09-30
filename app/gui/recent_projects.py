"""Recent-projects MRU list: the one implementation shared by MainWindow and Welcome.

Stored in ``QSettings(ORGANIZATION_NAME, APP_NAME)`` (``"LeakeyLab"`` /
``"SPROUTS"``) under ``"recent_projects"`` as a JSON list of
``{"name", "path", "count", "ts"}`` dicts, most recent first, capped at 8 —
exactly the format ``MainWindow._record_recent_project`` has always written, so
existing lists keep working.

The store itself is Qt-free: ``settings`` is anything with ``value(key,
default)`` / ``setValue(key, value)``; only the default constructs a
``QSettings`` (lazily).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol

logger = logging.getLogger(__name__)

__all__ = ["RecentProject", "RecentProjectsStore", "SETTINGS_KEY", "MAX_ENTRIES"]

SETTINGS_KEY = "recent_projects"
MAX_ENTRIES = 8


class _Settings(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...

    def setValue(self, key: str, value: Any) -> None: ...  # noqa: N802 - Qt API name


@dataclass(frozen=True)
class RecentProject:
    """One MRU entry (``count`` = images found at the last load, ``ts`` = ISO time)."""

    path: str
    name: str
    count: int | None = None
    ts: str = ""

    def exists(self) -> bool:
        """Whether the folder is still there (may block on a network path: call off-GUI)."""
        return os.path.isdir(self.path)

    def to_dict(self) -> dict[str, Any]:
        """The legacy JSON object (key order name, path, count, ts)."""
        d = asdict(self)
        return {"name": d["name"], "path": d["path"], "count": d["count"], "ts": d["ts"]}


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


class RecentProjectsStore:
    """Load / add / remove / clear the recent-projects list."""

    def __init__(self, settings: _Settings | None = None, *, max_entries: int = MAX_ENTRIES):
        if settings is None:
            from PyQt6.QtCore import QSettings

            from app import APP_NAME, ORGANIZATION_NAME

            settings = QSettings(ORGANIZATION_NAME, APP_NAME)
        self.settings = settings
        self.max_entries = max_entries

    def entries(self) -> list[RecentProject]:
        """Valid entries, most recent first (malformed items are skipped)."""
        raw = self.settings.value(SETTINGS_KEY, "[]")
        try:
            items = json.loads(raw) if isinstance(raw, str) else list(raw or [])
        except (ValueError, TypeError, RecursionError):  # RecursionError: nested too deep (R4)
            logger.warning("Ignoring an unreadable recent-projects list")
            return []
        if not isinstance(items, list):
            return []
        out = []
        for item in items:
            if not isinstance(item, dict) or not item.get("path"):
                continue
            path = str(item["path"])
            count = item.get("count")
            out.append(
                RecentProject(
                    path=path,
                    name=str(item.get("name") or os.path.basename(os.path.normpath(path))),
                    count=int(count) if isinstance(count, (int, float)) else None,
                    ts=str(item.get("ts") or ""),
                )
            )
        return out

    def _save(self, entries: list[RecentProject]) -> None:
        payload = [e.to_dict() for e in entries[: self.max_entries]]
        self.settings.setValue(SETTINGS_KEY, json.dumps(payload))

    def add(self, path: str, count: int, *, now: datetime | None = None) -> RecentProject:
        """Put ``path`` first (deduplicated), cap the list, persist; return the entry."""
        entry = RecentProject(
            path=path,
            name=os.path.basename(os.path.normpath(path)),
            count=int(count),
            ts=(now or datetime.now()).isoformat(),
        )
        rest = [e for e in self.entries() if not _same_path(e.path, path)]
        self._save([entry, *rest])
        return entry

    def remove(self, path: str) -> None:
        """Drop ``path`` from the list (no-op when absent)."""
        self._save([e for e in self.entries() if not _same_path(e.path, path)])

    def clear(self) -> None:
        """Forget every recent project."""
        self._save([])
