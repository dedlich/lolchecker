"""Design tokens + Qt stylesheet generation.

Layered palette: deep base for the window, lifted secondary for panels,
soft tertiary for inset rows. Tier colors stay distinct so a glance at the
pick list reads at a glance.
"""
from __future__ import annotations

# Base palette
BG_PRIMARY = "#0B0F14"
BG_SECONDARY = "#161C24"
BG_TERTIARY = "#1F2632"
BG_ELEVATED = "#243042"  # hover / active panels

TEXT_PRIMARY = "#E8EAED"
TEXT_SECONDARY = "#B6BCC4"
TEXT_MUTED = "#7A848F"
TEXT_DISABLED = "#4D5662"

ACCENT = "#5BA8FF"
ACCENT_DIM = "#2A5180"
DANGER = "#FF6B6B"
SUCCESS = "#7FCC7F"
WARNING = "#FFB84A"

BORDER = "#28303C"
BORDER_STRONG = "#3A4555"

TIER_S_PLUS = "#FF6B9D"
TIER_S = "#FFB84A"
TIER_A = "#7FCC7F"
TIER_B = "#9FA8B4"
TIER_C = TEXT_MUTED
TIER_D = TEXT_DISABLED

# Cooldown gradient (for spell timers): hot when fresh, cool when ready
CD_HOT = "#FF6B6B"        # > 50% remaining
CD_WARM = "#FFB84A"       # 20-50%
CD_COOL = "#7FCC7F"       # < 20% / ready

# Order: native first (SF Pro on macOS, Segoe UI on Windows), Inter as a
# nice-to-have fallback if the user has it installed.
FONT_FAMILY = "-apple-system, Segoe UI, Inter, sans-serif"
FONT_MONO = "SF Mono, Menlo, Consolas, monospace"

SPACING_GRID = 8
SPACING_TIGHT = 4
SPACING_WIDE = 12
RADIUS = 8
RADIUS_SMALL = 4


TIER_COLORS: dict[str, str] = {
    "S+": TIER_S_PLUS,
    "S": TIER_S,
    "A": TIER_A,
    "B": TIER_B,
    "C": TIER_C,
    "D": TIER_D,
}


def cooldown_color(fraction_remaining: float) -> str:
    """Pick a color for a cooldown timer based on how much time is left.

    ``fraction_remaining`` is the share of the cooldown still ticking
    (1.0 = just used, 0.0 = ready). Bright/hot when fresh, fading to cool
    as the timer winds down so the eye can scan the worst threats first.
    """
    if fraction_remaining > 0.5:
        return CD_HOT
    if fraction_remaining > 0.2:
        return CD_WARM
    return CD_COOL


def global_stylesheet() -> str:
    """Qt stylesheet applied to the top-level window."""
    return f"""
        QMainWindow, QWidget#root {{
            background-color: {BG_PRIMARY};
        }}
        QLabel {{
            color: {TEXT_PRIMARY};
            font-family: {FONT_FAMILY};
        }}
        QLabel[role="muted"] {{
            color: {TEXT_MUTED};
        }}
        QLabel[role="secondary"] {{
            color: {TEXT_SECONDARY};
        }}
        QLabel#sectionTitle {{
            font-size: 11px;
            font-weight: 700;
            color: {TEXT_MUTED};
            text-transform: uppercase;
            letter-spacing: 0.8px;
        }}
        QLabel#title {{
            font-size: 17px;
            font-weight: 700;
            color: {TEXT_PRIMARY};
            letter-spacing: -0.2px;
        }}
        QLabel[role="timer"] {{
            font-family: {FONT_MONO};
            font-size: 16px;
            font-weight: 700;
            letter-spacing: 0.5px;
        }}
        QLabel[role="timer-small"] {{
            font-family: {FONT_MONO};
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.3px;
        }}
        QFrame[panel="true"] {{
            background-color: {BG_SECONDARY};
            border-radius: {RADIUS}px;
            border: 1px solid {BORDER};
        }}
        QFrame[card="true"] {{
            background-color: {BG_TERTIARY};
            border-radius: {RADIUS_SMALL}px;
            border: 1px solid {BORDER};
        }}
        QFrame[card="true"]:hover {{
            border-color: {BORDER_STRONG};
        }}
        QFrame[role="row"] {{
            background-color: {BG_TERTIARY};
            border-radius: {RADIUS_SMALL}px;
        }}
        QFrame[role="row"]:hover {{
            background-color: {BG_ELEVATED};
        }}
        QStatusBar {{
            background-color: {BG_SECONDARY};
            color: {TEXT_MUTED};
            border-top: 1px solid {BORDER};
            font-size: 11px;
        }}
        QToolTip {{
            background-color: {BG_ELEVATED};
            color: {TEXT_PRIMARY};
            border: 1px solid {BORDER_STRONG};
            border-radius: {RADIUS_SMALL}px;
            padding: 4px 8px;
        }}
    """
