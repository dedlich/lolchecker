"""Logging configuration with stable subsystem tags.

Production logs need to be greppable: the user (or me) opens
``app.log`` six months from now and wants to instantly see "what was
the hotkey service doing when this crashed". Module-level logger names
like ``champ_assistant.hotkey_service`` carry the same information but
are wordy and inconsistent — so this module attaches a short bracketed
``[HOTKEY]``-style tag to every record.

Tag policy:
  * Tags are based on the *module* portion of the logger name
    (i.e. the segment after ``champ_assistant.``).
  * Anything not in the explicit map gets ``[APP]`` so the format string
    stays uniform — no missing-field crashes from log records emitted
    by code we forgot to map.
  * Tags are intentionally short (≤8 chars) so the message column lines
    up regardless of which subsystem fired the line.
"""
from __future__ import annotations

import logging

# Map ``champ_assistant.<module>`` → bracketed tag. Submodules under a
# subsystem inherit their parent's tag (e.g. ``ui.overlay`` → [UI]).
_SUBSYSTEM_TAGS: dict[str, str] = {
    "hotkey_service":  "HOTKEY",
    "hotkey_config":   "HOTKEY",
    "state_store":     "STATE",
    "render_scheduler": "RENDER",
    "update_check":    "UPDATE",
    "layout":          "LAYOUT",
    "window_flags":    "WINDOW",
    "lifecycle":       "LIFECYC",
    "safety":          "CRASH",
    "diagnostics":     "DIAG",
    "app":             "APP",
    "tasks":           "APP",
    "config":          "APP",
    "overlay_config":  "APP",
    "secrets":         "APP",
    "__main__":        "APP",
}

_PARENT_PREFIXES: dict[str, str] = {
    "ui":      "UI",
    "lcu":     "LCU",
    "data":    "DATA",
    "advisor": "ADVIS",
}

DEFAULT_TAG = "APP"


def _tag_for(name: str) -> str:
    """Resolve a logger name to its bracketed subsystem tag."""
    # Running via ``python -m champ_assistant`` makes the entry module's
    # logger name literally "__main__" (no package prefix). Treat as APP
    # so production logs don't carry a misleading [EXT] tag.
    if name == "__main__":
        return DEFAULT_TAG
    if not name.startswith("champ_assistant"):
        # Third-party logger that survived the WARNING-floor list (e.g. an
        # unfamiliar httpx submodule). Tag uniformly so format stays clean.
        return "EXT"
    parts = name.split(".")
    if len(parts) < 2:
        return DEFAULT_TAG
    leaf = parts[1]
    if leaf in _SUBSYSTEM_TAGS:
        return _SUBSYSTEM_TAGS[leaf]
    if leaf in _PARENT_PREFIXES:
        return _PARENT_PREFIXES[leaf]
    return DEFAULT_TAG


class SubsystemTagFilter(logging.Filter):
    """Stamps ``record.subsystem`` so the formatter can render ``[TAG]``.

    Filter (not Formatter) because the same tag needs to be available to
    every handler (file + stderr) without each one independently looking
    up the mapping.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.subsystem = _tag_for(record.name)
        return True


# Standard format used across every handler — single source of truth so
# file logs and stderr stay aligned.
TAGGED_FORMAT = "%(asctime)s [%(subsystem)-7s] %(levelname)-8s %(name)s: %(message)s"
TAGGED_DATEFMT = "%Y-%m-%d %H:%M:%S"


def make_formatter() -> logging.Formatter:
    return logging.Formatter(TAGGED_FORMAT, datefmt=TAGGED_DATEFMT)


def install_tag_filter(handler: logging.Handler) -> None:
    """Attach the SubsystemTagFilter to ``handler``.

    Filters live on handlers (not the root logger) because a Logger's
    own filters are not consulted for records that *propagate up* from a
    child — they only run when the record was logged directly through
    that Logger. Stamping at the handler ensures every record that
    actually gets emitted carries a tag, no matter which child logger
    originated it.

    Idempotent — running the same handler through multiple init paths
    won't pile up duplicate filters.
    """
    for f in handler.filters:
        if isinstance(f, SubsystemTagFilter):
            return
    handler.addFilter(SubsystemTagFilter())
