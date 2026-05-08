"""Design system tokens + global Qt stylesheet.

This module is a **closed visual contract**. Every UI value the
application paints with originates here:

  * background / text colors (BG_*, TEXT_*)
  * brand + state colors (ACCENT, DANGER, WARNING, SUCCESS, INFO,
    TEAM_*)
  * borders (BORDER*, with rgba alpha so they soften under panels)
  * tier colors (TIER_*) and cooldown urgency (CD_*)
  * typography scale (FS_CAPTION..FS_DISPLAY) + family (FONT_FAMILY,
    FONT_MONO)
  * spacing on a 4-pt grid (SPACING_TIGHT/GRID/WIDE/LOOSE)
  * radius (RADIUS_SMALL/RADIUS/RADIUS_LARGE)
  * shadow profiles (SHADOW_FLOAT, SHADOW_PANEL)
  * animation cadence (ANIM_FAST_MS / ANIM_DEFAULT_MS / ANIM_SLOW_MS)

Tokens are annotated ``Final`` to declare the contract: PEP 591 says a
``Final`` name must not be reassigned. Static type-checkers enforce
this. Adding a new visual primitive is a deliberate edit to *this* file;
inline drift in widgets is caught by ``tests/lint/test_design_lockdown.py``.
"""
from __future__ import annotations

from typing import Final

# --------------------------------------------------------------------------
# Backgrounds (layered) — modernized with deeper navy + cleaner steps
# --------------------------------------------------------------------------
BG_PRIMARY:    Final[str] = "#0A0E14"
BG_SECONDARY:  Final[str] = "#121823"
BG_TERTIARY:   Final[str] = "#1B2433"
BG_ELEVATED:   Final[str] = "#252F40"
BG_INTERACT:   Final[str] = "#2F3A4D"   # hover
BG_HIGHLIGHT:  Final[str] = "rgba(77, 163, 255, 32)"  # accent-tinted active state

# --------------------------------------------------------------------------
# Text — sharper contrast on primary, more separation from secondary
# --------------------------------------------------------------------------
TEXT_PRIMARY:   Final[str] = "#F2F4F7"
TEXT_SECONDARY: Final[str] = "#B0B7C2"
TEXT_MUTED:     Final[str] = "#717A86"
TEXT_DISABLED:  Final[str] = "#4A535F"

# --------------------------------------------------------------------------
# Brand + state colors — cooler, more saturated accent (modern overlay tone)
# --------------------------------------------------------------------------
ACCENT:         Final[str] = "#4DA3FF"
ACCENT_BRIGHT:  Final[str] = "#7BBDFF"
ACCENT_DIM:     Final[str] = "#234D7A"
ACCENT_FAINT:   Final[str] = "rgba(77, 163, 255, 30)"

DANGER:         Final[str] = "#FF6B6B"
DANGER_BRIGHT:  Final[str] = "#FF8F8F"
DANGER_DIM:     Final[str] = "#7D2F2F"
WARNING:        Final[str] = "#FFB84A"
WARNING_BRIGHT: Final[str] = "#FFCE80"
SUCCESS:        Final[str] = "#7FCC7F"
SUCCESS_BRIGHT: Final[str] = "#A5DDA5"
INFO:           Final[str] = ACCENT

# Team semantic colors — used by the scoreboard and any other widget
# that paints "ally" vs "enemy" data side by side. Centralized so a
# colorblind-friendly retheme is a one-line change instead of a sweep.
TEAM_ALLY:      Final[str] = "#6BBBFF"   # cool blue — ally side
TEAM_ENEMY:     Final[str] = DANGER      # warm red — enemy side
TEAM_NEUTRAL:   Final[str] = TEXT_MUTED  # neutral / no-delta separator

# Riot-canonical ORDER/CHAOS team colors — used by widgets that display
# absolute team identity (gold-diff scoreboard) rather than the local
# player's ally/enemy perspective. Keep separate from TEAM_ALLY/ENEMY
# so a perspective-relative retheme doesn't accidentally break the
# scoreboard layout, which always shows blue-side left / red-side right.
TEAM_ORDER:     Final[str] = "#3CA0E0"   # Riot ORDER blue (blue side)
TEAM_CHAOS:     Final[str] = "#D04040"   # Riot CHAOS red  (red side)

# --------------------------------------------------------------------------
# Borders (translucent so they read soft at any opacity)
# --------------------------------------------------------------------------
BORDER:         Final[str] = "rgba(60, 70, 85, 180)"
BORDER_STRONG:  Final[str] = "rgba(90, 105, 125, 220)"
BORDER_FAINT:   Final[str] = "rgba(60, 70, 85, 90)"
BORDER_ACCENT:  Final[str] = "rgba(91, 168, 255, 140)"

