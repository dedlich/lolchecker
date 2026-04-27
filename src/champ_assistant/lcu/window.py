"""Locate the League of Legends game window via Win32 (ctypes only).

We need this to **auto-pin our overlay** next to League's actual window
position rather than blindly anchoring to a screen edge: League can be on
the secondary monitor, in a non-fullscreen size, or moved by the user.

Returns ``None`` on non-Windows platforms (the function is a noop on
macOS/Linux — the caller falls back to the static screen-edge anchor).

We deliberately avoid pywin32 — pure ctypes keeps the PyInstaller bundle
smaller and removes a heavy native dependency.
"""
from __future__ import annotations

import ctypes
import logging
import sys
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Riot's class name for the in-game window. Stable across patches; the
# launcher uses a different class ("RiotWindow") which we ignore.
LEAGUE_WINDOW_CLASS_NAMES = ("RiotWindowClass",)
LEAGUE_WINDOW_TITLES = ("League of Legends (TM) Client", "League of Legends")

WS_POPUP = 0x80000000
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_BORDER = 0x00800000
WS_DLGFRAME = 0x00400000
WS_CAPTION = WS_BORDER | WS_DLGFRAME

GWL_STYLE = -16


@dataclass(frozen=True)
class LeagueWindowInfo:
    hwnd: int
    title: str
    class_name: str
    left: int
    top: int
    right: int
    bottom: int
    fullscreen_exclusive: bool

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


def _on_windows() -> bool:
    return sys.platform.startswith("win")


def find_league_window() -> LeagueWindowInfo | None:
    """Walk the top-level windows looking for League. ``None`` if not found
    or we're not on Windows. Never raises — Win32 errors degrade silently."""
    if not _on_windows():
        return None
    try:
        return _scan_windows()
    except OSError as exc:
        logger.info("league_window_scan_failed: %s", exc)
        return None


def _scan_windows() -> LeagueWindowInfo | None:
    user32 = ctypes.windll.user32
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
    )

    found: list[LeagueWindowInfo] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_text(user32, hwnd)
        cls = _class_name(user32, hwnd)
        if cls not in LEAGUE_WINDOW_CLASS_NAMES:
            return True
        # Title can be localized but always contains "League".
        if not any(kw.lower() in title.lower() for kw in ("league of legends",)):
            # Some early-load states have empty title; accept anyway since
            # the class name match is strong.
            if title and "league" not in title.lower():
                return True
        rect = _window_rect(user32, hwnd)
        if rect is None:
            return True
        left, top, right, bottom = rect
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        # Heuristic for fullscreen exclusive: WS_POPUP set + no caption,
        # exactly the size of the monitor. Borderless usually has the
        # same size but may keep WS_OVERLAPPEDWINDOW bits.
        is_popup = bool(style & WS_POPUP)
        has_caption = bool(style & WS_CAPTION)
        fse_likely = is_popup and not has_caption
        found.append(
            LeagueWindowInfo(
                hwnd=int(hwnd), title=title, class_name=cls,
                left=left, top=top, right=right, bottom=bottom,
                fullscreen_exclusive=fse_likely,
            )
        )
        return False  # stop enumerating once we've found one

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return found[0] if found else None


def _window_text(user32, hwnd: int) -> str:  # type: ignore[no-untyped-def]
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value or ""


def _class_name(user32, hwnd: int) -> str:  # type: ignore[no-untyped-def]
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value or ""


def _window_rect(user32, hwnd: int):  # type: ignore[no-untyped-def]
    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom
