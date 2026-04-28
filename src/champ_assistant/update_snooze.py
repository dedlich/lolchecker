"""Update notification snooze persistence.

When the user clicks "Später erinnern" on the update banner, we record
two things:
  * the tag they snoozed (so a *newer* tag still surfaces — never silently
    suppress an actually-newer release),
  * a timestamp until which we suppress the same tag.

Default snooze is 24h — short enough that an actively-developed branch
gets re-prompted soon, long enough that the user isn't nagged twice in
the same session.

Stored as JSON next to the rest of the persisted state so we don't grow
yet another config file. Failures (read or write) degrade silently —
worst case the snooze is forgotten and the banner shows again, which is
strictly better than crashing the update flow.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SNOOZE_SECONDS = 24 * 60 * 60   # 24h


@dataclass
class SnoozeState:
    tag: str = ""
    until_ts: float = 0.0  # unix epoch seconds

    def is_active_for(self, tag: str, *, now: float | None = None) -> bool:
        """True iff ``tag`` is currently snoozed.

        A different tag (i.e. a newer release than the one snoozed) is
        always non-snoozed — we never suppress a strictly-newer version
        just because the user dismissed a previous one.
        """
        if not tag or tag != self.tag:
            return False
        n = now if now is not None else time.time()
        return n < self.until_ts


def _state_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ChampAssistant"
    return Path.home() / ".champ-assistant"


def state_path() -> Path:
    return _state_dir() / "update_snooze.json"


def load() -> SnoozeState:
    """Return the persisted snooze, or an empty state on any failure."""
    path = state_path()
    if not path.is_file():
        return SnoozeState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.info("update_snooze unreadable: %s — ignoring", exc)
        return SnoozeState()
    if not isinstance(data, dict):
        return SnoozeState()
    tag = data.get("tag", "") if isinstance(data.get("tag"), str) else ""
    until = data.get("until_ts", 0.0)
    if not isinstance(until, (int, float)):
        until = 0.0
    return SnoozeState(tag=tag, until_ts=float(until))


def snooze_tag(tag: str, *, duration_s: float = DEFAULT_SNOOZE_SECONDS) -> None:
    """Persist a snooze for ``tag`` lasting ``duration_s`` seconds."""
    if not isinstance(tag, str) or not tag:
        return
    path = state_path()
    state = SnoozeState(tag=tag, until_ts=time.time() + max(60.0, duration_s))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"tag": state.tag, "until_ts": state.until_ts}, indent=2),
            encoding="utf-8",
        )
        logger.info(
            "update_snooze: %s snoozed until %.0f", state.tag, state.until_ts,
        )
    except OSError as exc:
        logger.warning("update_snooze save failed: %s", exc)


def clear() -> None:
    """Drop any active snooze (used when the user clicks Install now —
    so retrying after a failed install doesn't get blocked by their own
    earlier 'Later')."""
    path = state_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.info("update_snooze clear failed: %s", exc)
