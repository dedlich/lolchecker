"""Decision engine — turns raw LCDA state into actionable recommendations.

Strategy B1 — first foundation of the smartest pillar. Pure functions
over an LCDA snapshot; no Qt, no I/O, no asyncio. Each rule encodes
one heuristic the assistant would tell a teammate at that game state.

Honest scope (V1)
=================
This is a curated rule set, NOT an ML model. Five rules cover the
high-leverage patterns where a quick text nudge is genuinely useful:

  1. Drake-priority — drake up soon AND team has resources to take it
  2. Drake-give-up  — drake up AND team is behind, contest is bad ROI
  3. Gold-lead push — meaningful lead, time to convert into vision/objs
  4. Far-behind safe — significant deficit, correct play is wave clear
  5. Level-deficit  — average level gap large enough to lose any fight

Rules return ``Recommendation`` objects with severity + category +
text. The caller (UI panel, future B5 recommendation surface) picks
which to display.

What this is NOT
----------------
* No enemy-position detection (Vanguard-incompatible).
* No matchup-specific advice (would need curated dataset).
* No teamfight-readiness model — that requires ult availability data
  we don't have.
* Not an oracle — heuristics are approximations, the user remains in
  control. Every recommendation is a hint, never a command.

Adding rules
============
Each rule is a pure function ``(snapshot) -> Recommendation | None``.
Register it in ``ALL_RULES`` to plug it into ``evaluate``. Rules may
read any LCDA-derived field; they MUST defensively handle missing
data (most fields can be None / default during the first few seconds
of a game).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..lcda.objectives import ObjectiveTimer
    from ..lcda.source import LcdaSnapshot

# Thresholds — tunables. Pulled out so future re-tuning is one file.
DRAKE_PRIORITY_WINDOW_S = 30.0  # drake spawning within this is "soon"
BARON_PRIORITY_WINDOW_S = 45.0  # baron is more impactful → wider lead-up window
HERALD_LATE_GAME_S = 14 * 60.0  # herald despawns ~14:00; rule silent after
GOLD_LEAD_THRESHOLD = 3000      # absolute item-value diff that counts as "ahead"
GOLD_DEFICIT_THRESHOLD = 5000   # behind by this → play safe, don't force
LEVEL_GAP_THRESHOLD = 1.5       # avg-level diff that makes fighting bad
KILL_LEAD_THRESHOLD = 5         # kills ahead → real momentum, press it
KILL_DEFICIT_THRESHOLD = 7      # kills behind → real deficit, bunker
LATE_GAME_S = 30 * 60.0         # past 30:00 every fight is the last fight

# Window rule constants (pro-level window functions)
DRAKE_SETUP_WINDOW_S = 60.0     # start grouping this many seconds before drake
BARON_SETUP_WINDOW_S = 120.0    # baron needs more prep (vision sweep + waves)
FIGHT_SCORE_THRESHOLD = 0.30    # minimum |fight_score| to generate a fight rec

# --------------------------------------------------------------------------
# Champion focus-target database
# priority 1-5 (5 = kill first in a teamfight); tags = short warning strings;
# aoe_cc = True when the champion has a game-changing AoE CC that punishes
# clustering — generates "NICHT CLUSTERN" warnings.
# --------------------------------------------------------------------------
_CHAMP_DATA: dict[str, dict] = {
    # ---- ADC / Primary damage carries (kill first) ----
    "Jinx":         {"priority": 5, "tags": ["Hypercarry-Resets"], "aoe_cc": False},
    "Caitlyn":      {"priority": 5, "tags": ["Trap-CC"],           "aoe_cc": False},
    "Jhin":         {"priority": 5, "tags": ["Root-Grenade"],      "aoe_cc": False},
    "Miss Fortune": {"priority": 5, "tags": ["Bullet-Time-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Kog'Maw":      {"priority": 5, "tags": ["Hypercarry"],        "aoe_cc": False},
    "Samira":       {"priority": 5, "tags": ["CC-immune-Ult"],     "aoe_cc": False},
    "Draven":       {"priority": 5, "tags": ["Early-Burst"],       "aoe_cc": False},
    "Tristana":     {"priority": 5, "tags": ["Dive-Resets"],       "aoe_cc": False},
    "Twitch":       {"priority": 5, "tags": ["Invisible — Ward Pit!"], "aoe_cc": False},
    "Aphelios":     {"priority": 5, "tags": ["Moonlight-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Vayne":        {"priority": 4, "tags": ["Invisible-Tumble"],  "aoe_cc": False},
    "Ezreal":       {"priority": 4, "tags": ["Kiting"],            "aoe_cc": False},
    "Ashe":         {"priority": 4, "tags": ["Global-CC-Ult"],     "aoe_cc": False},
    "Zeri":         {"priority": 4, "tags": ["Stack-Hypercarry"],  "aoe_cc": False},
    "Xayah":        {"priority": 4, "tags": ["CC-immune-Ult"],     "aoe_cc": False},
    "Sivir":        {"priority": 4, "tags": ["Spell-Block-Shield"], "aoe_cc": False},
    # ---- AP Mid carries ----
    "Syndra":       {"priority": 5, "tags": ["One-Shot-Ult"],      "aoe_cc": False},
    "Zed":          {"priority": 5, "tags": ["Assassination"],     "aoe_cc": False},
    "LeBlanc":      {"priority": 5, "tags": ["Burst-Dash-Chain"],  "aoe_cc": False},
    "Akali":        {"priority": 5, "tags": ["Invisible"],         "aoe_cc": False},
    "Katarina":     {"priority": 5, "tags": ["Resets — CC=Counter!"], "aoe_cc": True},
    "Talon":        {"priority": 5, "tags": ["Roam-Burst"],        "aoe_cc": False},
    "Kha'Zix":      {"priority": 5, "tags": ["Isolation-Kill — NICHT ALLEIN!"], "aoe_cc": False},
    "Rengar":       {"priority": 5, "tags": ["One-Shot-Invisible — Ward Pit!"], "aoe_cc": False},
    "Evelynn":      {"priority": 5, "tags": ["Invisible nach 6 — Ward!"], "aoe_cc": False},
    "Orianna":      {"priority": 4, "tags": ["Ball-Shockwave — NICHT CLUSTERN!"], "aoe_cc": True},
    "Annie":        {"priority": 4, "tags": ["Tibbers-Stun — NICHT CLUSTERN!"], "aoe_cc": True},
    "Veigar":       {"priority": 4, "tags": ["Event-Horizon-Cage — NICHT REINGEHEN!"], "aoe_cc": True},
    "Viktor":       {"priority": 4, "tags": ["Gravity-Field — RAUS!"], "aoe_cc": True},
    "Lux":          {"priority": 4, "tags": ["Long-Range-CC"],     "aoe_cc": True},
    "Cassiopeia":   {"priority": 4, "tags": ["NICHT FRONTAL — Petrify-Ult!"], "aoe_cc": True},
    "Seraphine":    {"priority": 4, "tags": ["AoE-CC-Chain — NICHT CLUSTERN!"], "aoe_cc": True},
    "Xerath":       {"priority": 4, "tags": ["Long-Range-Snipe"],  "aoe_cc": True},
    "Ryze":         {"priority": 4, "tags": ["Hypercarry 3+ Items"], "aoe_cc": False},
    "Azir":         {"priority": 4, "tags": ["Soldiers-AoE"],      "aoe_cc": True},
    "Ekko":         {"priority": 4, "tags": ["AoE-Zone-Stun"],     "aoe_cc": True},
    "Shaco":        {"priority": 4, "tags": ["Clone — kill richtige Kopie!"], "aoe_cc": False},
    "Nidalee":      {"priority": 4, "tags": ["High-Poke-Dive"],    "aoe_cc": False},
    "Yasuo":        {"priority": 4, "tags": ["Wall-Engage", "CC-immune Ult"], "aoe_cc": False},
    "Yone":         {"priority": 4, "tags": ["AoE Ult — NICHT CLUSTERN!"],    "aoe_cc": True},
    "Zoe":          {"priority": 4, "tags": ["Sleep-CC"],          "aoe_cc": False},
    "Hwei":         {"priority": 4, "tags": ["AoE-CC-Chain"],      "aoe_cc": True},
    "Vex":          {"priority": 4, "tags": ["Global Ult-Reset"],  "aoe_cc": False},
    "Aurora":       {"priority": 4, "tags": ["Blink-Burst"],       "aoe_cc": False},
    "Malzahar":     {"priority": 4, "tags": ["Suppress Ult — kein Flash!"],    "aoe_cc": False},
    # ---- ADC (continued) ----
    "Kai'Sa":       {"priority": 5, "tags": ["Invisible Ult-Engage", "Hypercarry"], "aoe_cc": False},
    "Nilah":        {"priority": 5, "tags": ["AoE Ult — NICHT CLUSTERN!"],    "aoe_cc": True},
    "Smolder":      {"priority": 5, "tags": ["Hypercarry-late", "AoE Ult"],   "aoe_cc": True},
    # ---- Jungle ----
    "Lee Sin":      {"priority": 3, "tags": ["Kick-Displacement"], "aoe_cc": False},
    "Viego":        {"priority": 4, "tags": ["Possession — getöteter Champ übernommen!"], "aoe_cc": False},
    "Bel'Veth":     {"priority": 4, "tags": ["Hypercarry-late", "Unsterblich Ult"], "aoe_cc": False},
    "Vi":           {"priority": 3, "tags": ["Single-Target Ult"],  "aoe_cc": False},
    "Hecarim":      {"priority": 3, "tags": ["AoE Knockback — NICHT CLUSTERN!"], "aoe_cc": True},
    "Diana":        {"priority": 4, "tags": ["AoE Pull — NICHT CLUSTERN!"],    "aoe_cc": True},
    "Elise":        {"priority": 3, "tags": ["Burst-Cocoon CC"],   "aoe_cc": False},
    "Graves":       {"priority": 4, "tags": ["Burst-Dive"],        "aoe_cc": False},
    "Kindred":      {"priority": 4, "tags": ["Lamb's Respite — Unsterblichkeit Ult!"], "aoe_cc": False},
    "Master Yi":    {"priority": 5, "tags": ["CC = Counter!", "Hypercarry-resets"], "aoe_cc": False},
    "Xin Zhao":     {"priority": 3, "tags": ["AoE Ult-Knockback"], "aoe_cc": True},
    # ---- Top (continued) ----
    "Fiora":        {"priority": 3, "tags": ["True Damage Riposte"], "aoe_cc": False},
    "Riven":        {"priority": 3, "tags": ["Burst-Combo"],       "aoe_cc": False},
    "Tryndamere":   {"priority": 3, "tags": ["Unsterblich Ult — wartet ab!"],   "aoe_cc": False},
    "Camille":      {"priority": 3, "tags": ["Hextech-Ultimatum Isolation"],    "aoe_cc": False},
    "Gangplank":    {"priority": 3, "tags": ["Global Ult — NICHT CLUSTERN!"],  "aoe_cc": True},
    "Illaoi":       {"priority": 3, "tags": ["NICHT in Tentakeln fight!"],      "aoe_cc": True},
    "Irelia":       {"priority": 3, "tags": ["CC-Immune bei Stacks"],           "aoe_cc": False},
    "K'Sante":      {"priority": 2, "tags": ["AoE Ult-Displacement"],          "aoe_cc": True},
    "Maokai":       {"priority": 2, "tags": ["AoE — NICHT CLUSTERN!"],         "aoe_cc": True},
    # ---- Supports (continued) ----
    "Nami":         {"priority": 3, "tags": ["Tidal Wave — NICHT CLUSTERN!"],  "aoe_cc": True},
    "Brand":        {"priority": 4, "tags": ["AoE Blaze — NICHT CLUSTERN!"],   "aoe_cc": True},
    "Milio":        {"priority": 5, "tags": ["ZUERST TÖTEN — heilt + cleansed CC!"], "aoe_cc": False},
    "Renata Glasc": {"priority": 4, "tags": ["Hostile Takeover — NICHT CLUSTERN!", "Verbündete greifen an!"], "aoe_cc": True},
    "Bard":         {"priority": 3, "tags": ["Tempered Fate AoE Stasis — Ult beachten!"], "aoe_cc": True},
    "Pyke":         {"priority": 4, "tags": ["Execute-Reset", "Hook CC"],      "aoe_cc": False},
    # ---- Top tanks / fighters (lower kill priority) ----
    "Malphite":     {"priority": 2, "tags": ["Unstoppable-Force — NICHT CLUSTERN!"], "aoe_cc": True},
    "Amumu":        {"priority": 2, "tags": ["Sad-Mummy-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Sett":         {"priority": 3, "tags": ["Show-Stopper-Slam-AoE"], "aoe_cc": True},
    "Darius":       {"priority": 3, "tags": ["Execute-Resets!"],   "aoe_cc": False},
    "Garen":        {"priority": 3, "tags": ["True-Damage-Execute"], "aoe_cc": False},
    "Mordekaiser":  {"priority": 3, "tags": ["Death-Realm-Isolation"], "aoe_cc": False},
    "Nasus":        {"priority": 4, "tags": ["Hypercarry-Stacks — früh töten!"], "aoe_cc": True},
    "Ornn":         {"priority": 2, "tags": ["Stampede-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Jarvan IV":    {"priority": 2, "tags": ["Cataclysm-Arena — RAUS vor Ult!"], "aoe_cc": True},
    "Zac":          {"priority": 2, "tags": ["Bounce-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Cho'Gath":     {"priority": 2, "tags": ["Rupture-Knockup + Silence"], "aoe_cc": True},
    "Aatrox":       {"priority": 3, "tags": ["World-Ender-Revive beachten!"], "aoe_cc": True},
    "Volibear":     {"priority": 2, "tags": ["AoE-Thunderclap"],   "aoe_cc": True},
    "Renekton":     {"priority": 3, "tags": ["Dash-Stun-Combo"],   "aoe_cc": False},
    # ---- Supports ----
    "Soraka":       {"priority": 5, "tags": ["ZUERST TÖTEN — heilt alles!"], "aoe_cc": True},
    "Yuumi":        {"priority": 5, "tags": ["Host töten — Yuumi attached!"], "aoe_cc": False},
    "Sona":         {"priority": 5, "tags": ["Crescendo-AoE-Stun — NICHT CLUSTERN!"], "aoe_cc": True},
    "Leona":        {"priority": 2, "tags": ["Solar-Flare-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Nautilus":     {"priority": 2, "tags": ["Hook + Chain-CC"],   "aoe_cc": False},
    "Blitzcrank":   {"priority": 2, "tags": ["Hook = sofortiger Kill!"], "aoe_cc": False},
    "Thresh":       {"priority": 3, "tags": ["Hook-CC"],           "aoe_cc": False},
    "Morgana":      {"priority": 3, "tags": ["Soul-Shackles-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Lulu":         {"priority": 3, "tags": ["Wildgrowth-Hypercarry-Buff"], "aoe_cc": True},
    "Zyra":         {"priority": 3, "tags": ["Stranglethorns-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Janna":        {"priority": 3, "tags": ["Monsoon-Knockback — Engage-Interrupt!"], "aoe_cc": True},
    "Alistar":      {"priority": 2, "tags": ["Headbutt-Pulverize-Combo"], "aoe_cc": True},
    "Braum":        {"priority": 2, "tags": ["Glacial-Fissure-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    # ---- New champions ----
    "Mel":          {"priority": 4, "tags": ["Projektile NICHT einsetzen — Reflect!"], "aoe_cc": False},
    "Yunara":       {"priority": 4, "tags": ["Hypercarry-Scaling"],  "aoe_cc": False},
}

# Drake type localised names + strategic value (1=low, 5=high).
_DRAKE_DISPLAY: dict[str, str] = {
    "Fire":     "Infernal-Drache",
    "Earth":    "Berg-Drache",
    "Water":    "Ozean-Drache",
    "Air":      "Wolken-Drache",
    "Hextech":  "Hextech-Drache",
    "Chemtech": "Chemtech-Drache",
    "Elder":    "Elder-Drache",
}


@dataclass(frozen=True)
class Recommendation:
    """One actionable hint. Severity sorts these in the UI; category
    groups them so the user can see at a glance whether it's an
    attacking play, a safety call, or an objective decision.

    The confidence / risk / ttl_s fields were added in the v2 spec
    pass — they describe HOW SURE we are about the call, what the
    downside looks like, and how long the call stays valid. All
    three default to conservative values so legacy rules that didn't
    set them still produce sensible output."""
    text: str           # human-readable, German, short ("Drache forcen")
    severity: str       # "info" | "warn" | "alert"
    category: str       # "objective" | "tempo" | "safety" | "lane"
    # v2 additions — confidence band 0..1 (UI renders as a bar / glow),
    # risk band drives danger-coloring of the action, ttl_s tells the
    # UI how long this hint stays relevant before it should fade.
    confidence: float = 0.7    # default = "rule fired, moderate confidence"
    risk: str = "MEDIUM"       # "LOW" | "MEDIUM" | "HIGH"
    ttl_s: float = 15.0        # seconds before the hint becomes stale
    # Bulletpoints the InsightPanel renders when the user expands a
    # recommendation. Each entry is a short factual statement of WHY
    # the rule fired ("Drache in 25s", "Team-Gold-Diff +2400"). Empty
    # tuple is the safe default — legacy rules without explicit
    # reasons just don't expand.
    reasons: tuple[str, ...] = ()
    # Machine-readable tag used by _suppress_dominated() to de-duplicate
    # recommendations that cover the same signal. Default "" = no
    # suppression relationship. Never shown to the user.
    kind: str = ""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _team_gold_diff(snapshot: "LcdaSnapshot") -> int:
    """Allies items_value minus enemies items_value. Positive when
    we're ahead. None aggregates collapse to 0 — be defensive."""
    ally = getattr(snapshot, "ally_aggregate", None)
    enemy = getattr(snapshot, "enemy_aggregate", None)
    a = getattr(ally, "items_value", 0) if ally is not None else 0
    e = getattr(enemy, "items_value", 0) if enemy is not None else 0
    if not isinstance(a, (int, float)) or not isinstance(e, (int, float)):
        return 0
    return int(a) - int(e)


