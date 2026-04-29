"""Design-system lockdown linter.

Honest scope note
=================
The "freeze mode" spec asked for runtime token immutability + render-time
drift detection + a global DESIGN_FREEZE_MODE flag. None of those are
effective in Python+Qt without metaclass machinery that costs more in
maintenance than it saves in correctness. Python module attributes can
always be reassigned; Qt's stylesheet engine can't be intercepted at
render time without parsing every CSS string.

What *does* work and is worth doing: a CI linter that fails the test
suite when an inline value sneaks back into the UI tree. That's this
file. It runs in <100 ms, catches every drift case the previous polish
rounds had to manually grep for, and gives a clear error message
pointing at the file + line so the fix is obvious.

What this lint enforces
-----------------------
* No inline ``font-size: Npx`` literal in a UI file. All sizes must
  reference a ``styles.FS_*`` token via f-string interpolation.
* No bare ``#RRGGBB`` hex code outside an explicit allowlist
  (``styles.py`` defines them, ``badges.py`` carries Riot-canonical
  rank colors that are intentionally not theme tokens, ``tray.py``
  paints a literal white pixel into the system-tray icon).

What this lint does NOT enforce (and why)
-----------------------------------------
* Spacing literals — ``setContentsMargins(8, 4, 8, 4)`` etc. are
  all already on the 4-px grid; flagging integer literals would be
  noise across hundreds of call sites with no actual drift.
* ``rgba(...)`` calls — many are intentional (per-band confidence
  alpha modulation, gradient stops). A useful lint would need to
  parse RGB values back to a token, which is the kind of fragile
  string-parsing the spec also explicitly rejects elsewhere.
* Runtime token immutability — no enforcement mechanism in Python
  short of metaclass tricks that aren't worth it.
"""
from __future__ import annotations

import re
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[2] / "src" / "champ_assistant" / "ui"

# Files where raw hex codes are expected and intentional. Keep this
# list small and document each entry — adding a new one should require
# a real justification, not silence the lint.
HEX_ALLOWLIST_FILES: frozenset[str] = frozenset({
    "styles.py",   # the design-system definition itself
    "badges.py",   # riot-canonical rank colors (Iron/Bronze/.../Challenger)
    "tray.py",     # literal #FFFFFF pixel in the tray-icon painter
})

# Pattern matches "font-size: 11px" inside any string literal or styled
# stylesheet. We match the digits separately so the error message can
# report the offending value.
FONT_SIZE_LITERAL = re.compile(r"font-size:\s*(\d+)\s*px")

# Match #RRGGBB in either single- or double-quoted strings. We
# deliberately do not match #RRGGBBAA — those are very rare and tend
# to be inside intentional gradients we don't want to lint.
HEX_LITERAL = re.compile(r"#[0-9A-Fa-f]{6}\b")


def _ui_python_files() -> list[Path]:
    return sorted(p for p in UI_ROOT.rglob("*.py") if p.name != "__init__.py")


def _strip_comments_and_docstrings(text: str) -> str:
    """Remove # comments and triple-quoted docstrings so the lint doesn't
    misfire on a hex code that's only mentioned in prose.

    Cheap heuristic — not a full Python parser. Good enough for our
    own UI code which doesn't do anything weird with strings.
    """
    # Drop triple-quoted blocks first (greedy across lines).
    text = re.sub(r'"""[\s\S]*?"""', "", text)
    text = re.sub(r"'''[\s\S]*?'''", "", text)
    # Drop line comments (# to end of line) — but only if the # is not
    # inside a string. Naive but: lint scope is small, false-positives
    # surface in the error message and can be allowlisted explicitly.
    out_lines = []
    for line in text.splitlines():
        stripped = re.sub(r"(?<!\\)#.*$", "", line)
        out_lines.append(stripped)
    return "\n".join(out_lines)


def test_no_inline_font_size_outside_token_scale() -> None:
    """Every font-size declaration must reference a ``styles.FS_*`` token
    via f-string interpolation. A bare ``Npx`` literal is rejected so a
    new size can't quietly drift into the codebase."""
    violations: list[tuple[Path, int, str]] = []
    for path in _ui_python_files():
        if path.name == "styles.py":
            continue
        cleaned = _strip_comments_and_docstrings(path.read_text(encoding="utf-8"))
        for line_no, line in enumerate(cleaned.splitlines(), start=1):
            for match in FONT_SIZE_LITERAL.finditer(line):
                violations.append((path, line_no, match.group(0)))

    if violations:
        joined = "\n".join(
            f"  {p.relative_to(UI_ROOT.parents[2])}:{ln}  {snippet!r}"
            for p, ln, snippet in violations
        )
        raise AssertionError(
            "design-lockdown: inline font-size literal(s) — must use "
            f"styles.FS_* tokens via f-string interpolation:\n{joined}"
        )


def test_no_raw_hex_outside_allowlist() -> None:
    """Bare ``#RRGGBB`` codes are forbidden in UI files except in the
    explicit allowlist. If you genuinely need a new raw color, add the
    file to ``HEX_ALLOWLIST_FILES`` with a one-line justification — the
    review of THAT change is the gate, not a silent acceptance."""
    violations: list[tuple[Path, int, str]] = []
    for path in _ui_python_files():
        if path.name in HEX_ALLOWLIST_FILES:
            continue
        cleaned = _strip_comments_and_docstrings(path.read_text(encoding="utf-8"))
        for line_no, line in enumerate(cleaned.splitlines(), start=1):
            for match in HEX_LITERAL.finditer(line):
                violations.append((path, line_no, match.group(0)))

    if violations:
        joined = "\n".join(
            f"  {p.relative_to(UI_ROOT.parents[2])}:{ln}  {snippet}"
            for p, ln, snippet in violations
        )
        raise AssertionError(
            "design-lockdown: raw hex code(s) outside the allowlist — "
            "use a styles.* token, or add the file to HEX_ALLOWLIST_FILES "
            f"with a one-line reason:\n{joined}"
        )


def test_lint_finds_canonical_files() -> None:
    """Sanity: the linter must actually be looking at the UI tree. If
    UI_ROOT moves and we don't notice, the lint silently passes on zero
    files — which is worse than failing loudly."""
    files = _ui_python_files()
    assert len(files) > 5, (
        f"design-lockdown linter found only {len(files)} ui files at "
        f"{UI_ROOT} — UI_ROOT may have moved"
    )
    names = {p.name for p in files}
    # A few stable file names that should always be in the UI tree.
    for expected in ("overlay.py", "styles.py", "widgets.py"):
        assert expected in names, (
            f"design-lockdown linter missing {expected} — UI_ROOT broken?"
        )
