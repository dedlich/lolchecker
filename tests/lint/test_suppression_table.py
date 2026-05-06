"""SUPPRESSION_TABLE invariant linter.

The declarative suppression table in
``advisor.decision_engine._suppression`` replaced an imperative wall of
``if "x" in kinds:`` blocks. This linter enforces invariants the table
shape was designed to make checkable:

1. **No empty entries** — a SuppressionRule must have at least one
   trigger and at least one kind in ``suppresses`` (otherwise the entry
   is dead code).
2. **No circular suppression** — a kind that triggers a rule must not
   appear in that same rule's ``suppresses`` set (would suppress itself).
3. **Documented intent** — every entry has a non-empty ``description``.
4. **Severity gate validity** — when ``requires_severity`` is set, it
   matches one of the canonical severities (``info`` / ``warn`` /
   ``alert``).
5. **Terminal flag is rare** — at most one terminal rule (we only have
   ``numbers_disadv``); more than one would mean conflicting short-
   circuits and the order-of-application would matter without being
   documented.
6. **No orphaned strings** — every string that appears as a trigger or
   in a ``suppresses`` set should match a rule actually emitted by the
   engine. This catches typos like ``"recall_critic"`` (should be
   ``recall_critical``) before they silently fail to suppress anything.
"""
from __future__ import annotations

from champ_assistant.advisor.decision_engine._suppression import (
    SUPPRESSION_TABLE,
    SuppressionRule,
)


_VALID_SEVERITIES = {"info", "warn", "alert"}


def test_no_empty_entries() -> None:
    for rule in SUPPRESSION_TABLE:
        assert rule.triggers, f"empty triggers in: {rule.description}"
        assert rule.suppresses, f"empty suppresses in: {rule.description}"


def test_no_circular_suppression() -> None:
    """A kind that triggers a rule must not also be in that rule's
    ``suppresses`` set — that would mean the rule cancels its own
    trigger, leaving the suppression behaviour ambiguous."""
    for rule in SUPPRESSION_TABLE:
        overlap = rule.triggers & rule.suppresses
        assert not overlap, (
            f"circular suppression in '{rule.description}': "
            f"{sorted(overlap)} both trigger and are suppressed by this rule"
        )


def test_every_entry_has_description() -> None:
    for rule in SUPPRESSION_TABLE:
        assert rule.description.strip(), (
            f"empty description on rule with triggers={rule.triggers}, "
            f"suppresses={rule.suppresses}"
        )


def test_severity_gates_are_valid() -> None:
    for rule in SUPPRESSION_TABLE:
        if rule.requires_severity is None:
            continue
        assert rule.requires_severity in _VALID_SEVERITIES, (
            f"invalid requires_severity={rule.requires_severity!r} on rule "
            f"with triggers={rule.triggers}; must be one of {_VALID_SEVERITIES}"
        )


def test_at_most_one_terminal_rule() -> None:
    """Multiple terminal rules would mean the suppression pass behaves
    differently depending on which fires first — order would become
    load-bearing without being documented. The current intent is one
    terminal rule (``numbers_disadv``)."""
    terminal_rules = [r for r in SUPPRESSION_TABLE if r.terminal]
    assert len(terminal_rules) <= 1, (
        f"expected at most 1 terminal rule, found {len(terminal_rules)}: "
        f"{[r.description for r in terminal_rules]}"
    )


def test_every_kind_in_table_is_emitted_by_some_rule() -> None:
    """Catches typos: a string in ``triggers`` or ``suppresses`` that
    doesn't match any rule kind actually emitted by the engine.

    The trigger / suppresses fields use string literals because the
    engine emits ``Recommendation(kind="...")``. A typo like
    ``"recall_critic"`` (missing the trailing ``-al``) would silently
    never fire / never suppress anything — exactly the class of bug
    declarative tables are supposed to make impossible.

    The set of "real" rule kinds is captured by parsing every
    ``kind="..."`` literal out of ``_rules.py`` (the only place rule
    output is constructed) plus the few state-named kinds emitted from
    ``_state.py`` for hysteresis-bearing rules. False positives here
    are easier to investigate than silent typo-bugs."""
    import re
    from pathlib import Path

    engine_root = (
        Path(__file__).resolve().parents[2]
        / "src" / "champ_assistant" / "advisor" / "decision_engine"
    )
    # Scan _rules.py + every domain module under rules/ (the §3.2 split
    # moved rule bodies out of _rules.py into rules/<domain>.py — the
    # lint must follow them).
    sources = [engine_root / "_rules.py", *(engine_root / "rules").glob("*.py")]
    src = "\n".join(p.read_text(encoding="utf-8") for p in sources if p.is_file())
    # Match `kind="some_str"` and `kind='some_str'` literally.
    emitted = set(re.findall(r"kind\s*=\s*['\"]([a-z_][a-z0-9_]*)['\"]", src))

    # A few kinds are emitted via positional tuple returns from helpers
    # (e.g. ``_objective_taken_advice`` returns
    # ``(text, severity, risk, ttl_s, confidence, kind)`` and the consumer
    # unpacks then constructs the ``Recommendation`` with the kind passed
    # positionally / through a variable). The keyword-arg regex above can't
    # see those; whitelist them explicitly here. Each entry below is a
    # genuine engine-emitted kind — verified by grepping the helper.
    indirect_kinds: set[str] = {
        "objective_taken_baron",
        "objective_taken_elder",
        "objective_taken_soul",
        "objective_taken_drake",
        "objective_taken_herald",
    }
    emitted |= indirect_kinds

    # Aggregate every string the suppression table references.
    referenced: set[str] = set()
    for rule in SUPPRESSION_TABLE:
        referenced |= rule.triggers
        referenced |= rule.suppresses

    orphans = referenced - emitted
    assert not orphans, (
        f"SUPPRESSION_TABLE references {len(orphans)} kind(s) that no rule "
        f"actually emits — likely typos or removed rules:\n  "
        + "\n  ".join(sorted(orphans))
        + "\n\nIf the kind is genuinely produced (e.g. via a string format), "
          "add it to the indirect_kinds whitelist in this test."
    )


def test_suppression_table_is_well_typed() -> None:
    """Every entry is a SuppressionRule (catches accidental tuple
    insertions or other shape mistakes). frozenset usage is enforced —
    plain set would still work but loses immutability guarantees."""
    for rule in SUPPRESSION_TABLE:
        assert isinstance(rule, SuppressionRule), (
            f"expected SuppressionRule, got {type(rule).__name__}"
        )
        assert isinstance(rule.triggers, frozenset)
        assert isinstance(rule.suppresses, frozenset)