def _avg_level_diff(snapshot: "LcdaSnapshot") -> float:
    """Average ally level minus average enemy level. Positive when
    we're ahead. Empty teams return 0."""
    allies = list(getattr(snapshot, "allies", []) or [])
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not allies or not enemies:
        return 0.0
    a = sum(getattr(p, "level", 0) for p in allies) / len(allies)
    e = sum(getattr(p, "level", 0) for p in enemies) / len(enemies)
    return a - e


def fight_score(snapshot: "LcdaSnapshot | None") -> float:
    """Layer-2 scoring (v2 spec): weighted sum of advantage signals →
    a single 'how good is fighting right now' number in [-1.0..+1.0].

    Positive = we win this fight, negative = we lose. The mapping is
    intentionally calibrated against the existing thresholds:

      * gold_diff: ±5000 saturates one full point (≈ kill-spree gap)
      * level_diff: ±2.0 levels saturates (1 level ≈ 0.5 point)
      * kill_diff: ±10 saturates (large team-snowball)

    Pure function — no side effects. Used by future Layer-3 prediction
    helpers + as a confidence input for individual rules.
    """
    if snapshot is None:
        return 0.0
    gold = _team_gold_diff(snapshot)
    levels = _avg_level_diff(snapshot)
    kills = _team_kill_diff(snapshot)
    score = (
        max(-1.0, min(1.0, gold / 5000.0)) * 0.45
        + max(-1.0, min(1.0, levels / 2.0)) * 0.30
        + max(-1.0, min(1.0, kills / 10.0)) * 0.25
    )
    return max(-1.0, min(1.0, score))


