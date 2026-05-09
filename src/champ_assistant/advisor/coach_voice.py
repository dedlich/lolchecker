"""Editorial helpers for the recommendation engine — pro-coaching voice.

In-game readability is the constraint. The player has 2-3 seconds to
parse a recommendation between mechanical actions, so every rec the
engine surfaces must:

  * Be under 60 characters (one glance, one line in the panel).
  * Lead with an imperative verb or all-caps action ("Recall", "Drache",
    "JETZT") — never a status report ("you might want to consider...").
  * Carry the consequence in parentheses when the WHY isn't obvious
    from the action alone.

Voice: German. Matches the existing in-game text register
("Recall JETZT", "vs 3 AP-Gegner", "ACE!"). Switching mid-pipeline
would break the reading flow on screen.

Usage:

    from champ_assistant.advisor import coach_voice

    text = coach_voice.directive(
        action="Recall",
        consequence="nächster Trade tötet dich",
        urgency="now",
    )
    # → "Recall JETZT (nächster Trade tötet dich)"

    text = coach_voice.directive(
        action="Mortal Reminder",
        consequence="vs Aatrox + Vlad — heilen sonst durch",
    )
    # → "Mortal Reminder (vs Aatrox + Vlad — heilen sonst durch)"

The lint test ``tests/lint/test_coach_voice_lint.py`` walks every
``Recommendation`` constructor in the rules package and validates the
output against ``MAX_LENGTH`` + the imperative-marker regex.
"""
from __future__ import annotations

import re

# Hard limit — recommendation panel renders one line per rec. Above
# this the text wraps or truncates and the user can't glance-parse.
MAX_LENGTH = 60

# Markers that signal imperative / decisive voice. A rec MUST contain
# at least one — either the literal "JETZT" / "NUN" / "SOFORT" /
# "NIEMALS" tokens, or a leading German imperative verb (ends in
# nothing or "e", first capital), or all-caps verb / noun phrase.
_IMPERATIVE_MARKERS = (
    "JETZT", "NUN", "SOFORT", "NIEMALS", "KEIN", "KEINE",
    "NIE",
    "ACE!",
)
_LEADING_IMPERATIVE_VERBS = frozenset({
    # Common action verbs in coaching context. Matches first word
    # case-insensitively.
    "recall", "back", "push", "shove", "freeze", "trade", "engage",
    "disengage", "all-in", "allin", "fight", "ward", "drache",
    "drake", "baron", "herald", "buy", "kauf", "skip", "swap",
    "vermeide", "warte", "hol", "hole", "setze", "platziere",
    "pushen", "warten", "vermeiden", "schauen", "kontrollieren",
    "pressure", "zone", "zonen", "splitpush", "split",
    "ace!", "voraus", "grub", "elder", "soul", "dragon",
    "feindlicher", "feind-",
})


def directive(
    action: str,
    *,
    consequence: str | None = None,
    urgency: str = "default",
) -> str:
    """Build a directive recommendation line in the coaching voice.

    ``action`` is the imperative core ("Recall", "Mortal Reminder kaufen").
    ``consequence`` is the optional WHY clause that goes in parentheses.
    ``urgency`` selects an urgency marker:

      * ``"now"`` → " JETZT" suffix on the action ("Recall JETZT")
      * ``"never"`` → "Niemals " prefix ("Niemals 1v2 forward")
      * ``"default"`` → no urgency marker

    The function asserts the result fits ``MAX_LENGTH``. A coaching
    voice that gets clipped on screen is no coaching voice. Long inputs
    raise ``ValueError`` so the calling rule fails loud rather than
    silently emitting unreadable text.
    """
    head = action.strip()
    if not head:
        raise ValueError("coach_voice.directive: action must be non-empty")

    if urgency == "now":
        # Don't double-stamp if the action already carries it.
        if "JETZT" not in head.upper():
            head = f"{head} JETZT"
    elif urgency == "never":
        if not head.lower().startswith("niemals"):
            head = f"Niemals {head}"
    elif urgency != "default":
        raise ValueError(
            f"coach_voice.directive: unknown urgency '{urgency}' "
            "(allowed: now / never / default)"
        )

    text = head if not consequence else f"{head} ({consequence.strip()})"

    if len(text) > MAX_LENGTH:
        # Try shorter form: drop the parenthetical so the headline
        # still fits. Better truncated WHY than truncated WHAT.
        if consequence and len(head) <= MAX_LENGTH:
            text = head
        else:
            raise ValueError(
                f"coach_voice.directive: text exceeds {MAX_LENGTH} chars: {text!r}"
            )

    if not _has_imperative_marker(text):
        raise ValueError(
            "coach_voice.directive: result lacks imperative voice — "
            f"{text!r}. Use a recognized verb / urgency marker."
        )
    return text


