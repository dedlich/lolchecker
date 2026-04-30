"""Persistent hotkey configuration + combo string parser.

Storage: ``%LOCALAPPDATA%/ChampAssistant/config.json`` on Windows,
``~/.champ-assistant/config.json`` everywhere else. The schema is:

  {
    "hotkeys": {
      "toggle_overlay":  "Ctrl+Alt+H",
      "toggle_lock":     "Ctrl+Alt+L",
      "reset_positions": "Ctrl+Alt+R"
    }
  }

Combo strings are parsed into ``(modifiers, vk)`` pairs that
:class:`HotkeyService` hands to ``RegisterHotKey``. Unknown tokens or
modifier-only strings produce a ``None`` from :func:`parse_combo` so
callers can reject the input cleanly without exception handling.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Win32 modifier + virtual-key constants (subset). Duplicated here so
# config code doesn't depend on hotkey_service which carries the Qt
# QObject baggage. Keep in sync — they're physical-keyboard constants
# and won't change.
# --------------------------------------------------------------------------
MOD_ALT      = 0x0001
MOD_CONTROL  = 0x0002
MOD_SHIFT    = 0x0004
MOD_WIN      = 0x0008

# Letter VK codes line up with ASCII ('A' == 0x41 == VK_A).
# Digit VK codes line up with ASCII ('0' == 0x30 == VK_0).
# F-keys: VK_F1=0x70, VK_F2=0x71, ..., VK_F24=0x87.
_LETTER_RANGE = (0x41, 0x5A)   # A-Z
_DIGIT_RANGE  = (0x30, 0x39)   # 0-9
_FKEY_RANGE   = (0x70, 0x87)   # F1-F24

_TOKEN_TO_MOD: dict[str, int] = {
    "ctrl":    MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt":     MOD_ALT,
    "shift":   MOD_SHIFT,
    "win":     MOD_WIN,
    "meta":    MOD_WIN,
    "cmd":     MOD_WIN,
}


# --------------------------------------------------------------------------
# Combo string <-> (modifiers, vk)
# --------------------------------------------------------------------------
def parse_combo(combo: str) -> tuple[int, int] | None:
    """Parse a ``"Ctrl+Alt+H"``-style string into ``(mods, vk)``.

    Returns ``None`` for:
      * empty input
      * unknown tokens
      * modifier-only combos (no key body)
    """
    if not isinstance(combo, str):
        return None
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    if not parts:
        return None
    mods = 0
    vk = 0
    for token in parts:
        lower = token.lower()
        if lower in _TOKEN_TO_MOD:
            mods |= _TOKEN_TO_MOD[lower]
            continue
        upper = token.upper()
        if len(upper) == 1 and _LETTER_RANGE[0] <= ord(upper) <= _LETTER_RANGE[1]:
            vk = ord(upper)
        elif len(upper) == 1 and _DIGIT_RANGE[0] <= ord(upper) <= _DIGIT_RANGE[1]:
            vk = ord(upper)
        elif upper.startswith("F") and upper[1:].isdigit():
            n = int(upper[1:])
            if 1 <= n <= 24:
                vk = 0x6F + n
            else:
                return None
        else:
            return None
    if vk == 0:
        return None  # modifier-only
    return mods, vk


def format_combo(mods: int, vk: int) -> str:
    """Render ``(mods, vk)`` back into the canonical "Ctrl+Alt+H" shape."""
    parts: list[str] = []
    if mods & MOD_CONTROL: parts.append("Ctrl")
    if mods & MOD_ALT:     parts.append("Alt")
    if mods & MOD_SHIFT:   parts.append("Shift")
    if mods & MOD_WIN:     parts.append("Win")
    if _LETTER_RANGE[0] <= vk <= _LETTER_RANGE[1]:
        parts.append(chr(vk))
    elif _DIGIT_RANGE[0] <= vk <= _DIGIT_RANGE[1]:
        parts.append(chr(vk))
    elif _FKEY_RANGE[0] <= vk <= _FKEY_RANGE[1]:
        parts.append(f"F{vk - 0x6F}")
    else:
        parts.append(f"VK_{vk:02X}")
    return "+".join(parts)


def is_valid_combo(combo: str) -> bool:
    return parse_combo(combo) is not None


# --------------------------------------------------------------------------
# JSON config file
# --------------------------------------------------------------------------
DEFAULT_HOTKEYS: dict[str, str] = {
    "toggle_overlay":    "Ctrl+Alt+H",
    "toggle_lock":       "Ctrl+Alt+L",
    "reset_positions":   "Ctrl+Alt+R",
    "reset_layout":      "Ctrl+Alt+D",
    # Manual scoreboard toggle — alternative to the vision-based
    # auto-detection. Always available (does NOT require Win32
    # keyboard hooks; uses the safe RegisterHotKey API).
    "toggle_scoreboard": "Ctrl+Alt+B",
    # Toggle the InsightPanel detail-view for the current top
    # recommendation (v2 spec).
    "toggle_insight":    "Ctrl+Alt+I",
}


@dataclass
class HotkeyConfig:
    """In-memory mirror of the on-disk hotkey settings."""
    hotkeys: dict[str, str]

    @classmethod
    def defaults(cls) -> "HotkeyConfig":
        return cls(hotkeys=dict(DEFAULT_HOTKEYS))


def _config_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ChampAssistant"
    return Path.home() / ".champ-assistant"


def config_path() -> Path:
    return _config_dir() / "config.json"


def load() -> HotkeyConfig:
    """Read the hotkey config from disk. Falls back to defaults on any
    error (missing file, corrupt JSON, unknown action keys, invalid
    combo strings) — never raises."""
    path = config_path()
    if not path.is_file():
        return HotkeyConfig.defaults()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("hotkey_config corrupt or unreadable: %s — using defaults", exc)
        return HotkeyConfig.defaults()

    config = HotkeyConfig.defaults()
    if not isinstance(raw, dict):
        return config
    hotkeys = raw.get("hotkeys")
    if not isinstance(hotkeys, dict):
        return config

    for action in DEFAULT_HOTKEYS:
        candidate = hotkeys.get(action)
        if isinstance(candidate, str) and is_valid_combo(candidate):
            config.hotkeys[action] = candidate
        elif candidate is not None:
            logger.warning(
                "hotkey_config invalid combo for %s: %r — keeping default %r",
                action, candidate, config.hotkeys[action],
            )
    return config


def save(config: HotkeyConfig) -> None:
    """Persist the config. Failures are logged, never raised."""
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Validate before writing — never persist a broken combo.
        clean = {
            action: combo
            for action, combo in config.hotkeys.items()
            if action in DEFAULT_HOTKEYS and is_valid_combo(combo)
        }
        path.write_text(
            json.dumps({"hotkeys": clean}, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("hotkey_config save failed: %s", exc)
