"""Static safety guard — assert the codebase has NO low-level
keyboard / input hooks.

Why this lint exists
====================
Multiple feature specs in this codebase have asked for "TAB key
detection" or similar global-input behavior. The honest answer
every time has been: a global keyboard hook is a Vanguard concern.
Riot's anti-cheat treats SetWindowsHookEx WH_KEYBOARD_LL and
similar input-monitoring patterns as suspicious surface even when
used innocently.

This lint enforces that decision by failing CI if anyone (future
me, a contributor, or a careless paste from Stack Overflow) ever
introduces one of the forbidden patterns. Either:

  * Use the existing HotkeyService (Win32 RegisterHotKey, the
    sanctioned API for global hotkeys), or
  * Use Qt's per-window key-event handling (works inside our own
    overlay's focus context).

Adding a forbidden pattern requires removing this test — making
the decision explicit + reviewable, not silent.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
TEST_ROOT = ROOT / "tests"

# Patterns that indicate a low-level / global keyboard hook has been
# introduced. Match as substrings — these are unambiguous (no false
# positives in idiomatic Python).
FORBIDDEN_PATTERNS = (
    "SetWindowsHookEx",
    "WH_KEYBOARD_LL",
    "WH_MOUSE_LL",
    "WH_KEYBOARD",   # also catches plain WH_KEYBOARD constant
    "GetAsyncKeyState",  # input-state polling
    "keyboard.hook",     # `keyboard` package
    "pynput.keyboard.Listener",
    "pynput.mouse.Listener",
    "pyhook",
)


def _python_files() -> list[Path]:
    """All .py files under src/, EXCLUDING this test file (so the
    pattern strings here don't match themselves)."""
    out: list[Path] = []
    for path in SRC_ROOT.rglob("*.py"):
        out.append(path)
    return sorted(out)


def test_no_keyboard_hook_imports_or_calls() -> None:
    """Walk the production source tree; fail on any forbidden pattern.

    The list is small + explicit; allowlisting is via removing the
    pattern from FORBIDDEN_PATTERNS, which is itself a code review
    moment ("are we sure we want a keyboard hook now?").
    """
    violations: list[tuple[Path, int, str]] = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        # Strip line-comments so prose can mention the patterns
        # without tripping the lint.
        cleaned = re.sub(r"(?<!\\)#.*$", "", text, flags=re.MULTILINE)
        # Strip triple-quoted docstrings + module docstrings — same
        # logic as the design-lockdown linter.
        cleaned = re.sub(r'"""[\s\S]*?"""', "", cleaned)
        cleaned = re.sub(r"'''[\s\S]*?'''", "", cleaned)
        for line_no, line in enumerate(cleaned.splitlines(), start=1):
            for pattern in FORBIDDEN_PATTERNS:
                if pattern in line:
                    violations.append((path, line_no, pattern))

    if violations:
        joined = "\n".join(
            f"  {p.relative_to(ROOT)}:{ln}  → {pattern}"
            for p, ln, pattern in violations
        )
        raise AssertionError(
            "anti-cheat-safety: low-level keyboard/input hook pattern "
            f"detected in production code:\n{joined}\n\n"
            "Riot's Vanguard treats SetWindowsHookEx and equivalent input "
            "monitoring as suspicious. Use HotkeyService (RegisterHotKey) "
            "for global hotkeys, or Qt's per-window key events.\n\n"
            "If you genuinely need a forbidden pattern, remove it from "
            "FORBIDDEN_PATTERNS in this test — that change should "
            "trigger code review."
        )


def test_lint_finds_canonical_files() -> None:
    """Sanity: the linter is actually scanning something. If src/
    moves and the path resolution breaks, the lint silently passes
    on zero files — which is worse than failing loudly."""
    files = _python_files()
    assert len(files) > 30, (
        f"anti-cheat lint found only {len(files)} files at {SRC_ROOT} — "
        "src/ may have moved"
    )