def status_line(
    facts: str,
    *,
    action: str,
) -> str:
    """A two-clause "<facts> — <action>" line for situations where the
    coaching call needs the situational facts before the directive.
    The action half is checked for imperative voice; the facts half
    is free-form.

    Examples:
      ``status_line("Wir 4v5", action="keine Fights bis Respawn")``
      → ``"Wir 4v5 — keine Fights bis Respawn"``

    Length still capped at MAX_LENGTH.
    """
    facts = facts.strip()
    action = action.strip()
    if not facts or not action:
        raise ValueError("coach_voice.status_line: both halves must be non-empty")
    text = f"{facts} — {action}"
    if len(text) > MAX_LENGTH:
        raise ValueError(
            f"coach_voice.status_line: text exceeds {MAX_LENGTH} chars: {text!r}"
        )
    # Imperative check looks at the ACTION half only — the facts half
    # is free-form. Otherwise a "Drake spawnt — ich überlege" kind of
    # passive line would slip through because "Drake" is in the verb
    # list (it's a valid imperative when leading the action half).
    if not _has_imperative_marker(action):
        raise ValueError(
            "coach_voice.status_line: action half lacks imperative voice — "
            f"{action!r}"
        )
    return text


def _has_imperative_marker(text: str) -> bool:
    """True if the text carries a recognized imperative marker.

    Used by both ``directive`` / ``status_line`` (validation) and the
    lint test (regression catch). Recognizes:
      * Any all-caps urgency token from ``_IMPERATIVE_MARKERS``.
      * A leading verb from ``_LEADING_IMPERATIVE_VERBS`` (case-insensitive).
      * A leading verb followed by an action target separated by " — "
        (covers ``status_line`` second-half case where the verb is the
        first word AFTER the dash).
    """
    upper = text.upper()
    for token in _IMPERATIVE_MARKERS:
        if token in upper:
            return True
    # First word
    first = re.split(r"[\s—:(]", text.strip(), maxsplit=1)[0].lower().strip(",.;-")
    if first in _LEADING_IMPERATIVE_VERBS:
        return True
    # First word after " — " (status_line right half)
    parts = text.split(" — ", 1)
    if len(parts) == 2:
        after = re.split(r"[\s:]", parts[1].strip(), maxsplit=1)[0].lower().strip(",.;-")
        if after in _LEADING_IMPERATIVE_VERBS:
            return True
    # All-caps action word (e.g. "RECALL")
    first_token = text.strip().split(" ", 1)[0]
    if first_token.isupper() and len(first_token) >= 3:
        return True
    return False


def validate(text: str) -> None:
    """Assert that an already-built recommendation line passes the
    voice contract. Used by the certified-rules registry so existing
    good text (rules that built strings before coach_voice landed) can
    still be pinned without rewriting through ``directive`` / ``status_line``.

    Same checks the builders enforce:
      * Non-empty, ≤ ``MAX_LENGTH`` chars.
      * Carries an imperative marker (urgency token, leading verb,
        or all-caps action).

    Raises ``ValueError`` with a descriptive message on failure so the
    test that calls validate gets a useful regression assertion.
    """
    if not text:
        raise ValueError("coach_voice.validate: text is empty")
    if len(text) > MAX_LENGTH:
        raise ValueError(
            f"coach_voice.validate: text exceeds {MAX_LENGTH} chars: {text!r}"
        )
    if not _has_imperative_marker(text):
        raise ValueError(
            "coach_voice.validate: text lacks imperative voice — "
            f"{text!r}. Use a recognized verb / urgency marker."
        )


__all__ = ["directive", "status_line", "validate", "MAX_LENGTH"]