# --------------------------------------------------------------------------
# Tier colors (champion strength badges)
# --------------------------------------------------------------------------
TIER_S_PLUS:    Final[str] = "#FF6B9D"
TIER_S:         Final[str] = "#FFB84A"
TIER_A:         Final[str] = "#7FCC7F"
TIER_B:         Final[str] = "#9FA8B4"
TIER_C:         Final[str] = TEXT_MUTED
TIER_D:         Final[str] = TEXT_DISABLED

# Cooldown urgency (used in spell tracker badges)
CD_HOT:         Final[str] = "#FF6B6B"
CD_WARM:        Final[str] = "#FFB84A"
CD_COOL:        Final[str] = "#7FCC7F"

# --------------------------------------------------------------------------
# Typography
# --------------------------------------------------------------------------
FONT_FAMILY:    Final[str] = "-apple-system, Segoe UI, Inter, sans-serif"
FONT_MONO:      Final[str] = "SF Mono, Menlo, Consolas, monospace"

FS_CAPTION:     Final[int] = 10
FS_LABEL:       Final[int] = 11
FS_BODY:        Final[int] = 12
FS_HEADING:     Final[int] = 14
FS_TITLE:       Final[int] = 18   # bumped from 17 — better section-header presence
FS_DISPLAY:     Final[int] = 22   # bumped from 18 — score/team-totals now read as hero numbers

# --------------------------------------------------------------------------
# Spacing + radius (4pt grid)
# --------------------------------------------------------------------------
SPACING_TIGHT:  Final[int] = 4
SPACING_GRID:   Final[int] = 8
SPACING_WIDE:   Final[int] = 12
SPACING_LOOSE:  Final[int] = 16

RADIUS_SMALL:   Final[int] = 4
RADIUS:         Final[int] = 8
RADIUS_LARGE:   Final[int] = 12
RADIUS_PILL:    Final[int] = 999  # full-rounded chips (tags, badges)

# --------------------------------------------------------------------------
# Padding/margin scale — explicit (top, right, bottom, left) tuples.
# Use these instead of inline magic numbers so a sweep audit verifies
# every widget paints to the same rhythm.
# --------------------------------------------------------------------------
PAD_ROW_TIGHT:  Final[tuple[int, int, int, int]] = (6, 8, 6, 8)
PAD_ROW:        Final[tuple[int, int, int, int]] = (7, 10, 7, 10)
PAD_PANEL:      Final[tuple[int, int, int, int]] = (10, 12, 10, 12)
PAD_DIALOG:     Final[tuple[int, int, int, int]] = (18, 20, 18, 20)

# --------------------------------------------------------------------------
# Shadow profiles — paired with QGraphicsDropShadowEffect. Subtle by
# default; see the spec ("Avoid heavy glow effects").
# --------------------------------------------------------------------------
SHADOW_FLOAT:   Final[dict[str, int]] = {"blur": 28, "x": 0, "y": 4, "alpha": 180}
SHADOW_PANEL:   Final[dict[str, int]] = {"blur": 16, "x": 0, "y": 2, "alpha": 110}
SHADOW_HOVER:   Final[dict[str, int]] = {"blur": 36, "x": 0, "y": 6, "alpha": 200}

# --------------------------------------------------------------------------
# Animation timing — single source so every fade/transition uses the
# same cadence (spec: 150-200 ms).
# --------------------------------------------------------------------------
ANIM_FAST_MS:    Final[int] = 120
ANIM_DEFAULT_MS: Final[int] = 180
ANIM_SLOW_MS:    Final[int] = 240

# --------------------------------------------------------------------------
# Tier name -> color (used by widgets that show champion/pick tiers)
# --------------------------------------------------------------------------
TIER_COLORS: Final[dict[str, str]] = {
    "S+": TIER_S_PLUS,
    "S":  TIER_S,
    "A":  TIER_A,
    "B":  TIER_B,
    "C":  TIER_C,
    "D":  TIER_D,
}


def gradient_panel_stylesheet(
    *,
    selector: str = "QFrame[panel='true']",
    radius: int | None = None,
) -> str:
    """Subtle vertical gradient for non-floating body panels (BuildCard,
    ItemsPanel, GamePlanPanel, RosterPanel, SummaryRow).

    Two-stop and shallow on purpose: just enough lighting cue to give
    depth without competing with content. Stays well above
    ``BG_PRIMARY`` so the panel bottom edge remains visible against the
    window. The drop shadow added by ``apply_panel_shadow`` defines
    the actual edge — no border needed.

    The default selector matches a property attribute (``panel='true'``)
    rather than the bare ``QFrame`` type. v1.10.116 fix: QLabel
    inherits from QFrame in Qt's class tree, so a bare ``QFrame { ... }``
    rule cascades to every QLabel descendant inside a panel — each
    section title rendered as its own gradient pill. Property selectors
    don't cascade to subclasses unless the property is set, which gives
    us the right "panel only" scoping. Callers that want the gradient
    must mark the frame via ``frame.setProperty('panel', True)``.
    """
    r = radius if radius is not None else RADIUS
    return (
        f"{selector} {{"
        " background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
        f"  stop:0 {BG_ELEVATED},"
        f"  stop:1 {BG_SECONDARY});"
        f" border: none;"
        f" border-radius: {r}px;"
        " }"
    )


