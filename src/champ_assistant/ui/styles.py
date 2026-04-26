"""Design tokens + Qt stylesheet generation.

Tokens mirror the spec in masterplan §2.
"""
from __future__ import annotations

BG_PRIMARY = "#0F1419"
BG_SECONDARY = "#1A1F26"
TEXT_PRIMARY = "#E8EAED"
TEXT_MUTED = "#8B95A1"
ACCENT = "#4A9EFF"
BORDER = "#2A3038"

TIER_S_PLUS = "#FF6B9D"
TIER_S = "#FFB84A"
TIER_A = "#7FCC7F"
TIER_B = TEXT_MUTED
TIER_C = TEXT_MUTED
TIER_D = TEXT_MUTED

FONT_FAMILY = "Inter, -apple-system, Segoe UI, sans-serif"
SPACING_GRID = 8
RADIUS = 6


TIER_COLORS: dict[str, str] = {
    "S+": TIER_S_PLUS,
    "S": TIER_S,
    "A": TIER_A,
    "B": TIER_B,
    "C": TIER_C,
    "D": TIER_D,
}


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
        QLabel#sectionTitle {{
            font-size: 13px;
            font-weight: 600;
            color: {TEXT_MUTED};
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        QLabel#title {{
            font-size: 16px;
            font-weight: 700;
            color: {TEXT_PRIMARY};
        }}
        QFrame[panel="true"] {{
            background-color: {BG_SECONDARY};
            border-radius: {RADIUS}px;
            border: 1px solid {BORDER};
        }}
        QFrame[card="true"] {{
            background-color: {BG_SECONDARY};
            border-radius: {RADIUS}px;
            border: 1px solid {BORDER};
        }}
        QStatusBar {{
            background-color: {BG_SECONDARY};
            color: {TEXT_MUTED};
            border-top: 1px solid {BORDER};
        }}
    """