def win_probability(snapshot: "LcdaSnapshot | None") -> float:
    """Layer-3 prediction (v2 spec): logistic-shape mapping of
    fight_score into a [0..1] win-probability estimate.

    Heuristic, not a trained model — fight_score is already bounded
    [-1..1] so the logistic just smooths it into a probability that
    the UI can render as a bar / percentage. Future swap-in of a real
    regression model is a single function-body change.
    """
    s = fight_score(snapshot)
    # Logistic with steepness 3 — roughly 0.95 at +1, 0.05 at -1.
    import math
    return 1.0 / (1.0 + math.exp(-3.0 * s))


def _objective_remaining(
    snapshot: "LcdaSnapshot", name: str,
) -> float | None:
    """Seconds until ``name`` respawns. None if not killed yet or not
    available in the snapshot."""
    for obj in getattr(snapshot, "objectives", []) or []:
        if getattr(obj, "name", "") == name:
            try:
                return obj.remaining(getattr(snapshot, "game_time", 0.0))
            except Exception:  # noqa: BLE001
                return None
    return None


def _player_ids(players: list) -> set[str]:
    """Return a set of both summoner_name and champion_name for each player.
    LCDA's KillerName in kill events uses whichever identifier the API version
    exposes — historically champion names, post-Riot-ID potentially summoner
    names. Indexing both avoids zero-count stacks across API formats."""
    ids: set[str] = set()
    for p in players:
        sn = getattr(p, "summoner_name", "") or ""
        cn = getattr(p, "champion_name", "") or ""
        if sn:
            ids.add(sn)
        if cn:
            ids.add(cn)
    return ids


def _drake_stack_count(snapshot: "LcdaSnapshot") -> int:
    """Count drakes the allied team has taken (from raw_events)."""
    events = getattr(snapshot, "raw_events", []) or []
    if not events:
        return 0
    allies = getattr(snapshot, "allies", []) or []
    if not allies:
        return 0
    ids = _player_ids(allies)
    return sum(
        1 for e in events
        if e.get("EventName") == "DragonKill" and e.get("KillerName") in ids
    )


def _enemy_drake_stack_count(snapshot: "LcdaSnapshot") -> int:
    """Count drakes the enemy team has taken (from raw_events)."""
    events = getattr(snapshot, "raw_events", []) or []
    if not events:
        return 0
    enemies = getattr(snapshot, "enemies", []) or []
    if not enemies:
        return 0
    ids = _player_ids(enemies)
    return sum(
        1 for e in events
        if e.get("EventName") == "DragonKill" and e.get("KillerName") in ids
    )


HERALD_USAGE_WINDOW_S = 180.0   # enemy has ~3 min to place the herald after pickup

_LANE_DISPLAY: dict[str, str] = {"L0": "Bot", "L1": "Mid", "L2": "Top"}


def _enemy_herald_pickup(snapshot: "LcdaSnapshot") -> tuple[float, float] | None:
    """Return (pickup_game_time, remaining_window_s) if enemy picked up herald
    and the 3-minute usage window hasn't expired, else None.

    Walks HeraldKill events to find the last one taken by an enemy player.
    Only returns a result within HERALD_USAGE_WINDOW_S seconds of pickup.
    """
    events = getattr(snapshot, "raw_events", []) or []
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not events or not enemies:
        return None
    enemy_ids = _player_ids(enemies)
    herald_kills = [e for e in events if e.get("EventName") == "HeraldKill"]
    if not herald_kills:
        return None
    last = herald_kills[-1]
    if (last.get("KillerName") or "") not in enemy_ids:
        return None
    pickup_t = float(last.get("EventTime") or 0.0)
    game_time = getattr(snapshot, "game_time", 0.0)
    remaining = HERALD_USAGE_WINDOW_S - (game_time - pickup_t)
    if remaining <= 0:
        return None
    return (pickup_t, remaining)


def _active_enemy_inhibitors_down(snapshot: "LcdaSnapshot") -> int:
    """Count enemy inhibitor buildings currently destroyed.

    Uses KillerName matching against ally ids to identify enemy inhibitor
    kills. Subtracts InhibitorRespawned events (no team on those, so we
    treat all respawns as restoring one enemy inhibitor — conservative and
    correct when only one team's inhibs are down).
    """
    events = getattr(snapshot, "raw_events", []) or []
    allies = list(getattr(snapshot, "allies", []) or [])
    if not events or not allies:
        return 0
    ally_ids = _player_ids(allies)
    killed = sum(
        1 for e in events
        if e.get("EventName") == "InhibitorKilled"
        and (e.get("KillerName") or "") in ally_ids
    )
    respawned = sum(
        1 for e in events if e.get("EventName") == "InhibitorRespawned"
    )
    return max(0, killed - respawned)


def _parse_turret_name(name: str) -> tuple[str, str, str] | None:
    """Parse LCDA turret name into (side, lane, tier), or None on bad format.

    Format: Turret_<Side>_<Lane>_<Tier>_...
      side: TOrder (blue) or TChaos (red)
      lane: L0=Bot, L1=Mid, L2=Top
      tier: P1=Outer, P2=Inner, P3=Inhibitor, P4=Nexus
    """
    parts = name.split("_")
    side = lane = tier = ""
    for p in parts:
        if p in ("TOrder", "TChaos"):
            side = p
        elif len(p) == 2 and p[0] == "L" and p[1].isdigit():
            lane = p
        elif len(p) == 2 and p[0] == "P" and p[1].isdigit():
            tier = p
    return (side, lane, tier) if (side and lane and tier) else None


