"""RULES.md ⇄ code sync linter.

The decision engine has 57 callable rules across `ALL_RULES`, the three
spell-tracker rules, and `rule_situational_build`. ``RULES.md`` documents
each one. Without an automated check, the catalog will silently fall out
of date the next time a rule is added — at which point a stale doc is
worse than no doc.

This linter enforces the bidirectional invariant:

* Every callable rule_* in code is documented in RULES.md.
* Every rule_* documented in RULES.md exists in code.

When the test fails the message points at exactly which rules are missing
or stale, so the fix is: open RULES.md, copy the closest section, paste,
edit. ~30 seconds per rule.
"""
from __future__ import annotations

import re
from pathlib import Path

from champ_assistant.advisor.decision_engine import ALL_RULES, rule_situational_build
from champ_assistant.advisor.decision_engine._rules import (
    rule_enemy_combat_spell_down,
    rule_enemy_flash_down,
    rule_enemy_tp_down,
)

# Project root — this test lives in tests/lint/, so repo root is two up.
REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_MD = REPO_ROOT / "RULES.md"

# Match the heading style used in RULES.md: `### `rule_xxx``
RULE_HEADING_RE = re.compile(r"^###\s+`(rule_[a-z_]+)`", re.MULTILINE)


def _all_callable_rules() -> set[str]:
    """Every rule the engine will ever call: ALL_RULES + the 3 spell-tracker
    rules + rule_situational_build. Returns the function ``__name__`` set."""
    return (
        {fn.__name__ for fn in ALL_RULES}
        | {
            rule_enemy_flash_down.__name__,
            rule_enemy_tp_down.__name__,
            rule_enemy_combat_spell_down.__name__,
        }
        | {rule_situational_build.__name__}
    )


def _documented_rules() -> set[str]:
    r"""Rules documented in RULES.md, identified by their `### \`rule_xxx\``
    headings. Catches both fully-detailed entries and brief ones."""
    text = RULES_MD.read_text(encoding="utf-8")
    return set(RULE_HEADING_RE.findall(text))


def test_every_callable_rule_is_documented() -> None:
    """The catalog must mention every rule the engine actually runs.
    Adding a rule without a RULES.md entry fails this test."""
    callable_rules = _all_callable_rules()
    documented = _documented_rules()
    missing = callable_rules - documented
    assert not missing, (
        f"RULES.md is missing entries for {len(missing)} rule(s): "
        f"{sorted(missing)}. Open RULES.md, find the right category, "
        f"add a `### \\`rule_xxx\\`` heading + one-line description."
    )


def test_every_documented_rule_exists_in_code() -> None:
    """Catches stale entries — rules that were renamed or removed but still
    appear in the doc. A stale catalog is worse than no catalog."""
    callable_rules = _all_callable_rules()
    documented = _documented_rules()
    stale = documented - callable_rules
    assert not stale, (
        f"RULES.md documents {len(stale)} rule(s) that no longer exist "
        f"in code: {sorted(stale)}. Either restore the rule or remove "
        f"the heading from RULES.md."
    )


def test_rules_md_exists_and_has_content() -> None:
    """Sanity check — RULES.md should always be present + non-trivial.
    Catches accidental deletion or truncation."""
    assert RULES_MD.exists(), f"RULES.md not found at {RULES_MD}"
    text = RULES_MD.read_text(encoding="utf-8")
    assert len(text) > 5000, (
        f"RULES.md is suspiciously short ({len(text)} chars). "
        "Has it been truncated?"
    )
    # The catalog should mention these umbrella signals — they're the
    # backbone of _suppress_dominated and changing one without updating
    # the doc would silently mislead readers.
    for umbrella in ("ace", "numbers_disadv", "ally_inhib_down",
                     "enemy_elder_buff", "far_behind_safe", "tilt"):
        assert f"`{umbrella}`" in text, (
            f"RULES.md doesn't mention umbrella suppression signal "
            f"`{umbrella}` — the suppression matrix may be incomplete."
        )
