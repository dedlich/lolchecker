"""Win-condition advisor — coaching anchor for a single match.

Computed once at champion lock-in. Returns a structured plan the
player can refer to throughout the game: how this game closes, which
power spikes matter, which enemy threats to respect, and the one
mistake that loses it. The Game Plan column surfaces the headline +
primary path; the rest of the recommendation engine references the
plan to keep its calls tied to the game's win path.

Pure data + curated heuristics — no I/O, no LLM. Stable through the
match because the matchup itself is stable. If the user re-picks
during champ-select, ``compute_win_condition`` runs again and the
panel updates.

Voice: German, decisive, action-anchored. Same register as the
in-game recommendation rules so the player reads one consistent
coaching voice end to end.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .build_adapter import (
    BURST_KEYS,
    HARD_CC_KEYS,
    MOBILITY_KEYS,
    SUSTAIN_KEYS,
    damage_profile_for_tags,
)

if TYPE_CHECKING:
    from ..data.models import TagsData
    from .build_engine import ChampionArchetype


@dataclass(frozen=True)
class WinCondition:
    """Per-game coaching anchor.

    Every field is non-empty when the function returns a result —
    callers don't need to handle partial data. ``compute_win_condition``
    returns ``None`` only when essential inputs are missing
    (no archetype, no champion key); the UI then falls back to
    "Lock in to generate plan" placeholder.
    """
    headline: str
    """One-line plan headline. ≤70 chars so it fits the Game Plan
    column without wrapping. Imperative + concrete."""

    primary_path: str
    """How to execute the headline — one or two short clauses
    listing the main game-plan moves (where to play, when to roam,
    how to win teamfights)."""

    spikes: tuple[str, ...]
    """2-3 power-spike anchors as short labels. Format:
    ``"<trigger> — <effect>"`` (e.g. ``"L6 Ult — Pick-Window startet"``).
    Drives the right-column power-spike preview + decision-engine
    recall-window calls."""

    threats: tuple[str, ...]
    """1-2 enemy threats with explicit counter. Format:
    ``"<champion> <ability/pattern> — <counter>"``."""

    avoid: str
    """The single biggest mistake that loses this game. Imperative,
    consequence-explicit."""

    archetype_label: str = ""
    """Short human label for the player's archetype + role used to
    title the plan in the UI ("Mid-Lane Mage" / "Top-Lane Bruiser").
    Empty falls back to 'Game Plan'."""

    raw_tags: tuple[str, ...] = field(default_factory=tuple)
    """Raw structural tags for this game's plan (e.g.
    ``("ap_heavy_enemy", "burst_threat", "scaling_carry")``). Used by
    the recommendation engine to tag recs that serve this plan
    without re-deriving the heuristics. Internal — UI ignores."""


# ── Curated voice fragments ────────────────────────────────────────────────

# Per play-style headline templates. Each entry is (preferred / fallback)
# so the heuristic can pick the more specific one when the team comp
# matches and degrade to the generic when it doesn't.
_HEADLINES: dict[str, dict[str, str]] = {
    "marksman": {
        "scaling": "Scaling-Carry — bis 25 Min überleben, dann Teamfights führen",
        "default": "AD-Carry — Teamfights mit Peel führen, Side-Wellen vermeiden",
    },
    "mage": {
        "burst_window": "Burst-Mid — frühe Pick-Map mit Roams 1:30 / 3:00",
        "scaling": "Skalierungs-Mage — Wellen unter Turm, sichere CS-Lead, dann Teamfights",
        "default": "Wave-Control + Roams — Mid-Prio kontrollieren, Side carryen",
    },
    "assassin": {
        "pick_map": "Pick-Map — isolierte Ziele über Side-Lanes finden",
        "default": "Snowball über Roams — niemals 5v5, immer Pick-Vorteil suchen",
    },
    "bruiser": {
        "splitpush": "Side-Splitpush — Welle drücken, TP-Druck, 1v1-Duelle gewinnen",
        "default": "Frontline-Bruiser — Side-Pressure + Teamfight-Engage timen",
    },
    "tank": {
        "default": "Engage-Tank — Vision + Initiate, Carries peelen, Damage soaken",
    },
    "support": {
        "enchanter": "Carry-Peel — ADC schützen, Sight-Control, frühe Roams nur safe",
        "engage": "Engage-Support — Picks erzwingen, Vision-Krieg, Carry roams setzen",
        "default": "Vision + Peel — sicheres Bot, Roams bei Mid-Lane-Push",
    },
    "specialist": {
        "default": "Specialist-Plan — Champion-Stärken nutzen, Enemy-Weak-Side fokussieren",
    },
}

# Per play-style primary-path templates.
_PATHS: dict[str, str] = {
    "marksman": (
        "Sicheres Farmen unter Peel, Drachen-Setups mit Bot-Prio, "
        "Teamfights von hinten, nie ohne Flash."
    ),
    "mage": (
        "Mid-Wave kontrollieren, Roams bei pushed Wave, "
        "Side-Lanes überlegen, Vision für Pick-Plays."
    ),
    "assassin": (
        "Side-Lane-Pressure, Vision in Jungle, Ult-Roams Mid+Bot, "
        "5v5 nur mit Engage-Pick davor."
    ),
    "bruiser": (
        "Side gegnerische Welle drücken, TP/Ult-Cooldown tracken, "
        "Teamfight-Engage timen, niemals catch-out."
    ),
    "tank": (
        "Frontline für Carries, Vision-Tower setzen, "
        "Initiate nur wenn Carry follow-up hat."
    ),
    "support": (
        "Bot mit Pinks dichten, Mid-Roams nach pushed Wave, "
        "Carries durch Teamfights peelen."
    ),
    "specialist": (
        "Power-Spikes timen, Enemy-Side-Lane-Druck nutzen, "
        "Teamfight-Position durch Vision sichern."
    ),
}

# Per play-style power-spike anchors. Override per archetype/role
# beats the default when the heuristic identifies a more-specific path.
_SPIKES: dict[str, tuple[str, ...]] = {
    "marksman": (
        "L6 Ult — Trade-Drohung",
        "1. Item — Drache-Setup-Fenster",
        "3-Item-Spike — Teamfight-Carry online",
    ),
    "mage": (
        "L6 Ult — Roam + Pick-Map startet",
        "Lich Bane / 1300g Component — Wellen flachklatschen",
        "Hourglass 2600g — Anti-Burst-Spike",
    ),
    "assassin": (
        "L6 Ult — erstes Pick-Fenster",
        "1. Item (Eclipse / Profane) — One-Shot-Drohung",
        "Gold-Lead 1500g+ — Side-Roam für Snowball",
    ),
    "bruiser": (
        "L6 Ult — Lane-Druck + 1v1-Win",
        "Stridebreaker / Trinity — Side-Splitpush online",
        "Sterak / Death's Dance — Sustain-Spike für TF",
    ),
    "tank": (
        "L6 Ult — Engage-Drohung",
        "Sunfire / Iceborn — Wave-Clear + Frontline",
        "2-Item-Spike — Initiate ohne zu sterben",
    ),
    "support": (
        "L6 Ult — Engage / Disengage-Drohung",
        "Mythic-Item — Aura aktiv, Carries-Spike",
        "Boots+Sightstone — Vision-Krieg startet",
    ),
    "specialist": (
        "L6 Ult — Champion-Win-Window",
        "1. Item — Kit voll online",
        "2-Item-Spike — Teamfight-Power",
    ),
}


def _format_threat(champion_key: str) -> str:
    """Map a champion key to a one-line threat with counter."""
    if champion_key in BURST_KEYS:
        return f"{champion_key}: Burst-One-Shot → Stasis / Banshee timen"
    if champion_key in MOBILITY_KEYS:
        return f"{champion_key}: Dash/Catch → Slow-Aura + nicht solo erwischen lassen"
    if champion_key in HARD_CC_KEYS:
        return f"{champion_key}: Hard-CC → QSS / Mercurial / nicht überextend"
    if champion_key in SUSTAIN_KEYS:
        return f"{champion_key}: Sustain → Grievous Wounds vor 2. Item"
    return ""


def _select_threats(enemy_keys: list[str]) -> tuple[str, ...]:
    """Pick the 1-2 most-actionable enemy threats.

    Priority order: Burst > Mobility > Hard-CC > Sustain. Reflects
    "what kills me first" — assassins / pick mages are the faster
    death sentence than tank engage.
    """
    seen: set[str] = set()
    threats: list[str] = []

    for priority_set in (BURST_KEYS, MOBILITY_KEYS, HARD_CC_KEYS, SUSTAIN_KEYS):
        if len(threats) >= 2:
            break
        for key in enemy_keys:
            if not key or key in seen:
                continue
            if key in priority_set:
                tip = _format_threat(key)
                if tip:
                    threats.append(tip)
                    seen.add(key)
                    if len(threats) >= 2:
                        break
    return tuple(threats)


def _enemy_damage_split(
    enemy_keys: list[str], tags: "TagsData",
) -> tuple[int, int]:
    """Quick AP/AD count to drive headline-template selection."""
    ap = ad = 0
    for key in enemy_keys:
        if not key:
            continue
        profile = damage_profile_for_tags(tags.tags_for(key))
        if "AP" in profile:
            ap += 1
        if "AD" in profile:
            ad += 1
    return ap, ad


def _select_headline(
    archetype: "ChampionArchetype",
    enemy_keys: list[str],
    raw_tags: list[str],
) -> str:
    """Pick the headline template best matching the matchup.

    For each archetype we keep one or two specific variants + a
    default. The variant selection looks at enemy comp signals
    (heavy burst → assassin's pick-map variant, scaling carries on
    the team → marksman's scaling variant)."""
    bucket = _HEADLINES.get(archetype.play_style, _HEADLINES["specialist"])

    # Marksman: identify whether team is scaling-oriented.
    if archetype.play_style == "marksman":
        scaling_hint = "scaling_team" in raw_tags or len(enemy_keys) >= 3
        return bucket.get("scaling" if scaling_hint else "default", bucket["default"])

    # Mage: split between burst-window (against squishies) and scaling.
    if archetype.play_style == "mage":
        burst_window = sum(1 for k in enemy_keys if k in BURST_KEYS) <= 1 \
            and "burst_kit" in raw_tags
        if burst_window:
            return bucket.get("burst_window", bucket["default"])
        return bucket.get("scaling", bucket["default"])

    # Assassin: pick-map variant when enemy team has squishy carries
    # (3+ AP/AD ranged threats — they get isolated easier).
    if archetype.play_style == "assassin":
        if "isolated_carries" in raw_tags:
            return bucket.get("pick_map", bucket["default"])
        return bucket["default"]

    # Bruiser: splitpush variant when ally team is scaling and we
    # have TP available (proxy: archetype hints, not currently in
    # ChampionArchetype). Default is fine for now.
    if archetype.play_style == "bruiser":
        return bucket.get("splitpush", bucket["default"])

    # Support: enchanter vs engage. Identified by archetype's play
    # style which collapses both into "support" — disambiguate via
    # damage type (enchanters are AP, engage tank-supports are
    # item_damage_type=physical even though they're tank/AP kits).
    if archetype.play_style == "support":
        if archetype.item_damage_type == "physical":
            return bucket.get("engage", bucket["default"])
        return bucket.get("enchanter", bucket["default"])

    return bucket["default"]


def _select_avoid(archetype: "ChampionArchetype", enemy_keys: list[str]) -> str:
    """Pick the single biggest mistake to avoid based on archetype +
    visible enemy threats."""
    has_burst = any(k in BURST_KEYS for k in enemy_keys)
    has_mobility = any(k in MOBILITY_KEYS for k in enemy_keys)
    has_engage = any(k in HARD_CC_KEYS for k in enemy_keys)

    if archetype.play_style == "marksman":
        if has_burst:
            return "Niemals ohne Peel-Front — ein Engage und du stirbst zuerst."
        return "Kein Side-Farmen ohne Vision — du bist Catch-Target #1."
    if archetype.play_style == "mage":
        if has_mobility:
            return "Nicht overextenden — ein Dash + Engage und du bist tot."
        return "Niemals ohne Flash poken — TF-Zone ist deine Lebensversicherung."
    if archetype.play_style == "assassin":
        return "Kein 5v5 in Lategame — verlierst Skalierung gegen Carries."
    if archetype.play_style == "bruiser":
        if has_engage:
            return "Nicht catch-out gehen — Engage von hinten beendet das Spiel."
        return "Kein 1v2 ins offene River — TP retten oder vermeiden."
    if archetype.play_style == "tank":
        return "Nicht ohne Carry-Follow-Up engagen — Solo-Engage verschenkt Initiate."
    if archetype.play_style == "support":
        if has_burst:
            return "Niemals ADC alleine lassen — Pick + One-Shot kostet das Bot-Lane."
        return "Vision-Pause = Catch-Pause. Pinks vor Drache priorisieren."
    return "Spiel zur Stärke deines Champions — kein Brute-Force in fremde Win-Cons."


def _archetype_label(archetype: "ChampionArchetype") -> str:
    """Short German label for the Game Plan title bar."""
    role_map = {
        "TOP": "Top-Lane",
        "JUNGLE": "Jungle",
        "MIDDLE": "Mid-Lane",
        "BOTTOM": "Bot-Lane",
        "SUPPORT": "Support",
    }
    style_map = {
        "marksman": "ADC",
        "mage": "Mage",
        "assassin": "Assassin",
        "bruiser": "Bruiser",
        "tank": "Tank",
        "support": "Support",
        "specialist": "Specialist",
    }
    role = role_map.get(archetype.primary_position, "")
    style = style_map.get(archetype.play_style, "Champion")
    if role and style and role.lower() != style.lower():
        return f"{role} {style}"
    return style


def _derive_raw_tags(
    archetype: "ChampionArchetype",
    enemy_keys: list[str],
    ally_keys: list[str],
    tags: "TagsData",
) -> tuple[str, ...]:
    """Build the structural tag list for downstream rule consumers.

    These tags are NOT user-facing — they let the decision engine
    cheaply check "does my rec serve the win condition" by string
    membership instead of re-deriving the heuristics.
    """
    out: list[str] = []
    ap, ad = _enemy_damage_split(enemy_keys, tags)
    if ap >= 3 and ap - ad >= 2:
        out.append("ap_heavy_enemy")
    if ad >= 3 and ad - ap >= 2:
        out.append("ad_heavy_enemy")

    if any(k in BURST_KEYS for k in enemy_keys):
        out.append("burst_threat")
    if any(k in MOBILITY_KEYS for k in enemy_keys):
        out.append("mobility_threat")
    if any(k in HARD_CC_KEYS for k in enemy_keys):
        out.append("cc_threat")
    if any(k in SUSTAIN_KEYS for k in enemy_keys):
        out.append("sustain_threat")

    # Ally-team flavor: scaling team (3+ scaling-tagged champs) means
    # the carry needs to live until the late game; engage team means
    # we have follow-up for tank initiates.
    scaling_signals = sum(
        1 for k in ally_keys
        if any(t in {"Late-Game", "Hyper-Carry", "Scaling"}
               for t in tags.tags_for(k))
    )
    if scaling_signals >= 3:
        out.append("scaling_team")
    engage_signals = sum(1 for k in ally_keys if k in HARD_CC_KEYS)
    if engage_signals >= 2:
        out.append("engage_team")

    # Per-archetype kit flavor (used by the headline picker).
    if archetype.play_style == "mage" and "BURST" in (
        getattr(archetype, "scaling_attributes", frozenset()) or frozenset()
    ):
        out.append("burst_kit")
    if archetype.play_style == "assassin" and ap + ad >= 3:
        out.append("isolated_carries")

    return tuple(out)


def archetype_from_tags(
    champion_key: str, role: str, tags: list[str],
) -> "ChampionArchetype":
    """Build a lightweight ``ChampionArchetype`` from DataDragon tags +
    LCU role token. Used as a fast-path when the full Meraki dict
    isn't available (champ-select pipeline only has the basic
    Champion model).

    Heuristic priority order matches ``detect_archetype`` for the
    same-named outputs but skips the Meraki-only details
    (``has_mana`` / ``scaling_attributes``) that the WinCondition
    layer doesn't read. Conservative defaults when tags don't match.
    """
    from .build_engine import ChampionArchetype

    tag_set = set(tags)
    is_ranged = any(t in tag_set for t in ("Marksman",))
    # Damage type — tag-driven proxy. AP if Mage tag present, else AD.
    is_ap = "Mage" in tag_set
    damage_type = "magic" if is_ap else "physical"

    # Play style priority — stays close to detect_archetype's logic
    # but works on DataDragon tags.
    if "Marksman" in tag_set:
        play_style = "marksman"
    elif "Mage" in tag_set and "Assassin" in tag_set:
        play_style = "mage"  # AP assassins like Akali / LeBlanc
    elif "Mage" in tag_set:
        play_style = "mage"
    elif "Assassin" in tag_set:
        play_style = "assassin"
    elif "Support" in tag_set:
        play_style = "support"
    elif "Tank" in tag_set and ("Fighter" in tag_set or len(tag_set) == 1):
        play_style = "tank"
    elif "Fighter" in tag_set or "Tank" in tag_set:
        play_style = "bruiser"
    else:
        play_style = "specialist"

    primary_position = (role or "").upper().strip() or "MIDDLE"

    return ChampionArchetype(
        damage_type=damage_type,
        item_damage_type=damage_type,
        play_style=play_style,
        is_ranged=is_ranged,
        has_mana=True,
        primary_position=primary_position,
        scaling_attributes=frozenset(),
    )


def compute_win_condition(
    *,
    my_champion_key: str,
    archetype: "ChampionArchetype | None",
    ally_team_keys: list[str],
    enemy_team_keys: list[str],
    tags: "TagsData",
) -> WinCondition | None:
    """Build a WinCondition for the locked champion and current matchup.

    Returns ``None`` when essential data is missing (no archetype, no
    champion key, or empty enemy team) — caller (UI) falls back to the
    "Lock in to generate plan" placeholder.

    Pure: only reads the curated tag sets + provided arguments. No
    network, no I/O.
    """
    if not my_champion_key or archetype is None:
        return None
    enemy_keys = [k for k in (enemy_team_keys or []) if k]
    if not enemy_keys:
        return None
    ally_keys = [k for k in (ally_team_keys or []) if k]

    raw_tags = _derive_raw_tags(archetype, enemy_keys, ally_keys, tags)
    headline = _select_headline(archetype, enemy_keys, list(raw_tags))
    primary_path = _PATHS.get(archetype.play_style, _PATHS["specialist"])
    spikes = _SPIKES.get(archetype.play_style, _SPIKES["specialist"])
    threats = _select_threats(enemy_keys)
    avoid = _select_avoid(archetype, enemy_keys)
    label = _archetype_label(archetype)

    return WinCondition(
        headline=headline,
        primary_path=primary_path,
        spikes=spikes,
        threats=threats,
        avoid=avoid,
        archetype_label=label,
        raw_tags=raw_tags,
    )