def gradient_stripe_stylesheet(bright: str, base: str) -> str:
    """Vertical gradient for thin progress / segment bars (damage-type
    bar, power-spikes bar, etc). Top edge brighter, bottom darker —
    reads as lit-from-above. v1.10.112 lifts the previously flat
    stripes to give the stat blocks more visual presence."""
    return (
        "background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
        f"  stop:0 {bright},"
        f"  stop:1 {base});"
        " border-radius: 3px;"
    )


def gradient_card_stylesheet(
    *,
    selector: str = "QFrame",
    radius: int | None = None,
) -> str:
    """Lighter gradient for nested cards (matchup rows, picks rows,
    roster rows). Reads as one elevation step ABOVE a panel — same
    direction (top brighter, bottom darker) but starting from a higher
    baseline so cards "pop" against their parent panel."""
    r = radius if radius is not None else RADIUS
    return (
        f"{selector} {{"
        " background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
        f"  stop:0 {BG_INTERACT},"
        f"  stop:1 {BG_TERTIARY});"
        f" border: 1px solid {BORDER_FAINT};"
        f" border-radius: {r}px;"
        " }"
    )


def apply_panel_shadow(widget: object) -> None:
    """Attach the SHADOW_PANEL drop-shadow effect to ``widget``.

    Used by non-floating body panels in LiveCompanion so they lift
    visually off the main window background. Floating widgets already
    get their own shadow via ``FloatingWidget``.
    """
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import QGraphicsDropShadowEffect, QWidget

    assert isinstance(widget, QWidget)
    profile = SHADOW_PANEL
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(profile["blur"])
    effect.setOffset(profile["x"], profile["y"])
    effect.setColor(QColor(0, 0, 0, profile["alpha"]))
    widget.setGraphicsEffect(effect)


def floating_panel_stylesheet() -> str:
    """Single source of truth for the dark gradient + accent border that
    every floating mini-widget (scoreboard, minimap-timers, lobby-stats)
    paints into its ``QFrame[panel='true']`` root.

    Lives here (not duplicated inline in each widget file) so a future
    retheme — say, a lighter-mode build — is a one-line change rather
    than a sweep across every floating widget. Three identical-ish
    inline gradients with subtly different alphas was the old anti-pattern.
    """
    # Premium-solid: opaker als Glass-Style. The v2 spec walked the
    # glass attempt back — translucent panels disappeared into busy
    # game backgrounds. Higher alpha (240) gives a clean, readable
    # surface that still has subtle gradient depth + accent border.
    return (
        "QFrame[panel='true'] {"
        " background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
        "  stop:0 rgba(26, 33, 46, 240),"
        "  stop:0.4 rgba(18, 24, 35, 240),"
        "  stop:1 rgba(12, 16, 24, 245));"
        f" border: 1px solid {BORDER};"
        f" border-radius: {RADIUS_LARGE}px;"
        " }"
    )


def cooldown_color(fraction_remaining: float) -> str:
    """Map remaining-fraction (0..1) to a cooldown color. Hot when fresh,
    cool as the timer winds down."""
    if fraction_remaining > 0.5:
        return CD_HOT
    if fraction_remaining > 0.2:
        return CD_WARM
    return CD_COOL


def time_state_color(remaining_seconds: float | None) -> str:
    """Standard color ramp for objective/spawn timers across the whole UI.

    None / >60s → primary (calm / informational)
    ≤60s        → accent  (heads-up)
    ≤30s        → warning (urgent)
    ≤0s         → success (UP / spawned)

    Centralized here so every widget that displays a countdown (objectives,
    minimap-timers, scoreboard) paints with the same semantics.
    """
    if remaining_seconds is None:
        return TEXT_DISABLED
    if remaining_seconds <= 0:
        return SUCCESS
    if remaining_seconds <= 30:
        return WARNING
    if remaining_seconds <= 60:
        return ACCENT
    return TEXT_PRIMARY