def _enemy_turrets_down(
    snapshot: "LcdaSnapshot",
    tiers: tuple[str, ...] = ("P1", "P2"),
) -> dict[str, int]:
    """Count enemy turrets destroyed per lane (from raw_events).

    Returns {"Bot": n, "Mid": n, "Top": n}.
    ``tiers`` controls which tier codes to count:
      P1=Outer, P2=Inner, P3=Inhibitor, P4=Nexus
    Default counts only outer+inner; pass ("P1","P2","P3") for base exposure.
    Returns empty dict when team identity is unknown.
    """
    events = getattr(snapshot, "raw_events", []) or []
    active_team = (getattr(snapshot, "active_team", "") or "").upper()
    if not events or not active_team:
        return {}
    enemy_side = "TChaos" if active_team == "ORDER" else "TOrder"
    counts: dict[str, int] = {}
    for e in events:
        if e.get("EventName") != "TurretKilled":
            continue
        parsed = _parse_turret_name(e.get("TurretKilled", "") or "")
        if parsed is None:
            continue
        side, lane, tier = parsed
        if side != enemy_side or tier not in tiers:
            continue
        label = _LANE_DISPLAY.get(lane, lane)
        counts[label] = counts.get(label, 0) + 1
    return counts


def _fed_score(player: object, game_time: float) -> float:
    """Kills-per-5-minutes for ``player``. 1.0 = normal pace; 2.0+ = fed."""
    kills = getattr(player, "kills", 0)
    if not kills or game_time <= 0:
        return 0.0
    return kills / max(1.0, game_time / 60.0) * 5.0


def _kill_streak(player: object, events: list[dict]) -> int:
    """Consecutive kills by ``player`` since their last death (from raw_events).

    Walks events in reverse order. Stops counting when we find a
    ChampionKill where this player is the victim, or when all events
    are exhausted. Multi-kills (rapid kills in quick succession) count
    as individual kill-streak entries — this is intentional; a penta
    reads as streak=5.
    """
    ids: set[str] = set()
    sn = getattr(player, "summoner_name", "") or ""
    cn = getattr(player, "champion_name", "") or ""
    if sn:
        ids.add(sn)
    if cn:
        ids.add(cn)
    if not ids:
        return 0
    streak = 0
    for evt in reversed(events):
        if evt.get("EventName") != "ChampionKill":
            continue
        killer = evt.get("KillerName") or ""
        victim = evt.get("VictimName") or ""
        if killer in ids:
            streak += 1
        elif victim in ids:
            break  # they died — streak resets here
    return streak


def _focus_target(
    enemies: list,
    game_time: float,
    raw_events: list[dict] | None = None,
) -> tuple[str, str] | None:
    """Return (champion_name, reason) for the highest-priority alive enemy.

    Factors:
    - Base kill priority from _CHAMP_DATA
    - Fed-score (kills/time)
    - Kill streak from raw_events (boosts priority when on a spree)
    """
    if not enemies:
        return None
    events = raw_events or []

    def _score(p: object) -> float:
        if not getattr(p, "is_alive", True):
            return -1.0
        champ = getattr(p, "champion_name", "") or ""
        base = float(_CHAMP_DATA.get(champ, {}).get("priority", 3))
        fed = _fed_score(p, game_time)
        streak = _kill_streak(p, events)
        # Each kill on the current streak adds +1 to base priority (capped at 5+).
        streak_bonus = min(streak, 3) * 0.5
        return (base + streak_bonus) * max(1.0, fed)

    ranked = sorted(enemies, key=_score, reverse=True)
    best = ranked[0]
    champ = getattr(best, "champion_name", "") or ""
    if not champ:
        return None

    kills = getattr(best, "kills", 0)
    deaths = max(1, getattr(best, "deaths", 1) or 1)
    fed = _fed_score(best, game_time)
    streak = _kill_streak(best, events)
    priority = _CHAMP_DATA.get(champ, {}).get("priority", 3)

    parts: list[str] = []
    if streak >= 3:
        parts.append(f"KILLING SPREE {streak} kills — sofort töten!")
    elif streak == 2:
        parts.append(f"Double Kill — {kills}/{deaths} fed")
    elif fed >= 2.0:
        parts.append(f"{kills}/{deaths} — extrem fed")
    elif fed >= 1.3:
        parts.append(f"{kills}/{deaths} — fed")
    if priority >= 5 and not streak:
        parts.append("primäres Carry")
    return (champ, ", ".join(parts) if parts else "höchste Kill-Prio")


def _aoe_cc_warnings(enemies: list) -> list[str]:
    """One-line warning for each enemy whose AoE CC punishes clustering."""
    warnings: list[str] = []
    for p in enemies:
        champ = getattr(p, "champion_name", "") or ""
        data = _CHAMP_DATA.get(champ, {})
        if not data.get("aoe_cc"):
            continue
        tags = data.get("tags", [])
        tip = tags[0] if tags else "AoE CC"
        warnings.append(f"{champ} — {tip}")
    return warnings


def _alive_count(players: list, default_to_full_team: bool = True) -> int:
    """Count players currently on the map (respawn_timer == 0).
    Falls back to ``len(players)`` when respawn data isn't carried
    (older LCDA payloads / replayed fixtures) — alternative is to
    silently report everyone as dead, which would spam fight-avoidance
    recommendations during the early-game window."""
    if not players:
        return 0
    has_respawn = any(
        getattr(p, "respawn_timer", None) is not None for p in players
    )
    if not has_respawn and default_to_full_team:
        return len(players)
    return sum(1 for p in players if getattr(p, "is_alive", True))


def _team_kill_diff(snapshot: "LcdaSnapshot") -> int:
    """Allies' total kills minus enemies'. Positive when we're snowballing.
    Falls back to summing per-player kills when team aggregates are
    missing."""
    ally = getattr(snapshot, "ally_aggregate", None)
    enemy = getattr(snapshot, "enemy_aggregate", None)
    a = getattr(ally, "kills", None) if ally is not None else None
    e = getattr(enemy, "kills", None) if enemy is not None else None
    if a is None:
        a = sum(
            getattr(p, "kills", 0)
            for p in (getattr(snapshot, "allies", []) or [])
        )
    if e is None:
        e = sum(
            getattr(p, "kills", 0)
            for p in (getattr(snapshot, "enemies", []) or [])
        )
    if not isinstance(a, (int, float)) or not isinstance(e, (int, float)):
        return 0
    return int(a) - int(e)


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

