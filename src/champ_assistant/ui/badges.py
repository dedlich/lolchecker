"""Reusable visual badges — rank pill, tier pill, kill counter.

Centralizing these as small QLabel subclasses keeps the per-widget code
free of styling boilerplate and makes design tweaks a single-file change.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

from . import styles

def _darken(hex_color: str, factor: float) -> str:
    """Return a darker variant of an #RRGGBB string."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r, g, b = int(r * factor), int(g * factor), int(b * factor)
    return f"#{r:02X}{g:02X}{b:02X}"


# Riot's official tier brand colors
RANK_COLORS = {
    "IRON":        "#7E7E7E",
    "BRONZE":      "#A36A45",
    "SILVER":      "#A0A8B7",
    "GOLD":        "#E0B046",
    "PLATINUM":    "#4FA9A1",
    "EMERALD":     "#3FCB7E",
    "DIAMOND":     "#5499D6",
    "MASTER":      "#A269D6",
    "GRANDMASTER": "#D86060",
    "CHALLENGER":  "#F4D169",
}

# Each tier renders with a slight gradient (top -> bottom slightly darker)
RANK_GRADIENTS = {
    tier: (color, _darken(color, 0.65))
    for tier, color in RANK_COLORS.items()
}


class RankPill(QLabel):
    """Compact pill showing tier + division + LP, colored by tier."""

    def __init__(self, tier: str = "", division: str = "", lp: int = 0) -> None:
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_rank(tier=tier, division=division, lp=lp)

    def set_rank(self, *, tier: str, division: str, lp: int) -> None:
        if not tier:
            self.setText("UNRANKED")
            self.setStyleSheet(
                f"background-color: {styles.BG_TERTIARY};"
                f" color: {styles.TEXT_MUTED};"
                f" padding: 2px 8px; border-radius: 10px;"
                f" font-size: {styles.FS_CAPTION}px; font-weight: 700;"
                f" letter-spacing: 0.5px;"
            )
            return
        if tier in ("MASTER", "GRANDMASTER", "CHALLENGER"):
            text = f"{tier[:5]} {lp}"
        else:
            text = f"{tier[:4]} {division} · {lp}"
        top, bottom = RANK_GRADIENTS.get(tier, (styles.TEXT_MUTED, styles.TEXT_MUTED))
        self.setText(text)
        self.setStyleSheet(
            f"background: qlineargradient("
            f"x1:0, y1:0, x2:0, y2:1, stop:0 {top}, stop:1 {bottom});"
            f" color: white; padding: 2px 8px; border-radius: 10px;"
            f" font-size: {styles.FS_CAPTION}px; font-weight: 700;"
            f" letter-spacing: 0.5px;"
        )


class TierBadge(QLabel):
    """Small colored label showing a champion strength tier (S+/S/A/B...)."""

    def __init__(self, tier: str | None) -> None:
        super().__init__(tier or "—")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        color = styles.TIER_COLORS.get(tier or "", styles.TEXT_MUTED)
        self.setStyleSheet(
            f"color: {color};"
            f" background-color: rgba(255, 255, 255, 8);"
            f" font-weight: 700; padding: 2px 8px;"
            f" border: 1px solid {color};"
            f" border-radius: 4px;"
            f" font-size: {styles.FS_LABEL}px;"
        )
        self.setFixedHeight(20)


class StateDot(QLabel):
    """Small connection-state indicator dot (12x12)."""

    COLORS = {
        "disconnected": styles.TEXT_MUTED,
        "waiting":      styles.WARNING,
        "connected":    styles.SUCCESS,
        "reconnecting": styles.WARNING,
    }

    def __init__(self, state: str = "disconnected") -> None:
        super().__init__()
        self.setFixedSize(10, 10)
        self.set_state(state)

    def set_state(self, state: str) -> None:
        color = self.COLORS.get(state, styles.TEXT_MUTED)
        self.setStyleSheet(
            f"background-color: {color}; border-radius: 5px;"
        )