def global_stylesheet() -> str:
    """Top-level QApplication stylesheet. Picks defaults that nested widgets
    inherit unless they override per-property."""
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
            font-size: {FS_LABEL}px;
            font-weight: 700;
            color: {TEXT_MUTED};
            letter-spacing: 1.2px;
            padding: 2px 0;
        }}
        QLabel#title {{
            font-size: {FS_TITLE}px;
            font-weight: 700;
            color: {TEXT_PRIMARY};
            letter-spacing: -0.2px;
        }}
        /* Tabular-numeric digit width comes from the FONT_MONO family
           itself — Qt's Stylesheet engine doesn't support
           font-variant-numeric (silent ignore + log spam). */
        QLabel[role="timer"] {{
            font-family: {FONT_MONO};
            font-size: 16px;
            font-weight: 700;
            letter-spacing: 0.4px;
        }}
        QLabel[role="timer-small"] {{
            font-family: {FONT_MONO};
            font-size: {FS_LABEL}px;
            font-weight: 700;
            letter-spacing: 0.3px;
        }}
        QLabel[role="numeric"] {{
            font-family: {FONT_MONO};
        }}

        /* Panels carry the depth (gradient + shadow + border).
           Inner cards / rows are intentionally borderless and
           flat-on-panel so the eye doesn't read "card-in-card-in-card"
           noise. Hover state alone signals interactivity — no border
           flicker, no gradient flicker.
           v1.10.108: walked back v1.10.107's nested-gradient + nested-
           border approach which produced the Russian-doll look. */
        QFrame[panel="true"] {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {BG_ELEVATED},
                stop:1 {BG_SECONDARY});
            border-radius: {RADIUS}px;
            border: none;
        }}
        QFrame[card="true"] {{
            background-color: transparent;
            border-radius: {RADIUS}px;
            border: none;
        }}
        QFrame[card="true"]:hover {{
            background-color: {BG_HIGHLIGHT};
        }}
        QFrame[role="row"] {{
            background-color: transparent;
            border-radius: {RADIUS}px;
            border: none;
        }}
        QFrame[role="row"]:hover {{
            background-color: {BG_HIGHLIGHT};
        }}

        /* Status + tray-related */
        QStatusBar {{
            background-color: {BG_SECONDARY};
            color: {TEXT_MUTED};
            border-top: 1px solid {BORDER_FAINT};
            font-size: {FS_LABEL}px;
        }}
        QToolTip {{
            background-color: {BG_ELEVATED};
            color: {TEXT_PRIMARY};
            border: 1px solid {BORDER_STRONG};
            border-radius: {RADIUS_SMALL}px;
            padding: 5px 9px;
        }}

        /* Generic push-buttons inherit a flat dark style; specific panels
           override with their own accent treatment. */
        QPushButton {{
            background-color: {BG_TERTIARY};
            color: {TEXT_PRIMARY};
            border: 1px solid {BORDER};
            border-radius: {RADIUS_SMALL}px;
            padding: 4px 12px;
            font-weight: 600;
            font-size: {FS_LABEL}px;
        }}
        QPushButton:hover {{
            background-color: {BG_ELEVATED};
            border-color: {BORDER_STRONG};
        }}
        QPushButton:pressed {{
            background-color: {BG_INTERACT};
        }}
        QPushButton:disabled {{
            background-color: {BG_TERTIARY};
            color: {TEXT_DISABLED};
            border-color: {BORDER_FAINT};
        }}

        /* Combobox + line edit (settings dialog) */
        QLineEdit, QComboBox {{
            background-color: {BG_TERTIARY};
            color: {TEXT_PRIMARY};
            border: 1px solid {BORDER};
            border-radius: {RADIUS_SMALL}px;
            padding: 5px 8px;
            font-size: {FS_BODY}px;
            selection-background-color: {ACCENT_DIM};
        }}
        QLineEdit:focus, QComboBox:focus {{
            border-color: {ACCENT};
        }}
        QComboBox::drop-down {{
            width: 20px;
            border: none;
        }}
        QComboBox QAbstractItemView {{
            background-color: {BG_SECONDARY};
            border: 1px solid {BORDER};
            color: {TEXT_PRIMARY};
            selection-background-color: {ACCENT_DIM};
        }}

        /* Slider (transparency control in title bar) */
        QSlider::groove:horizontal {{
            height: 4px;
            background: {BG_TERTIARY};
            border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            background: {ACCENT};
            width: 12px;
            margin: -4px 0;
            border-radius: 6px;
        }}
        QSlider::handle:horizontal:hover {{
            background: {ACCENT_BRIGHT};
        }}

        /* Menu (tray context) */
        QMenu {{
            background-color: {BG_SECONDARY};
            color: {TEXT_PRIMARY};
            border: 1px solid {BORDER};
            border-radius: {RADIUS_SMALL}px;
            padding: 4px;
        }}
        QMenu::item {{
            background-color: transparent;
            padding: 6px 14px;
            border-radius: {RADIUS_SMALL}px;
        }}
        QMenu::item:selected {{
            background-color: {ACCENT_DIM};
            color: {TEXT_PRIMARY};
        }}
        QMenu::separator {{
            height: 1px;
            background-color: {BORDER_FAINT};
            margin: 4px 8px;
        }}
    """