def rule_drake_priority(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Drake spawning soon AND we have resources → contest it.

    "Resources" today: not significantly behind in gold OR ahead in
    levels. The full version would also check ult availability +
    summoner CDs; we don't have that signal.
    """
    remaining = _objective_remaining(snapshot, "Dragon")
    if remaining is None or remaining > DRAKE_PRIORITY_WINDOW_S:
        return None
    gold = _team_gold_diff(snapshot)
    levels = _avg_level_diff(snapshot)
    if gold < -GOLD_LEAD_THRESHOLD and levels < 0:
        # Resource-poor: don't force. The drake_give_up rule handles this.
        return None
    return Recommendation(
        text=f"Drache spawnt in {int(remaining)}s — Vision setzen, Side gruppieren",
        severity="alert",
        category="objective",
        confidence=0.85,
        risk="MEDIUM",
        ttl_s=remaining,
        reasons=(
            f"Drache spawnt in {int(remaining)}s",
            f"Team-Gold-Diff: {gold:+d}",
            f"Level-Diff: {levels:+.1f}",
        ),
    )


def rule_drake_give_up(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Drake up but we're significantly behind → don't contest, take
    side waves instead. Better to give up the objective than feed."""
    remaining = _objective_remaining(snapshot, "Dragon")
    if remaining is None or remaining > DRAKE_PRIORITY_WINDOW_S:
        return None
    gold = _team_gold_diff(snapshot)
    if gold > -GOLD_DEFICIT_THRESHOLD:
        return None  # not behind enough to skip
    return Recommendation(
        text=f"Drache ({int(remaining)}s) abgeben — Side-Wellen pushen, "
             f"Gold-Diff aufholen",
        severity="warn",
        category="objective",
        confidence=0.80,
        risk="HIGH",
        ttl_s=remaining,
        reasons=(
            f"Drache spawnt in {int(remaining)}s",
            f"Team-Gold-Diff: {gold:+d} (unter -{GOLD_DEFICIT_THRESHOLD})",
            "Contest = Risk vs Reward negativ",
        ),
    )


def rule_gold_lead_push(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Significant team gold lead → convert into map pressure (vision,
    plates, objectives) instead of just farming."""
    gold = _team_gold_diff(snapshot)
    if gold < GOLD_LEAD_THRESHOLD:
        return None
    return Recommendation(
        text=f"+{gold} Gold — Vision + Objective pushen",
        severity="info",
        category="tempo",
        confidence=0.75,
        risk="LOW",
        ttl_s=20.0,
        kind="gold_lead",
        reasons=(
            f"Team-Gold-Vorsprung: +{gold}",
            "Über Schwelle für aktiven Tempo-Push",
            "Nächstes Objective sollte priorisiert werden",
        ),
    )


def rule_far_behind_safe(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Significant deficit → safe play, wave clear, don't force fights."""
    gold = _team_gold_diff(snapshot)
    if gold > -GOLD_DEFICIT_THRESHOLD:
        return None
    return Recommendation(
        text=f"{gold} Gold — Safe spielen, Wellen abräumen, keine Fights",
        severity="warn",
        category="safety",
        confidence=0.80,
        risk="HIGH",
        ttl_s=30.0,
        reasons=(
            f"Team-Gold-Diff: {gold} (unter -{GOLD_DEFICIT_THRESHOLD})",
            "Fights statistisch verloren",
            "Wave-Clear sichert XP + Gold ohne Risiko",
        ),
    )


def rule_level_deficit(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Average level gap large enough that any fair fight loses."""
    diff = _avg_level_diff(snapshot)
    if diff > -LEVEL_GAP_THRESHOLD:
        return None
    return Recommendation(
        text=f"Level-Nachteil ({diff:+.1f}) — XP-Wellen sichern, "
             f"keine Skirmishes",
        severity="warn",
        category="safety",
        confidence=0.78,
        risk="HIGH",
        ttl_s=20.0,
        reasons=(
            f"Avg-Level-Diff: {diff:+.1f}",
            f"Schwelle: ±{LEVEL_GAP_THRESHOLD}",
            "Fair fights gehen verloren bei Level-Disparität",
        ),
    )


def rule_baron_priority(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Baron up soon AND we have resources → set up vision + group.
    Baron's window is wider than drake (45s vs 30s) because the prep
    matters more — wave clear, vision sweep, ult availability check."""
    remaining = _objective_remaining(snapshot, "Baron")
    if remaining is None or remaining > BARON_PRIORITY_WINDOW_S:
        return None
    gold = _team_gold_diff(snapshot)
    if gold < -GOLD_LEAD_THRESHOLD:
        return None  # baron_give_up handles the behind case
    return Recommendation(
        text=f"Baron in {int(remaining)}s — Vision-Pinks setzen, "
             f"Side-Wellen prep, Ults checken",
        severity="alert",
        category="objective",
        confidence=0.88,
        risk="MEDIUM",
        ttl_s=remaining,
        reasons=(
            f"Baron spawnt in {int(remaining)}s",
            f"Team-Gold-Diff: {gold:+d}",
            "Baron-Buff = Game-Winner — Setup-Phase kritisch",
        ),
    )


def rule_baron_give_up(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Baron up but we're significantly behind → don't contest. A
    Baron-throw at 8k behind is a 14-day vacation."""
    remaining = _objective_remaining(snapshot, "Baron")
    if remaining is None or remaining > BARON_PRIORITY_WINDOW_S:
        return None
    gold = _team_gold_diff(snapshot)
    if gold > -GOLD_DEFICIT_THRESHOLD:
        return None
    return Recommendation(
        text=f"Baron ({int(remaining)}s) abgeben — defensiv warten, "
             f"Konter-Engage suchen",
        severity="warn",
        category="objective",
        confidence=0.82,
        risk="HIGH",
        ttl_s=remaining,
        reasons=(
            f"Baron spawnt in {int(remaining)}s",
            f"Team-Gold-Diff: {gold:+d} (deutlich hinten)",
            "Baron-Throw = 14-Tage Vacation",
        ),
    )


def rule_herald_priority(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Herald is an early-game tower-plate engine. Rule only fires
    in the herald window (≤14:00) and when we're roughly even or
    ahead. No herald → silent."""
    game_time = getattr(snapshot, "game_time", 0.0)
    if game_time > HERALD_LATE_GAME_S:
        return None
    remaining = _objective_remaining(snapshot, "Herald")
    if remaining is None or remaining > DRAKE_PRIORITY_WINDOW_S:
        return None
    gold = _team_gold_diff(snapshot)
    if gold < -GOLD_LEAD_THRESHOLD:
        return None
    return Recommendation(
        text=f"Herald in {int(remaining)}s — top-side prio, "
             f"Plates abholen",
        severity="alert",
        category="objective",
        confidence=0.82,
        risk="LOW",
        ttl_s=remaining,
        reasons=(
            f"Herald spawnt in {int(remaining)}s",
            f"Game-Time: {int(game_time)}s (im Herald-Window)",
            "Herald → Plates = +400g pro Plate",
        ),
    )


def rule_kill_lead_snowball(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Substantial kill lead → press it. More aggressive vision +
    dive setups. The kill-diff signal is independent from items_value
    — you can be ahead in kills but behind in items if assists
    dominated, but the momentum is still real."""
    diff = _team_kill_diff(snapshot)
    if diff < KILL_LEAD_THRESHOLD:
        return None
    return Recommendation(
        text=f"+{diff} Kills — aggressiv Vision pushen",
        severity="info",
        category="tempo",
        confidence=0.78,
        risk="LOW",
        ttl_s=25.0,
        kind="kill_lead",
        reasons=(
            f"Team-Kill-Diff: +{diff}",
            "Momentum-Signal — Vision sollte aggressiv vorgeschoben werden",
            "Dive-Setups statt Lane-Farming",
        ),
    )


def rule_kill_deficit_defensive(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Substantial kill deficit → bunker. Don't extend, hold turret
    line, wait for a back-coordinated reset."""
    diff = _team_kill_diff(snapshot)
    if diff > -KILL_DEFICIT_THRESHOLD:
        return None
    return Recommendation(
        text=f"{diff} Kills — Bunker am Inhib, kein Überfarmen, "
             f"auf koordinierten Reset warten",
        severity="warn",
        category="safety",
        confidence=0.80,
        risk="HIGH",
        ttl_s=30.0,
        reasons=(
            f"Team-Kill-Diff: {diff}",
            "Skirmishes verlieren wir statistisch",
            "Defensive Position + koordinierter Back = nur Weg raus",
        ),
    )


def rule_numbers_disadvantage(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Allies dead while enemies are up → don't fight, don't extend.
    Highest-priority safety call — overrides drake/baron context.
    """
    allies = list(getattr(snapshot, "allies", []) or [])
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not allies or not enemies:
        return None  # team identity not established yet — can't compare
    allies_alive = _alive_count(allies)
    enemies_alive = _alive_count(enemies)
    if allies_alive >= enemies_alive:
        return None
    deficit = enemies_alive - allies_alive
    if deficit <= 0:
        return None
    return Recommendation(
        text=f"Wir {allies_alive}v{enemies_alive} — KEINE Fights bis Respawn",
        severity="alert",
        category="safety",
        confidence=0.92,
        risk="HIGH",
        ttl_s=8.0,
        kind="numbers_disadv",
        reasons=(
            f"Allies alive: {allies_alive}/5",
            f"Enemies alive: {enemies_alive}/5",
            "Numbers-Disadvantage — jeder Fight = sicherer Tod",
        ),
    )


def rule_numbers_advantage(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemies dead → push the temporary 5v4 / 5v3. The window is
    short (single death = ~30s), so the rec ttl matches that."""
    allies = list(getattr(snapshot, "allies", []) or [])
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not allies or not enemies:
        return None
    allies_alive = _alive_count(allies)
    enemies_alive = _alive_count(enemies)
    if enemies_alive >= allies_alive:
        return None
    advantage = allies_alive - enemies_alive
    if advantage <= 0:
        return None
    return Recommendation(
        text=f"{allies_alive}v{enemies_alive} — JETZT Pressure, Obj forcen!",
        severity="alert",
        category="tempo",
        confidence=0.90,
        risk="LOW",
        ttl_s=12.0,
        kind="numbers_adv",
        reasons=(
            f"Allies alive: {allies_alive}/5",
            f"Enemies alive: {enemies_alive}/5",
            "Window ist kurz — sofort ausnutzen",
        ),
    )


def rule_late_game_group(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Past 30:00 every teamfight decides the game. Splitpush is
    rarely worth the death timer; group as 5 around objectives."""
    game_time = getattr(snapshot, "game_time", 0.0)
    if game_time < LATE_GAME_S:
        return None
    return Recommendation(
        text="Late game — group 5, kein Splitpush ohne TP, "
             "jeder Death = 50s+",
        severity="info",
        category="tempo",
        confidence=0.85,
        risk="MEDIUM",
        ttl_s=60.0,
        reasons=(
            f"Game-Time: {int(game_time / 60)}min",
            "Death-Timer 50s+ — jeder Tod = verlorenes Objective",
            "Splitpush-Risk > Reward ohne TP-Insurance",
        ),
    )


# --------------------------------------------------------------------------
# Window rules — pro-level objective + fight decision trees
# --------------------------------------------------------------------------

def rule_dragon_window(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Pro-level Dragon call. Factors: timer, stack count + soul-point,
    drake type, dead-enemy free-window, gold/numbers. Replaces the
    simpler rule_drake_priority + rule_drake_give_up in ALL_RULES."""
    remaining = _objective_remaining(snapshot, "Dragon")
    if remaining is None or remaining > DRAKE_SETUP_WINDOW_S:
        return None

    game_time = getattr(snapshot, "game_time", 0.0)
    allies = list(getattr(snapshot, "allies", []) or [])
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not allies or not enemies:
        return None  # team identity not established; can't compute window quality
    allies_alive = _alive_count(allies)
    enemies_alive = _alive_count(enemies)
    numbers_diff = allies_alive - enemies_alive
    gold = _team_gold_diff(snapshot)

    ally_stacks = _drake_stack_count(snapshot)
    enemy_stacks = _enemy_drake_stack_count(snapshot)
    soul_point = ally_stacks >= 3          # taking this = OUR soul
    enemy_soul_point = enemy_stacks >= 3   # must deny their soul

    drake_obj = next(
        (o for o in (getattr(snapshot, "objectives", []) or [])
         if getattr(o, "name", "") == "Dragon"),
        None,
    )
    drake_name = _DRAKE_DISPLAY.get(
        getattr(drake_obj, "detail", None) or "", "Drache"
    )

    dead_enemies = [e for e in enemies if not getattr(e, "is_alive", True)]
    free_window = numbers_diff > 0 and len(dead_enemies) > 0

    # Hard give-up: significantly behind + no edge
    if gold < -GOLD_DEFICIT_THRESHOLD and numbers_diff <= 0 and not soul_point and not free_window:
        return Recommendation(
            text=f"Drache ({int(remaining)}s) abgeben — Side pushen",
            severity="warn",
            category="objective",
            confidence=0.83,
            risk="HIGH",
            ttl_s=remaining,
            kind="dragon_give",
            reasons=(
                f"Drache in {int(remaining)}s",
                f"Gold-Diff: {gold:+d} (unter -{GOLD_DEFICIT_THRESHOLD})",
                f"Numbers: {allies_alive}v{enemies_alive}",
                "Contest = negatives Expected Value",
            ),
        )

    # Free-take window: enemies dead, we have numbers advantage
    if free_window:
        dead_names = " + ".join(
            getattr(e, "champion_name", "?") for e in dead_enemies[:2]
        )
        return Recommendation(
            text=f"Drache JETZT {allies_alive}v{enemies_alive} — {dead_names} tot!",
            severity="alert",
            category="objective",
            confidence=0.95,
            risk="LOW",
            ttl_s=remaining,
            kind="dragon_free",
            reasons=(
                f"FREE TAKE — {dead_names} tot ({numbers_diff} man up)",
                f"{drake_name} spawnt in {int(remaining)}s",
                f"Stacks: Wir {ally_stacks} — Gegner {enemy_stacks}",
                f"Gold-Diff: {gold:+d}",
            ),
        )

    # Soul-point urgency
    if soul_point:
        return Recommendation(
            text=f"{drake_name} in {int(remaining)}s — SOUL POINT! JETZT gehen",
            severity="alert",
            category="objective",
            confidence=0.92,
            risk="MEDIUM",
            ttl_s=remaining,
            kind="dragon_take",
            reasons=(
                f"SOUL POINT — Wir bei {ally_stacks}/4 Stacks!",
                f"Gold-Diff: {gold:+d} | {allies_alive}v{enemies_alive} alive",
            ),
        )
    if enemy_soul_point:
        return Recommendation(
            text=f"Drache in {int(remaining)}s — Gegner-Soul STOPPEN!",
            severity="alert",
            category="objective",
            confidence=0.90,
            risk="HIGH",
            ttl_s=remaining,
            kind="dragon_take",
            reasons=(
                f"GEGNER Soul Point ({enemy_stacks}/4 Stacks) — VERHINDERN!",
                f"Gold-Diff: {gold:+d} | {allies_alive}v{enemies_alive} alive",
            ),
        )

    # Active fight window (≤30s) or setup phase
    active = remaining <= DRAKE_PRIORITY_WINDOW_S and gold >= -GOLD_LEAD_THRESHOLD
    severity = "alert" if active else "warn"
    confidence = 0.84 if active else 0.78
    stack_suffix = f" ({ally_stacks}/4)" if ally_stacks > 0 else ""
    action = "JETZT Vision + Group" if active else "Vision + Group starten"

    return Recommendation(
        text=f"{drake_name}{stack_suffix} in {int(remaining)}s — {action}",
        severity=severity,
        category="objective",
        confidence=confidence,
        risk="MEDIUM",
        ttl_s=remaining,
        kind="dragon_take",
        reasons=(
            f"{drake_name} spawnt in {int(remaining)}s",
            *(
                (f"Stacks: Wir {ally_stacks} — Gegner {enemy_stacks}",)
                if ally_stacks > 0 or enemy_stacks > 0 else ()
            ),
            f"Gold-Diff: {gold:+d} | {allies_alive}v{enemies_alive} alive",
        ),
    )


def rule_baron_window(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Pro-level Baron call. 120s setup window (vision + waves), 45s
    fight window. Higher stakes than Drake — one throw = potential GG.
    Replaces rule_baron_priority + rule_baron_give_up in ALL_RULES."""
    remaining = _objective_remaining(snapshot, "Baron")
    if remaining is None or remaining > BARON_SETUP_WINDOW_S:
        return None

    game_time = getattr(snapshot, "game_time", 0.0)
    allies = list(getattr(snapshot, "allies", []) or [])
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not allies or not enemies:
        return None
    allies_alive = _alive_count(allies)
    enemies_alive = _alive_count(enemies)
    numbers_diff = allies_alive - enemies_alive
    gold = _team_gold_diff(snapshot)
    levels = _avg_level_diff(snapshot)
    is_late = game_time >= LATE_GAME_S

    dead_enemies = [e for e in enemies if not getattr(e, "is_alive", True)]
    free_window = numbers_diff > 0 and len(dead_enemies) > 0

    # Hard no-go: behind + no edge
    if gold < -GOLD_DEFICIT_THRESHOLD and numbers_diff <= 0 and not free_window:
        return Recommendation(
            text=f"Baron ({int(remaining)}s) abgeben — Konter suchen",
            severity="warn",
            category="objective",
            confidence=0.85,
            risk="HIGH",
            ttl_s=remaining,
            kind="baron_give",
            reasons=(
                f"Baron in {int(remaining)}s",
                f"Gold-Diff: {gold:+d} (deutlich hinten)",
                f"Numbers: {allies_alive}v{enemies_alive}",
                "Baron-Throw = sofortiges GG",
            ),
        )

    # Free-take window: enemies dead, numbers advantage
    if free_window:
        dead_names = " + ".join(
            getattr(e, "champion_name", "?") for e in dead_enemies[:2]
        )
        confidence = min(0.97, 0.96 + (0.02 if is_late else 0.0))
        return Recommendation(
            text=f"Baron JETZT {allies_alive}v{enemies_alive} — {dead_names} tot!",
            severity="alert",
            category="objective",
            confidence=confidence,
            risk="LOW",
            ttl_s=remaining,
            kind="baron_free",
            reasons=(
                f"FREE BARON — {dead_names} tot ({numbers_diff} man up)",
                f"Baron spawnt in {int(remaining)}s",
                f"Gold-Diff: {gold:+d} | Level: {levels:+.1f}",
                *(("Late Game — Baron = potenzieller Game-Winner",) if is_late else ()),
            ),
        )

    # Active fight window (≤45s)
    if remaining <= BARON_PRIORITY_WINDOW_S:
        confidence = min(0.92, 0.88 + (0.03 if is_late else 0.0))
        context = f"+{gold}" if gold >= GOLD_LEAD_THRESHOLD else f"{allies_alive}v{enemies_alive}"
        return Recommendation(
            text=f"Baron in {int(remaining)}s ({context}) — Pit-Control forcen",
            severity="alert",
            category="objective",
            confidence=confidence,
            risk="MEDIUM",
            ttl_s=remaining,
            kind="baron_take",
            reasons=(
                f"Baron spawnt in {int(remaining)}s",
                f"Gold-Diff: {gold:+d} | Level: {levels:+.1f}",
                f"Numbers: {allies_alive}v{enemies_alive} alive",
                *(("Late Game — Baron-Buff = potenzieller Game-Winner",) if is_late else ()),
            ),
        )

    # Setup phase — vision + wave clear
    confidence = min(0.85, 0.78 + (0.05 if is_late else 0.0))
    if remaining <= 90:
        action = "Waves + Vision (Tri-Bush, River)"
    else:
        action = f"Waves claren, Pinks kaufen ({int(remaining)}s)"

    return Recommendation(
        text=f"Baron in {int(remaining)}s — {action}",
        severity="warn",
        category="objective",
        confidence=confidence,
        risk="MEDIUM",
        ttl_s=remaining,
        kind="baron_take",
        reasons=(
            f"Baron spawnt in {int(remaining)}s",
            f"Gold-Diff: {gold:+d} | Level: {levels:+.1f}",
            f"Numbers: {allies_alive}v{enemies_alive} alive",
            *(("Late Game — Setup ist kritisch",) if is_late else ()),
        ),
    )


def rule_fight_opportunity(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Pro-level fight recommendation. Fires on a clearly favorable OR
    clearly unfavorable fight score. Surfaces:
    - Overall fight-chance percentage
    - Focus target (champion to kill first + reason)
    - AoE CC warnings ("NICHT CLUSTERN")
    """
    allies = list(getattr(snapshot, "allies", []) or [])
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not allies or not enemies:
        return None

    game_time = getattr(snapshot, "game_time", 0.0)
    score = fight_score(snapshot)
    win_pct = int(win_probability(snapshot) * 100)
    allies_alive = _alive_count(allies)
    enemies_alive = _alive_count(enemies)
    numbers_diff = allies_alive - enemies_alive
    gold = _team_gold_diff(snapshot)

    # Only fire when there's a clear directional signal
    if -FIGHT_SCORE_THRESHOLD < score < FIGHT_SCORE_THRESHOLD:
        return None

    raw_events = list(getattr(snapshot, "raw_events", []) or [])
    focus = _focus_target(enemies, game_time, raw_events)
    aoe_warnings = _aoe_cc_warnings(enemies)[:2]

    reasons: list[str] = [
        f"Fight-Chance: {win_pct}% (Score {score:+.2f})",
        f"Numbers: {allies_alive}v{enemies_alive} alive",
        f"Gold-Diff: {gold:+d}",
    ]
    if focus:
        reasons.append(f"Fokus: {focus[0]} — {focus[1]}")
    for w in aoe_warnings:
        reasons.append(f"AoE-Warnung: {w}")

    if score >= FIGHT_SCORE_THRESHOLD:
        # Don't recommend engaging when we're down in numbers
        if numbers_diff < 0:
            return None
        severity = "alert" if score >= 0.55 or numbers_diff >= 2 else "warn"
        confidence = min(0.95, 0.60 + score * 0.35)
        risk = "LOW" if gold >= GOLD_LEAD_THRESHOLD else "MEDIUM"

        # Build natural-sounding main text: "Fight 74% — 5v3. Fokus Jinx. Nicht clustern (Ori)!"
        parts: list[str] = []
        if numbers_diff >= 1:
            parts.append(f"Fight {allies_alive}v{enemies_alive} ({win_pct}%)")
        else:
            parts.append(f"Fight JETZT ({win_pct}%)")
        if focus:
            parts.append(f"Fokus {focus[0]}")
        if aoe_warnings:
            # Extract champion name from "ChampName — Tag text"
            aoe_champ = aoe_warnings[0].split(" — ")[0]
            parts.append(f"Nicht clustern ({aoe_champ})!")

        return Recommendation(
            text=" — ".join(parts),
            severity=severity,
            category="tempo",
            confidence=confidence,
            risk=risk,
            ttl_s=15.0,
            kind="fight",
            reasons=tuple(reasons),
        )
    else:
        # Unfavorable fight
        confidence = min(0.90, 0.60 + abs(score) * 0.30)
        return Recommendation(
            text=f"Fights meiden ({win_pct}%) — farmen + Vision",
            severity="warn",
            category="safety",
            confidence=confidence,
            risk="HIGH",
            ttl_s=20.0,
            kind="fight_bad",
            reasons=tuple(reasons),
        )


def rule_enemy_base_exposed(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy inhibitor turret(s) down → base exposed, push for GG.

    Fires when at least one lane has all three outer/inner/inhib turrets
    fallen, meaning our minions are now pushing into the base and an inhib
    kill creates super-minions. Higher-priority than lane_open.
    """
    active_team = (getattr(snapshot, "active_team", "") or "")
    if not active_team:
        return None
    full_counts = _enemy_turrets_down(snapshot, tiers=("P1", "P2", "P3"))
    exposed = [lane for lane, n in full_counts.items() if n >= 3]
    if not exposed:
        return None
    lanes_str = " + ".join(sorted(exposed))
    gold = _team_gold_diff(snapshot)
    return Recommendation(
        text=f"{lanes_str}-Inhib offen — Basis-Angriff, GG forcen!",
        severity="alert",
        category="lane",
        confidence=0.88,
        risk="LOW",
        ttl_s=90.0,
        kind="base_exposed",
        reasons=(
            f"Enemy {lanes_str}: Outer + Inner + Inhib-Turm gefallen",
            "Inhib-Kill = Super-Minions dauerhafter Pressure",
            f"Gold-Diff: {gold:+d}",
        ),
    )


def rule_lane_pressure(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy outer or inner turrets down → push that lane for objectives.

    Fully open lane (both outer + inner fallen) signals an inhib threat
    and forces enemy rotations — use it to enable drake/baron vision.
    Partial open (only outer) is an info nudge to send waves.
    """
    active_team = (getattr(snapshot, "active_team", "") or "")
    if not active_team:
        return None
    turrets_down = _enemy_turrets_down(snapshot)
    if not turrets_down:
        return None
    fully_open = [lane for lane, n in turrets_down.items() if n >= 2]
    partially_open = [lane for lane, n in turrets_down.items() if n == 1]
    if fully_open:
        lanes_str = " + ".join(sorted(fully_open))
        return Recommendation(
            text=f"{lanes_str}-Lane offen bis Inhib — Pressure + Obj-Vision!",
            severity="warn",
            category="lane",
            confidence=0.82,
            risk="LOW",
            ttl_s=60.0,
            kind="lane_open",
            reasons=(
                f"Enemy {lanes_str}: Outer + Inner Tower fallen",
                "Inhib angreifbar — zwingt Rotationen",
                "Super-Minions nach Inhib = passiver Pressure",
            ),
        )
    if partially_open:
        lanes_str = " + ".join(sorted(partially_open))
        return Recommendation(
            text=f"{lanes_str}-Lane Outer down — Side-Waves pushen",
            severity="info",
            category="lane",
            confidence=0.74,
            risk="LOW",
            ttl_s=45.0,
            kind="lane_open",
            reasons=(
                f"Enemy {lanes_str}: Outer Tower fallen",
                "Side-Waves erzeugen Rotationsdruck",
            ),
        )
    return None


def rule_ace_detected(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """All 5 enemies dead simultaneously — game-winning window, push NOW."""
    enemies = list(getattr(snapshot, "enemies", []) or [])
    allies = list(getattr(snapshot, "allies", []) or [])
    if len(enemies) < 5 or not allies:
        return None
    enemies_alive = _alive_count(enemies)
    if enemies_alive > 0:
        return None
    allies_alive = _alive_count(allies)
    return Recommendation(
        text=f"ACE! Alle 5 Feinde tot — PUSHEN zum GG! ({allies_alive}v0)",
        severity="alert",
        category="tempo",
        confidence=0.98,
        risk="LOW",
        ttl_s=30.0,
        kind="ace",
        reasons=(
            "ACE — alle Gegner tot",
            f"Allies alive: {allies_alive}/5",
            "Pushe Inhib + Nexus-Türme sofort!",
        ),
    )


def rule_enemy_herald_danger(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy picked up Rift Herald → tower-push wave incoming.

    Only fires within HERALD_USAGE_WINDOW_S (3 min) of pickup so the warning
    auto-expires once the herald has almost certainly been placed.
    """
    pickup = _enemy_herald_pickup(snapshot)
    if pickup is None:
        return None
    _, remaining = pickup
    return Recommendation(
        text=f"Enemy Herald ({int(remaining)}s) — TOP-Push! Ward River!",
        severity="warn",
        category="lane",
        confidence=0.85,
        risk="MEDIUM",
        ttl_s=remaining,
        kind="enemy_herald",
        reasons=(
            "Gegner pickupte Rift Herald",
            f"Verbleibendes Fenster: ~{int(remaining)}s",
            "Herald → TOP/MID-Tower = frühe Gold-Plates",
            "Ward Top-River + Tri-Bush vor dem Push",
        ),
    )


def rule_enemy_inhibitor_down(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """One or more enemy inhibitor buildings are dead → Super-Minions active.

    Unlike base_exposed (inhibitor turret fallen), this fires only after the
    actual inhibitor structure is destroyed, signalling super-minion pressure
    is already live.
    """
    count = _active_enemy_inhibitors_down(snapshot)
    if count <= 0:
        return None
    return Recommendation(
        text=f"{count}x Feind-Inhib DOWN — Super-Minions spawnen! Nexus-Angriff!",
        severity="alert" if count >= 2 else "warn",
        category="lane",
        confidence=0.90,
        risk="LOW",
        ttl_s=90.0,
        kind="inhib_down",
        reasons=(
            f"{count} enemy inhibitor(s) zerstört",
            "Super-Minions = dauerhafter Minion-Pressure",
            "Pushe mit Welle für Nexus-Türme",
        ),
    )


def rule_game_ended(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Surface a final Win/Loss card when the GameEnd event is present.

    Shows ally drake count and final gold diff as context. Once this fires,
    _suppress_dominated drops all other recommendations — the game is over.
    """
    result = getattr(snapshot, "game_result", "") or ""
    if not result:
        return None
    ally_drakes = _drake_stack_count(snapshot)
    enemy_drakes = _enemy_drake_stack_count(snapshot)
    gold = _team_gold_diff(snapshot)
    drake_str = f"{ally_drakes}x Drake" if ally_drakes else "0 Drakes"
    if result == "Win":
        return Recommendation(
            text=f"SIEG! GG — {drake_str}, Gold {gold:+d}",
            severity="alert",
            category="tempo",
            confidence=1.0,
            risk="LOW",
            ttl_s=300.0,
            kind="game_end",
            reasons=(
                "VICTORY — Spiel gewonnen!",
                f"Ally Drakes: {ally_drakes} | Enemy Drakes: {enemy_drakes}",
                f"Final Gold-Diff: {gold:+d}",
            ),
        )
    return Recommendation(
        text=f"NIEDERLAGE — GG, nächstes Spiel ({drake_str})",
        severity="warn",
        category="safety",
        confidence=1.0,
        risk="LOW",
        ttl_s=300.0,
        kind="game_end",
        reasons=(
            "DEFEAT — Spiel verloren",
            f"Ally Drakes: {ally_drakes} | Enemy Drakes: {enemy_drakes}",
            f"Final Gold-Diff: {gold:+d}",
        ),
    )


# Rule registry — extend by appending a function. Order doesn't affect
# ``evaluate``'s output (caller sorts by severity).
ALL_RULES: tuple[Callable[["LcdaSnapshot"], Recommendation | None], ...] = (
    # Game-end summary — trumps everything when the match is over.
    rule_game_ended,
    # Ace detection — highest-priority window, overrides most other calls.
    rule_ace_detected,
    # Numbers-asymmetry — safety overrides objective calls.
    rule_numbers_disadvantage,
    rule_numbers_advantage,
    # Pro-level window rules (replace the simpler drake/baron 4-pack).
    rule_dragon_window,
    rule_baron_window,
    rule_herald_priority,
    rule_fight_opportunity,
    # General tempo + safety rules.
    rule_gold_lead_push,
    rule_far_behind_safe,
    rule_level_deficit,
    rule_kill_lead_snowball,
    rule_kill_deficit_defensive,
    rule_late_game_group,
    # Lane/base pressure — structural map-state (turrets + inhibs + herald).
    rule_enemy_herald_danger,
    rule_enemy_inhibitor_down,
    rule_enemy_base_exposed,
    rule_lane_pressure,
)


_SEVERITY_RANK = {"alert": 0, "warn": 1, "info": 2}


def _suppress_dominated(recs: list[Recommendation]) -> list[Recommendation]:
    """Remove recommendations made redundant by more specific ones.

    Suppression rules (applied in order):

    1. ace present → drop numbers_adv, fight, gold_lead, kill_lead (all
       subsumed by the ACE push signal). Keep safety and lane_open.

    2. numbers_disadv present → drop ALL offensive calls (fight, push,
       numbers_adv, and objective "take" recs). Keep give-up and safety.
       A teammate is dead — no aggressive rec should reach the user.

    3. dragon_free / baron_free already embeds the numbers-advantage
       signal in richer context → remove the standalone numbers_adv card.

    4. fight rec present → remove gold_lead and kill_lead (they are
       sub-signals of the same "you're ahead, press it" message).

    5. "don't fight" contradicts an active objective-take call;
       suppress fight_bad when we're already recommending taking an objective.
    """
    kinds = {r.kind for r in recs}

    # Rule 0 — game_end present: show only the result card, suppress everything
    if "game_end" in kinds:
        return [r for r in recs if r.kind == "game_end"]

    # Rule 1 — ace absorbs redundant offensive signals
    if "ace" in kinds:
        _ace_drop = {"fight", "fight_bad", "numbers_adv", "gold_lead", "kill_lead"}
        recs = [r for r in recs if r.kind not in _ace_drop]
        kinds = {r.kind for r in recs}

    # Rule 2 — safety first
    if "numbers_disadv" in kinds:
        _offensive = {"fight", "numbers_adv", "gold_lead", "kill_lead",
                      "dragon_take", "dragon_free", "baron_take", "baron_free"}
        return [r for r in recs if r.kind not in _offensive]

    # Rule 3 — free-window objective absorbs standalone numbers_adv
    if "dragon_free" in kinds or "baron_free" in kinds:
        recs = [r for r in recs if r.kind != "numbers_adv"]

    # Rule 4 — fight rec subsumes generic lead signals
    if "fight" in kinds:
        recs = [r for r in recs if r.kind not in {"gold_lead", "kill_lead"}]

    # Rule 5 — "don't fight" contradicts an active objective-take call;
    # suppress fight_bad when we're already recommending taking an objective.
    _obj_take = {"dragon_take", "dragon_free", "baron_take", "baron_free"}
    if kinds & _obj_take:
        recs = [r for r in recs if r.kind != "fight_bad"]

    # Rule 6 — base_exposed absorbs lane_open for the same lane context;
    # suppress generic lane_open cards when a base-exposure alert is present.
    if "base_exposed" in kinds:
        recs = [r for r in recs if r.kind != "lane_open"]

    # Rule 7 — inhib_down (building destroyed) supersedes base_exposed
    # (turret just fell). The state has advanced past base_exposed.
    if "inhib_down" in kinds:
        recs = [r for r in recs if r.kind not in {"base_exposed", "lane_open"}]

    return recs


def evaluate(
    snapshot: "LcdaSnapshot | None",
    *,
    rules: tuple = ALL_RULES,
) -> list[Recommendation]:
    """Run every rule against ``snapshot`` and return the non-None
    results sorted by severity (alerts first). Pure function — safe
    to call on the LCDA-snapshot tick without any state.

    None snapshot → empty list (pre-game window). Rules that raise
    are silently skipped — a buggy rule must not break the engine.
    """
    if snapshot is None:
        return []
    out: list[Recommendation] = []
    for rule in rules:
        try:
            rec = rule(snapshot)
        except Exception:  # noqa: BLE001 — engine never propagates rule bugs
            continue
        if rec is not None:
            out.append(rec)
    out.sort(key=lambda r: _SEVERITY_RANK.get(r.severity, 99))
    return _suppress_dominated(out)
