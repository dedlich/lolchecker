"""Design system tokens + global Qt stylesheet.

Approach:
  - Layered backgrounds (primary -> secondary -> tertiary -> elevated) so
    nested cards visually separate without hard borders.
  - Translucent borders (rgba) instead of solid 1px lines so chrome reads
    softer at all opacity levels.
  - Tier / state colors as semantic constants so panels reuse the same
    blue for "info", same green for "success", etc.
  - One typographic scale: 10/11/12/14/17 — used consistently.
"""
from __future__ import annotations

# --------------------------------------------------------------------------
# Backgrounds (layered)
# --------------------------------------------------------------------------
BG_PRIMARY    = "#0A0E14"
BG_SECONDARY  = "#141A22"
BG_TERTIARY   = "#1D2530"
BG_ELEVATED   = "#27313F"
BG_INTERACT   = "#2F3A4A"  # hover

# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------
TEXT_PRIMARY    = "#ECEEF1"
TEXT_SECONDARY  = "#B6BCC4"
TEXT_MUTED      = "#7A848F"
TEXT_DISABLED   = "#4D5662"

# --------------------------------------------------------------------------
# Brand + state colors
# --------------------------------------------------------------------------
ACCENT          = "#5BA8FF"
ACCENT_BRIGHT   = "#7DBBFF"
ACCENT_DIM      = "#2A5180"
ACCENT_FAINT    = "rgba(91, 168, 255, 30)"

DANGER          = "#FF6B6B"
DANGER_DIM      = "#7D2F2F"
WARNING         = "#FFB84A"
SUCCESS         = "#7FCC7F"
INFO            = ACCENT

# Team semantic colors — used by the scoreboard and any other widget
# that paints "ally" vs "enemy" data side by side. Centralized so a
# colorblind-friendly retheme is a one-line change instead of a sweep.
TEAM_ALLY       = "#6BBBFF"   # cool blue — ally side
TEAM_ENEMY      = DANGER      # warm red — enemy side
TEAM_NEUTRAL    = TEXT_MUTED  # neutral / no-delta separator

# --------------------------------------------------------------------------
# Borders (translucent so they read soft at any opacity)
# --------------------------------------------------------------------------
BORDER          = "rgba(60, 70, 85, 180)"
BORDER_STRONG   = "rgba(90, 105, 125, 220)"
BORDER_FAINT    = "rgba(60, 70, 85, 90)"
BORDER_ACCENT   = "rgba(91, 168, 255, 140)"

# --------------------------------------------------------------------------
# Tier colors (champion strength badges)
# --------------------------------------------------------------------------
TIER_S_PLUS     = "#FF6B9D"
TIER_S          = "#FFB84A"
TIER_A          = "#7FCC7F"
TIER_B          = "#9FA8B4"
TIER_C          = TEXT_MUTED
TIER_D          = TEXT_DISABLED

# Cooldown urgency (used in spell tracker badges)
CD_HOT          = "#FF6B6B"
CD_WARM         = "#FFB84A"
CD_COOL         = "#7FCC7F"

# --------------------------------------------------------------------------
# Typography
# --------------------------------------------------------------------------
FONT_FAMILY     = "-apple-system, Segoe UI, Inter, sans-serif"
FONT_MONO       = "SF Mono, Menlo, Consolas, monospace"

FS_CAPTION      = 10
FS_LABEL        = 11
FS_BODY         = 12
FS_HEADING      = 14
FS_TITLE        = 17

# --------------------------------------------------------------------------
# Spacing + radius (8pt grid)
# --------------------------------------------------------------------------
SPACING_TIGHT   = 4
SPACING_GRID    = 8
SPACING_WIDE    = 12
SPACING_LOOSE   = 16

RADIUS_SMALL    = 4
RADIUS          = 8
RADIUS_LARGE    = 12

# --------------------------------------------------------------------------
# Padding/margin scale — explicit (top, right, bottom, left) tuples.
# Use these instead of inline magic numbers so a sweep audit verifies
# every widget paints to the same rhythm.
# --------------------------------------------------------------------------
PAD_ROW_TIGHT   = (6, 8, 6, 8)     # compact rows (camp buttons, mini lists)
PAD_ROW         = (7, 10, 7, 10)   # standard rows (objective, summoner, lobby)
PAD_PANEL       = (10, 12, 10, 12) # outer panel padding
PAD_DIALOG      = (18, 20, 18, 20) # settings dialog

# --------------------------------------------------------------------------
# Shadow profiles — paired with QGraphicsDropShadowEffect. Subtle by
# default; see the spec ("Avoid heavy glow effects").
# --------------------------------------------------------------------------
SHADOW_FLOAT    = {"blur": 22, "x": 0, "y": 3, "alpha": 160}  # floating widgets
SHADOW_PANEL    = {"blur": 12, "x": 0, "y": 2, "alpha": 90}   # nested panels (rare)

# --------------------------------------------------------------------------
# Animation timing — single source so every fade/transition uses the
# same cadence (spec: 150-200 ms).
# --------------------------------------------------------------------------
ANIM_FAST_MS    = 120
ANIM_DEFAULT_MS = 180
ANIM_SLOW_MS    = 240

# --------------------------------------------------------------------------
# Tier name -> color (used by widgets that show champion/pick tiers)
# --------------------------------------------------------------------------
TIER_COLORS: dict[str, str] = {
    "S+": TIER_S_PLUS,
    "S":  TIER_S,
    "A":  TIER_A,
    "B":  TIER_B,
    "C":  TIER_C,
    "D":  TIER_D,
}


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
            text-transform: uppercase;
            letter-spacing: 1.2px;
            padding: 2px 0;
        }}
        QLabel#title {{
            font-size: {FS_TITLE}px;
            font-weight: 700;
            color: {TEXT_PRIMARY};
            letter-spacing: -0.2px;
        }}
        QLabel[role="timer"] {{
            font-family: {FONT_MONO};
            font-size: 16px;
            font-weight: 700;
            letter-spacing: 0.4px;
            font-variant-numeric: tabular-nums;
        }}
        QLabel[role="timer-small"] {{
            font-family: {FONT_MONO};
            font-size: {FS_LABEL}px;
            font-weight: 700;
            letter-spacing: 0.3px;
            font-variant-numeric: tabular-nums;
        }}
        QLabel[role="numeric"] {{
            font-family: {FONT_MONO};
            font-variant-numeric: tabular-nums;
        }}

        /* Panels: layered cards with soft borders */
        QFrame[panel="true"] {{
            background-color: {BG_SECONDARY};
            border-radius: {RADIUS}px;
            border: 1px solid {BORDER_FAINT};
        }}
        QFrame[card="true"] {{
            background-color: {BG_TERTIARY};
            border-radius: {RADIUS_SMALL}px;
            border: 1px solid {BORDER_FAINT};
        }}
        QFrame[card="true"]:hover {{
            border-color: {BORDER};
            background-color: {BG_ELEVATED};
        }}
        QFrame[role="row"] {{
            background-color: {BG_TERTIARY};
            border-radius: {RADIUS_SMALL}px;
        }}
        QFrame[role="row"]:hover {{
            background-color: {BG_INTERACT};
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
