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
from typing import TYPE_CHECKING
from collections.abc import Callable

if TYPE_CHECKING:
    from ..lcda.source import LcdaSnapshot
    from ..lcda.spell_tracker import SpellTracker

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
FIGHT_WINDOW_CLOSING_S = 12.0   # warn when enemy respawns within this many seconds

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
    # ---- ADC (extended) ----
    "Lucian":       {"priority": 4, "tags": ["Mobility-Burst"],               "aoe_cc": False},
    "Varus":        {"priority": 4, "tags": ["Chain-of-Corruption-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Kalista":      {"priority": 4, "tags": ["Oathsworn-Ult", "Rend-Charges"], "aoe_cc": False},
    "Corki":        {"priority": 4, "tags": ["The-Package-Poke"],              "aoe_cc": False},
    # ---- Mid (extended) ----
    "Ahri":         {"priority": 4, "tags": ["Charm-CC", "Spirit-Rush-Triple-Dash"], "aoe_cc": False},
    "Twisted Fate": {"priority": 4, "tags": ["Gold-Card-Stun", "Destiny-Global-Ult"], "aoe_cc": False},
    "Fizz":         {"priority": 4, "tags": ["Shark-Ult-AoE — NICHT NÄHERN!"], "aoe_cc": True},
    "Lissandra":    {"priority": 3, "tags": ["AoE-Freeze — NICHT CLUSTERN!"],  "aoe_cc": True},
    "Aurelion Sol": {"priority": 4, "tags": ["Hypercarry-Stacks", "Singularity-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Anivia":       {"priority": 3, "tags": ["Flash-Frost-Stun", "Crystallize-Wall"], "aoe_cc": True},
    "Zilean":       {"priority": 3, "tags": ["Chronoshift-Revive beachten!", "Time-Bomb-AoE-Stun"], "aoe_cc": True},
    "Vel'Koz":      {"priority": 4, "tags": ["True-Damage-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Ziggs":        {"priority": 4, "tags": ["Mega-Inferno-Bomb-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Kassadin":     {"priority": 4, "tags": ["Riftwalk-Hypercarry-late"],       "aoe_cc": False},
    "Vladimir":     {"priority": 4, "tags": ["Hemoplague-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Neeko":        {"priority": 4, "tags": ["Clone — töte richtige!", "AoE-Root-Ult — NICHT CLUSTERN!"], "aoe_cc": True},
    "Qiyana":       {"priority": 4, "tags": ["Terrain-CC-Burst"],               "aoe_cc": True},
    "Sylas":        {"priority": 4, "tags": ["Stiehlt Ult — Ult beachten!"],   "aoe_cc": False},
    "Galio":        {"priority": 3, "tags": ["Hero-Entrance-AoE — NICHT CLUSTERN!", "Bulwark-Taunt"], "aoe_cc": True},
    "Swain":        {"priority": 4, "tags": ["Vision-AoE-Drain — NICHT CLUSTERN!"], "aoe_cc": True},
    "Taliyah":      {"priority": 4, "tags": ["Weaver's-Wall", "AoE-Rockslide — NICHT CLUSTERN!"], "aoe_cc": True},
    "Jayce":        {"priority": 3, "tags": ["Cannon-Poke", "Mercury-Hammer-Knockback"], "aoe_cc": False},
    "Pantheon":     {"priority": 3, "tags": ["AoE-Grand-Starfall — NICHT CLUSTERN!", "Shield-Stun"], "aoe_cc": True},
    "Rumble":       {"priority": 4, "tags": ["Equalizer-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Naafiri":      {"priority": 4, "tags": ["Pack-Hunt-Burst"],                "aoe_cc": False},
    # ---- Jungle (extended) ----
    "Kayn":         {"priority": 4, "tags": ["Stealth-Wall-Walk-Ult"],          "aoe_cc": False},
    "Nocturne":     {"priority": 4, "tags": ["Paranoia-Darkness-Ult — Ward!"],  "aoe_cc": False},
    "Sejuani":      {"priority": 2, "tags": ["Glacial-Prison-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Warwick":      {"priority": 3, "tags": ["Infinite-Duress-Suppress"],       "aoe_cc": False},
    "Nunu":         {"priority": 2, "tags": ["Absolute-Zero-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Fiddlesticks": {"priority": 4, "tags": ["Crowstorm-AoE — NICHT CLUSTERN!", "Terrify-CC"], "aoe_cc": True},
    "Udyr":         {"priority": 2, "tags": ["Bear-Dash-Stun", "AoE-Phoenix-Stun"], "aoe_cc": True},
    "Wukong":       {"priority": 3, "tags": ["Cyclone-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Rammus":       {"priority": 2, "tags": ["Tremors-AoE — NICHT CLUSTERN!", "Powerball-Knockup"], "aoe_cc": True},
    "Shyvana":      {"priority": 3, "tags": ["Dragon-Form-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Lillia":       {"priority": 3, "tags": ["Sleepy-Trouble-Ult-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Rek'Sai":      {"priority": 3, "tags": ["Void-Rush-Ult", "Tunnel-Network"], "aoe_cc": False},
    "Briar":        {"priority": 4, "tags": ["Hypercarry-late", "Head-Rush-Frenzy-AoE"], "aoe_cc": True},
    "Trundle":      {"priority": 2, "tags": ["Subjugate-Drain", "Pillar-CC"],  "aoe_cc": False},
    "Poppy":        {"priority": 2, "tags": ["Keeper's-Verdict-Knockback-AoE"], "aoe_cc": True},
    # ---- Top (extended) ----
    "Gnar":         {"priority": 3, "tags": ["GIGA-GNAR-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Urgot":        {"priority": 3, "tags": ["Fear-Beyond-Death-Execute"],      "aoe_cc": False},
    "Jax":          {"priority": 3, "tags": ["Counter-Strike-AoE — Kein AA gegen Jax!"], "aoe_cc": True},
    "Shen":         {"priority": 2, "tags": ["Stand-United-Global — Ult beachten!", "Shadow-Dash-Taunt"], "aoe_cc": False},
    "Kennen":       {"priority": 3, "tags": ["Slicing-Maelstrom-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Kayle":        {"priority": 4, "tags": ["Hypercarry nach Lvl 16", "Divine-Judgement-Invulnerable-AoE"], "aoe_cc": True},
    "Teemo":        {"priority": 3, "tags": ["Noxious-Trap-Shrooms — Ward Jungle!", "Blind-CC"], "aoe_cc": False},
    "Gwen":         {"priority": 3, "tags": ["Hallowed-Mist-Zone — KEIN ANGRIFF!"], "aoe_cc": False},
    "Olaf":         {"priority": 3, "tags": ["Ragnarok-CC-Immune Ult"],         "aoe_cc": False},
    "Sion":         {"priority": 2, "tags": ["Unstoppable-Onslaught — AUS DEM WEG!", "AoE-Smash-Stun"], "aoe_cc": True},
    "Tahm Kench":   {"priority": 2, "tags": ["Devour-Ally-Save", "An-Acquired-Taste"], "aoe_cc": False},
    "Heimerdinger": {"priority": 3, "tags": ["Turrets-AoE-Zone — NICHT DRÜCKEN!"], "aoe_cc": True},
    "Yorick":       {"priority": 3, "tags": ["Maiden-of-the-Mist-Bruiser"],    "aoe_cc": False},
    "Quinn":        {"priority": 3, "tags": ["Behind-Enemy-Lines-Mobility"],    "aoe_cc": False},
    "Singed":       {"priority": 2, "tags": ["Poison-Trail-AoE", "Fling-Knockback"], "aoe_cc": True},
    # ---- Support (extended) ----
    "Rakan":        {"priority": 2, "tags": ["Grand-Entrance-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Senna":        {"priority": 4, "tags": ["Absolution-Long-Range", "Global-Dawning-Shadow-AoE"], "aoe_cc": True},
    "Rell":         {"priority": 2, "tags": ["Magnet-Storm-AoE — NICHT CLUSTERN!"], "aoe_cc": True},
    "Karma":        {"priority": 3, "tags": ["Mantra-Inspire-Shield", "Spirit-Bond-Slow"], "aoe_cc": False},
    # ---- Remaining champions ----
    "Akshan":       {"priority": 4, "tags": ["Revive-Passive", "Grappling-Hook-Mobility"], "aoe_cc": False},
    "Ambessa":      {"priority": 4, "tags": ["Burst-Dash-Combo", "AoE-Ult-Fist — NICHT CLUSTERN!"], "aoe_cc": True},
    "Dr. Mundo":    {"priority": 2, "tags": ["Unsterblich-Ult — einfach warten!"],         "aoe_cc": False},
    "Gragas":       {"priority": 3, "tags": ["Explosive-Cask-AoE-Knockback — NICHT CLUSTERN!"], "aoe_cc": True},
    "Ivern":        {"priority": 2, "tags": ["Daisy-Tank", "Rootcaller-CC"],               "aoe_cc": False},
    "Karthus":      {"priority": 5, "tags": ["Requiem-Global-Ult — KEIN RECALL!", "AoE-Defile — NICHT CLUSTERN!"], "aoe_cc": True},
    "Kled":         {"priority": 3, "tags": ["Chaaaaarge!!!-Global-Ult", "Skaarl-Revive"], "aoe_cc": False},
    "Taric":        {"priority": 2, "tags": ["Cosmic-Radiance-Invulnerable — Ult beachten!", "Bastion-Chain-CC"], "aoe_cc": False},
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


def _ally_grub_count(snapshot: "LcdaSnapshot") -> int:
    """Count Void Grubs killed by the allied team (EventName='VoidGrub')."""
    events = getattr(snapshot, "raw_events", []) or []
    allies = getattr(snapshot, "allies", []) or []
    if not events or not allies:
        return 0
    ids = _player_ids(allies)
    return sum(
        1 for e in events
        if e.get("EventName") == "VoidGrub" and e.get("KillerName") in ids
    )


def _enemy_grub_count(snapshot: "LcdaSnapshot") -> int:
    """Count Void Grubs killed by the enemy team (EventName='VoidGrub')."""
    events = getattr(snapshot, "raw_events", []) or []
    enemies = getattr(snapshot, "enemies", []) or []
    if not events or not enemies:
        return 0
    ids = _player_ids(enemies)
    return sum(
        1 for e in events
        if e.get("EventName") == "VoidGrub" and e.get("KillerName") in ids
    )


def _is_jungler(player: object) -> bool:
    """Return True if the player is the jungler.

    Primary: LCDA position field == "JUNGLE" (available when LCDA exposes it).
    Fallback: Smite summoner spell (champion-neutral, every patch-stable).
    Both paths checked so older LCDA versions that omit position still work."""
    if (getattr(player, "position", "") or "").upper() == "JUNGLE":
        return True
    for attr in ("spell_one", "spell_two"):
        spell = getattr(player, attr, None)
        if spell is not None and getattr(spell, "name", "") == "Smite":
            return True
    return False


HERALD_USAGE_WINDOW_S = 180.0   # enemy has ~3 min to place the herald after pickup
# Flash is 300s base; alert only when it won't be back imminently.
FLASH_DOWN_ALERT_S = 60.0
# Teleport alert: wider window than Flash since TP blocks global rotations.
TP_DOWN_ALERT_S = 90.0
# Combat spells (Exhaust, Heal, Ignite, Barrier, Cleanse): alert when ≥60s CD.
COMBAT_SPELL_ALERT_S = 60.0
# Spells handled by rule_enemy_combat_spell_down (not Flash/TP — those have
# their own rules with spell-specific text).
_COMBAT_SPELLS: frozenset[str] = frozenset({
    "Exhaust", "Heal", "Ignite", "Barrier", "Cleanse",
})
# Enemy inhibitor respawns 300s after being killed; alert in final 60s.
INHIB_RESPAWN_S = 300.0
INHIB_EXPIRY_ALERT_S = 60.0
# Baron buff (Hand of Baron) lasts 180s; alert in final 60s to push NOW.
BARON_BUFF_DURATION_S = 180.0
BARON_BUFF_EXPIRY_ALERT_S = 60.0
# Elder Drake buff lasts 150s; alert in final 60s.
ELDER_BUFF_DURATION_S = 150.0
ELDER_BUFF_EXPIRY_ALERT_S = 60.0
# Ally turret lost — fire a defensive alert for this many seconds after the kill.
ALLY_TURRET_ALERT_WINDOW_S = 60.0
# Enemy soul-point persistent reminder: hand off to dragon_window when dragon
# spawns within this many seconds (dragon_window has more specific messaging).
ENEMY_SOUL_POINT_HANDOFF_S = 120.0
# Ally inhib respawning — fire info this many seconds before the respawn.
ALLY_INHIB_RESPAWN_ALERT_S = 60.0
# Dragon Soul reminder fires for this many seconds after the 4th drake is secured.
DRAGON_SOUL_SIGNAL_S = 120.0
# Void Grubs — pre-Herald early objective (Season 14+).
# Event name in LCDA is "VoidGrub" (not "VoidGrubKill").
VOID_GRUB_WINDOW_START_S = 270.0   # 4:30 — warn slightly before first spawn
VOID_GRUB_WINDOW_END_S = 840.0    # 14:00 — Herald replaces grubs at this point
VOID_GRUB_HORNGUARD = 3            # kills required for Hornguard buff
# Enemy jungler down — only fire when respawn is at least this far out so
# short sub-second flickers don't spam the panel.
JUNGLER_DOWN_MIN_S = 5.0
# Objective "about to spawn" window used by jungler_down to upgrade severity.
JUNGLER_DOWN_OBJ_WINDOW_S = 60.0

_LANE_DISPLAY: dict[str, str] = {"L0": "Bot", "L1": "Mid", "L2": "Top"}


def _herald_pickup(
    snapshot: "LcdaSnapshot", *, team: str
) -> tuple[float, float] | None:
    """Return (pickup_game_time, remaining_window_s) if the given team picked
    up Rift Herald and the 3-minute placement window hasn't expired.

    ``team`` is "ally" or "enemy". Walks HeraldKill events to find the last
    one taken by that team and returns remaining seconds within the 180s window.
    """
    events = getattr(snapshot, "raw_events", []) or []
    players = list(
        getattr(snapshot, "allies" if team == "ally" else "enemies", []) or []
    )
    if not events or not players:
        return None
    ids = _player_ids(players)
    herald_kills = [e for e in events if e.get("EventName") == "HeraldKill"]
    if not herald_kills:
        return None
    last = herald_kills[-1]
    if (last.get("KillerName") or "") not in ids:
        return None
    pickup_t = float(last.get("EventTime") or 0.0)
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    remaining = HERALD_USAGE_WINDOW_S - (game_time - pickup_t)
    return (pickup_t, remaining) if remaining > 0 else None


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


def _earliest_enemy_inhib_respawn_remaining(snapshot: "LcdaSnapshot") -> float | None:
    """Seconds until the next enemy inhibitor respawns, or None if none active.

    Each InhibitorKilled event taken by an ally produces an active
    inhib-down that respawns INHIB_RESPAWN_S (300s) later. Pairs kills
    with InhibitorRespawned events oldest-first (FIFO — the first killed
    is the first to respawn). Returns the smallest positive remaining
    time, or None when no active kill is found.
    """
    events = getattr(snapshot, "raw_events", []) or []
    allies = list(getattr(snapshot, "allies", []) or [])
    if not events or not allies:
        return None
    ally_ids = _player_ids(allies)
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)

    kill_times = sorted(
        float(e.get("EventTime") or 0.0)
        for e in events
        if e.get("EventName") == "InhibitorKilled"
        and (e.get("KillerName") or "") in ally_ids
    )
    if not kill_times:
        return None

    respawn_count = sum(1 for e in events if e.get("EventName") == "InhibitorRespawned")
    active_kills = kill_times[respawn_count:]  # oldest are matched to respawns first
    if not active_kills:
        return None

    remaining_times = [
        INHIB_RESPAWN_S - (game_time - t)
        for t in active_kills
    ]
    positive = [r for r in remaining_times if r > 0]
    return min(positive) if positive else None


def _active_ally_inhibitors_down(snapshot: "LcdaSnapshot") -> int:
    """Count OUR inhibitor buildings currently destroyed (enemy killed them).

    Mirror of _active_enemy_inhibitors_down: looks for InhibitorKilled events
    where the KillerName is in the enemy team — meaning the enemy pushed far
    enough to destroy our inhibitor. Conservative: subtracts all respawns.
    """
    events = getattr(snapshot, "raw_events", []) or []
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not events or not enemies:
        return 0
    enemy_ids = _player_ids(enemies)
    killed = sum(
        1 for e in events
        if e.get("EventName") == "InhibitorKilled"
        and (e.get("KillerName") or "") in enemy_ids
    )
    respawned = sum(
        1 for e in events if e.get("EventName") == "InhibitorRespawned"
    )
    return max(0, killed - respawned)


def _earliest_ally_inhib_respawn_remaining(snapshot: "LcdaSnapshot") -> float | None:
    """Seconds until the next ally inhibitor respawns, or None.

    Mirrors _earliest_enemy_inhib_respawn_remaining but looks for
    InhibitorKilled events taken by an ENEMY player (they destroyed our
    inhibitor). FIFO pairing with InhibitorRespawned events finds the
    soonest active respawn."""
    events = getattr(snapshot, "raw_events", []) or []
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not events or not enemies:
        return None
    enemy_ids = _player_ids(enemies)
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    kill_times = sorted(
        float(e.get("EventTime") or 0.0)
        for e in events
        if e.get("EventName") == "InhibitorKilled"
        and (e.get("KillerName") or "") in enemy_ids
    )
    if not kill_times:
        return None
    respawn_count = sum(1 for e in events if e.get("EventName") == "InhibitorRespawned")
    active_kills = kill_times[respawn_count:]
    if not active_kills:
        return None
    remaining_times = [INHIB_RESPAWN_S - (game_time - t) for t in active_kills]
    positive = [r for r in remaining_times if r > 0]
    return min(positive) if positive else None


def _ally_baron_buff_remaining(snapshot: "LcdaSnapshot") -> float | None:
    """Seconds left on the ally Hand-of-Baron buff, or None if not active.

    Finds the last BaronKill event taken by an ally and computes how much
    of the 180s buff window remains. Returns None when no ally Baron kill
    is recorded or the buff has already expired.
    """
    events = getattr(snapshot, "raw_events", []) or []
    allies = list(getattr(snapshot, "allies", []) or [])
    if not events or not allies:
        return None
    ally_ids = _player_ids(allies)
    baron_kills = [
        e for e in events
        if e.get("EventName") == "BaronKill"
        and (e.get("KillerName") or "") in ally_ids
    ]
    if not baron_kills:
        return None
    last = baron_kills[-1]
    kill_time = float(last.get("EventTime") or 0.0)
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    remaining = BARON_BUFF_DURATION_S - (game_time - kill_time)
    return remaining if remaining > 0 else None


def _enemy_baron_buff_remaining(snapshot: "LcdaSnapshot") -> float | None:
    """Seconds left on the enemy Hand-of-Baron buff, or None if not active.

    Mirrors _ally_baron_buff_remaining but looks for BaronKill events
    taken by enemy players — meaning we need to defend our base.
    """
    events = getattr(snapshot, "raw_events", []) or []
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not events or not enemies:
        return None
    enemy_ids = _player_ids(enemies)
    baron_kills = [
        e for e in events
        if e.get("EventName") == "BaronKill"
        and (e.get("KillerName") or "") in enemy_ids
    ]
    if not baron_kills:
        return None
    last = baron_kills[-1]
    kill_time = float(last.get("EventTime") or 0.0)
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    remaining = BARON_BUFF_DURATION_S - (game_time - kill_time)
    return remaining if remaining > 0 else None


def _ally_elder_buff_remaining(snapshot: "LcdaSnapshot") -> float | None:
    """Seconds left on the ally Elder Drake buff, or None if not active.

    Looks for DragonKill events with DragonType "Elder" taken by an ally.
    The buff lasts ELDER_BUFF_DURATION_S (150s). LCDA uses either
    'DragonType' or 'TrapType' depending on the API version — we check
    both defensively.
    """
    events = getattr(snapshot, "raw_events", []) or []
    allies = list(getattr(snapshot, "allies", []) or [])
    if not events or not allies:
        return None
    ally_ids = _player_ids(allies)
    elder_kills = [
        e for e in events
        if e.get("EventName") == "DragonKill"
        and (e.get("KillerName") or "") in ally_ids
        and (e.get("DragonType") or e.get("TrapType") or "").lower() == "elder"
    ]
    if not elder_kills:
        return None
    last = elder_kills[-1]
    kill_time = float(last.get("EventTime") or 0.0)
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    remaining = ELDER_BUFF_DURATION_S - (game_time - kill_time)
    return remaining if remaining > 0 else None


def _enemy_elder_buff_remaining(snapshot: "LcdaSnapshot") -> float | None:
    """Seconds left on the enemy Elder Drake buff, or None if not active.

    Mirrors _ally_elder_buff_remaining but looks for DragonKill events
    taken by enemy players. Buff lasts ELDER_BUFF_DURATION_S (150s)."""
    events = getattr(snapshot, "raw_events", []) or []
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not events or not enemies:
        return None
    enemy_ids = _player_ids(enemies)
    elder_kills = [
        e for e in events
        if e.get("EventName") == "DragonKill"
        and (e.get("KillerName") or "") in enemy_ids
        and (e.get("DragonType") or e.get("TrapType") or "").lower() == "elder"
    ]
    if not elder_kills:
        return None
    last = elder_kills[-1]
    kill_time = float(last.get("EventTime") or 0.0)
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    remaining = ELDER_BUFF_DURATION_S - (game_time - kill_time)
    return remaining if remaining > 0 else None


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


def _recent_ally_turret_losses(
    snapshot: "LcdaSnapshot",
) -> list[tuple[str, str, str, float]]:
    """Return (lane_label, tier, side, event_time) for ally turrets destroyed
    within ALLY_TURRET_ALERT_WINDOW_S of current game_time.

    Returns [] when team identity is unknown or no recent kills exist."""
    events = getattr(snapshot, "raw_events", []) or []
    active_team = (getattr(snapshot, "active_team", "") or "").upper()
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    if not events or not active_team:
        return []
    ally_side = "TOrder" if active_team == "ORDER" else "TChaos"
    result: list[tuple[str, str, str, float]] = []
    for e in events:
        if e.get("EventName") != "TurretKilled":
            continue
        evt_time = float(e.get("EventTime") or 0.0)
        if game_time - evt_time > ALLY_TURRET_ALERT_WINDOW_S:
            continue
        parsed = _parse_turret_name(e.get("TurretKilled", "") or "")
        if parsed is None:
            continue
        side, lane, tier = parsed
        if side != ally_side:
            continue
        label = _LANE_DISPLAY.get(lane, lane)
        result.append((label, tier, side, evt_time))
    return result


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


# ---------------------------------------------------------------------------
# CS deficit and lane-level-advantage rules
# ---------------------------------------------------------------------------

# Minimum game-time before CS efficiency has enough data to be meaningful.
CS_MIN_GAME_TIME_S = 240.0         # 4 min
# Suppress in late game where grouping unavoidably drops CS/min.
CS_LATE_SUPPRESS_S = 1680.0        # 28 min
# Target farm rate for lane players (emerald+ average).
CS_EXPECTED_PER_MIN = 8.0
# How far below expected before we fire.
CS_INFO_DEFICIT = 2.0              # info  (< 6.0/min when expected 8.0)
CS_WARN_DEFICIT = 3.5              # warn  (< 4.5/min when expected 8.0)
# Long TTL so the rule fires once per ~2 ticks, not every 2 s tick.
CS_DEFICIT_TTL_S = 30.0
# Positions exempt from CS checks.
_NON_CS_POSITIONS: frozenset[str] = frozenset({"UTILITY", "JUNGLE"})

# Lane-level advantage thresholds (laning phase only).
LANE_LEVEL_ADV_THRESHOLD = 2      # 2-level edge = real advantage
LANE_LEVEL_DOM_THRESHOLD = 3      # 3-level edge = dominance
LANE_PHASE_CUTOFF_S = 1200.0      # 20 min


def _active_player(snapshot: "LcdaSnapshot") -> object | None:
    """Return the active player's LivePlayer record from the allies list."""
    name = getattr(snapshot, "active_summoner", "") or ""
    if not name:
        return None
    for p in (getattr(snapshot, "allies", []) or []):
        if getattr(p, "summoner_name", "") == name:
            return p
    return None


def rule_cs_deficit(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Warn when the active laner's CS/min is significantly below the
    expected farming rate (≈8 CS/min for lane roles). Excludes supports
    and junglers who have different gold income paths. Suppressed before
    4 min (not enough waves) and after 28 min (grouping reduces farm).
    """
    game_time = getattr(snapshot, "game_time", 0.0)
    if game_time < CS_MIN_GAME_TIME_S or game_time >= CS_LATE_SUPPRESS_S:
        return None

    player = _active_player(snapshot)
    if player is None:
        return None

    position = (getattr(player, "position", "") or "").upper()
    if position in _NON_CS_POSITIONS:
        return None

    cs = getattr(player, "creep_score", 0)
    if not isinstance(cs, int) or cs < 0:
        return None

    cs_per_min = cs / (game_time / 60.0)
    deficit = CS_EXPECTED_PER_MIN - cs_per_min
    if deficit < CS_INFO_DEFICIT:
        return None

    severity = "warn" if deficit >= CS_WARN_DEFICIT else "info"
    min_elapsed = int(game_time / 60)
    expected_cs = int(CS_EXPECTED_PER_MIN * min_elapsed)

    return Recommendation(
        text=f"CS {cs} ({cs_per_min:.1f}/min, Ziel {CS_EXPECTED_PER_MIN:.0f}/min) — farme Wellen",
        severity=severity,
        category="lane",
        confidence=0.75,
        risk="LOW",
        ttl_s=CS_DEFICIT_TTL_S,
        kind="cs_deficit",
        reasons=(
            f"{cs_per_min:.1f} CS/min (Ziel: {CS_EXPECTED_PER_MIN:.0f}/min)",
            f"Minute {min_elapsed}: {cs} CS, Soll ~{expected_cs}",
        ),
    )


def rule_lane_level_advantage(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Surface a meaningful level edge in the active player's lane matchup.

    Level 2+ lead over the lane opponent is a reliable all-in / trade
    window that many players miss. Level 2+ deficit is an additional
    safety reminder on top of the team-average rule_level_deficit.
    Only fires during the laning phase (< 20 min) and only when LCDA
    exposes position data for both players so the matchup is unambiguous.
    """
    game_time = getattr(snapshot, "game_time", 0.0)
    if game_time >= LANE_PHASE_CUTOFF_S:
        return None

    player = _active_player(snapshot)
    if player is None:
        return None

    position = (getattr(player, "position", "") or "").upper()
    if not position or position in _NON_CS_POSITIONS:
        return None

    # Find the enemy at the same position — LCDA sets position for all 10.
    enemies = list(getattr(snapshot, "enemies", []) or [])
    lane_opp = next(
        (e for e in enemies if (getattr(e, "position", "") or "").upper() == position),
        None,
    )
    if lane_opp is None:
        return None

    my_level = int(getattr(player, "level", 0) or 0)
    opp_level = int(getattr(lane_opp, "level", 0) or 0)
    diff = my_level - opp_level

    if abs(diff) < LANE_LEVEL_ADV_THRESHOLD:
        return None

    opp_name = getattr(lane_opp, "champion_name", "") or "Gegner"

    if diff >= LANE_LEVEL_DOM_THRESHOLD:
        return Recommendation(
            text=f"Level-Dominanz +{diff} vs {opp_name} — All-in erzwingen",
            severity="warn",
            category="lane",
            confidence=0.82,
            risk="LOW",
            ttl_s=25.0,
            kind="lane_level_adv",
            reasons=(
                f"Du: Level {my_level}, {opp_name}: Level {opp_level}",
                f"+{diff} Level = statistisch gewonnener All-in",
            ),
        )
    if diff >= LANE_LEVEL_ADV_THRESHOLD:
        return Recommendation(
            text=f"Level-Vorteil +{diff} vs {opp_name} — Trade-Fenster",
            severity="info",
            category="lane",
            confidence=0.75,
            risk="LOW",
            ttl_s=20.0,
            kind="lane_level_adv",
            reasons=(
                f"Du: Level {my_level}, {opp_name}: Level {opp_level}",
                "Level-Edge: Trade erzwingen oder Turm-Plates nehmen",
            ),
        )
    # diff <= -LANE_LEVEL_ADV_THRESHOLD → enemy level lead
    return Recommendation(
        text=f"Level-Nachteil {diff} vs {opp_name} — Safe farmen",
        severity="warn",
        category="lane",
        confidence=0.80,
        risk="HIGH",
        ttl_s=20.0,
        kind="lane_level_disadv",
        reasons=(
            f"Du: Level {my_level}, {opp_name}: Level {opp_level}",
            f"{diff} Level = {opp_name} gewinnt jeden Trade",
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
    simpler rule_drake_priority + rule_drake_give_up in ALL_RULES.
    Elder Dragon is handled by rule_elder_window — deferred here."""
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
    # Elder gets its own dedicated rule with higher-urgency messaging.
    if getattr(drake_obj, "detail", None) == "Elder":
        return None

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


def rule_elder_window(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Elder Dragon — highest-stakes drake. Fires only when Elder is the
    active spawn. Combined with Dragon Soul it is essentially a GG button;
    must always be contested or seized.

    Fires with a wider setup window (120s, same as Baron) because Elder
    vision / wave-clear preparation takes longer than regular drakes."""
    remaining = _objective_remaining(snapshot, "Dragon")
    if remaining is None or remaining > BARON_SETUP_WINDOW_S:
        return None

    drake_obj = next(
        (o for o in (getattr(snapshot, "objectives", []) or [])
         if getattr(o, "name", "") == "Dragon"),
        None,
    )
    if getattr(drake_obj, "detail", None) != "Elder":
        return None  # not Elder — handled by rule_dragon_window

    allies = list(getattr(snapshot, "allies", []) or [])
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not allies or not enemies:
        return None
    allies_alive = _alive_count(allies)
    enemies_alive = _alive_count(enemies)
    numbers_diff = allies_alive - enemies_alive
    gold = _team_gold_diff(snapshot)

    ally_stacks = _drake_stack_count(snapshot)
    enemy_stacks = _enemy_drake_stack_count(snapshot)
    ally_has_soul = ally_stacks >= 4
    enemy_has_soul = enemy_stacks >= 4

    dead_enemies = [e for e in enemies if not getattr(e, "is_alive", True)]
    free_window = numbers_diff > 0 and len(dead_enemies) > 0

    # Free-take: numbers advantage + dead enemies
    if free_window:
        dead_names = " + ".join(
            getattr(e, "champion_name", "?") for e in dead_enemies[:2]
        )
        soul_suffix = " + Soul = GG!" if ally_has_soul else ""
        return Recommendation(
            text=f"Elder JETZT nehmen{soul_suffix} — {dead_names} tot!",
            severity="alert",
            category="objective",
            confidence=0.97,
            risk="LOW",
            ttl_s=remaining,
            kind="elder_take",
            reasons=(
                f"FREE ELDER — {dead_names} tot ({numbers_diff} man up)",
                f"Elder in {int(remaining)}s",
                *(("Wir haben Dragon Soul — Elder + Soul = GG!",) if ally_has_soul else ()),
                f"Gold-Diff: {gold:+d} | {allies_alive}v{enemies_alive} alive",
            ),
        )

    # Ally has Dragon Soul → Elder closes the game
    if ally_has_soul:
        severity = "alert" if remaining <= BARON_PRIORITY_WINDOW_S else "warn"
        return Recommendation(
            text=f"Elder in {int(remaining)}s — Soul + Elder = GG JETZT!",
            severity=severity,
            category="objective",
            confidence=0.94,
            risk="MEDIUM",
            ttl_s=remaining,
            kind="elder_take",
            reasons=(
                "Wir haben Dragon Soul — Elder-Buff = Execute-Schaden!",
                f"Elder in {int(remaining)}s — Soul + Elder ist unschlagbar",
                f"Gold-Diff: {gold:+d} | {allies_alive}v{enemies_alive} alive",
            ),
        )

    # Enemy has Dragon Soul → must contest Elder at all costs
    if enemy_has_soul:
        return Recommendation(
            text=f"Elder VERHINDERN in {int(remaining)}s — Gegner-Soul + Elder = GG!",
            severity="alert",
            category="objective",
            confidence=0.92,
            risk="HIGH",
            ttl_s=remaining,
            kind="elder_take",
            reasons=(
                f"Gegner hat Dragon Soul ({enemy_stacks} Stacks)!",
                "Elder geben = Gegner unschlagbar — Contest ist Pflicht!",
                f"Elder in {int(remaining)}s | Gold-Diff: {gold:+d}",
                f"Numbers: {allies_alive}v{enemies_alive}",
            ),
        )

    # Neither team has soul (early Elder, uncommon)
    active = remaining <= BARON_PRIORITY_WINDOW_S
    severity = "alert" if active else "warn"
    return Recommendation(
        text=f"Elder-Drache in {int(remaining)}s — Gruppenbildung!",
        severity=severity,
        category="objective",
        confidence=0.88,
        risk="MEDIUM",
        ttl_s=remaining,
        kind="elder_take",
        reasons=(
            f"Elder Drake in {int(remaining)}s — Execute-Buff für ganzes Team",
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


def rule_ally_herald_window(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Ally picked up Rift Herald — place it within 3 minutes (B4 tempo).

    Eye of the Herald expires 180s after pickup. The rule fires for the
    full window with escalating urgency so the team doesn't waste the item:
    - >120s remaining → info "Herald platzieren!"
    - 60–120s           → warn "Herald läuft ab — JETZT platzieren!"
    - ≤60s              → alert "Herald JETZT! Nur noch Xs!"
    """
    pickup = _herald_pickup(snapshot, team="ally")
    if pickup is None:
        return None
    _, remaining = pickup
    if remaining > 120:
        return Recommendation(
            text=f"Ally Herald — platzieren! ({int(remaining)}s verbleibend)",
            severity="info",
            category="tempo",
            confidence=0.90,
            risk="LOW",
            ttl_s=remaining,
            kind="ally_herald",
            reasons=(
                f"Eye of the Herald: {int(remaining)}s bis Ablauf",
                "Herald → TOP/MID Tower = Plate-Gold + Map-Pressure",
                "Jetzt sidelaners informieren + platzieren",
            ),
        )
    if remaining > 60:
        return Recommendation(
            text=f"Herald läuft ab in {int(remaining)}s — JETZT platzieren!",
            severity="warn",
            category="tempo",
            confidence=0.93,
            risk="LOW",
            ttl_s=remaining,
            kind="ally_herald",
            reasons=(
                f"Eye of the Herald: nur noch {int(remaining)}s!",
                "Herald Ablauf = verschwendetes Objective",
                "Sofort Top- oder Mid-Tower angreifen",
            ),
        )
    return Recommendation(
        text=f"Herald JETZT! Nur noch {int(remaining)}s — sofort platzieren!",
        severity="alert",
        category="tempo",
        confidence=0.96,
        risk="LOW",
        ttl_s=remaining,
        kind="ally_herald",
        reasons=(
            f"Eye of the Herald: {int(remaining)}s — KRITISCH",
            "Herald verschwindet gleich — sofort nutzen!",
        ),
    )


def rule_ally_turret_lost(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy destroyed one of OUR turrets within the last 60 seconds.

    Fires a short-lived defensive nudge: recall to clear the wave, prevent
    the enemy from extending the advantage. Severity scales with turret tier:
      P1 (Outer)  → info   — wave-clear + rotate
      P2 (Inner)  → warn   — base is now reachable
      P3 (Inhib)  → alert  — inhibitor turret gone, base siege imminent

    Only fires within ALLY_TURRET_ALERT_WINDOW_S (60s) of the kill so the
    signal doesn't linger for the rest of the game. Not suppressed by
    numbers_disadv (defensive signals remain relevant while short-handed).
    """
    losses = _recent_ally_turret_losses(snapshot)
    if not losses:
        return None

    # Escalate to the highest-tier recent loss.
    tier_rank = {"P3": 3, "P2": 2, "P1": 1}
    losses.sort(key=lambda t: tier_rank.get(t[1], 0), reverse=True)
    lane, tier, _side, evt_time = losses[0]
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    age_s = int(game_time - evt_time)
    gold = _team_gold_diff(snapshot)

    if tier == "P3":
        return Recommendation(
            text=f"Inhib-Turm {lane} verloren — SOFORT Basis verteidigen!",
            severity="alert",
            category="safety",
            confidence=0.88,
            risk="HIGH",
            ttl_s=max(0.0, ALLY_TURRET_ALERT_WINDOW_S - age_s),
            kind="ally_turret_lost",
            reasons=(
                f"Gegner zerstörte unseren {lane} Inhib-Turm (vor {age_s}s)",
                "Inhib jetzt angreifbar — Super-Minions drohen!",
                f"Gold-Diff: {gold:+d}",
            ),
        )
    if tier == "P2":
        return Recommendation(
            text=f"Inner {lane}-Turm verloren — Wave claren, Basis absichern",
            severity="warn",
            category="safety",
            confidence=0.82,
            risk="HIGH",
            ttl_s=max(0.0, ALLY_TURRET_ALERT_WINDOW_S - age_s),
            kind="ally_turret_lost",
            reasons=(
                f"Gegner zerstörte unseren {lane} Inner Tower (vor {age_s}s)",
                "Lane offen bis Inhib-Turm — Wellen drohen Base",
                f"Gold-Diff: {gold:+d}",
            ),
        )
    # P1 — outer turret
    return Recommendation(
        text=f"Outer {lane}-Turm verloren — Welle claren, dann reagieren",
        severity="info",
        category="safety",
        confidence=0.74,
        risk="MEDIUM",
        ttl_s=max(0.0, ALLY_TURRET_ALERT_WINDOW_S - age_s),
        kind="ally_turret_lost",
        reasons=(
            f"Gegner zerstörte unseren {lane} Outer Tower (vor {age_s}s)",
            "Lane jetzt nur noch Inner Tower — reagieren!",
            f"Gold-Diff: {gold:+d}",
        ),
    )


def rule_dragon_soul_pressure(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Fires for DRAGON_SOUL_SIGNAL_S seconds after the 4th dragon is secured.

    Reminds the team to capitalise on Dragon Soul before regrouping attention
    on Baron/Elder. Suppressed by any active objective-take call (which is
    already the more specific action) and by numbers_disadv."""
    ally_stacks = _drake_stack_count(snapshot)
    if ally_stacks < 4:
        return None
    events = getattr(snapshot, "raw_events", []) or []
    allies = list(getattr(snapshot, "allies", []) or [])
    if not allies:
        return None
    ids = _player_ids(allies)
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    dragon_kills = sorted(
        (e for e in events if e.get("EventName") == "DragonKill" and e.get("KillerName") in ids),
        key=lambda e: float(e.get("EventTime", 0)),
    )
    if len(dragon_kills) < 4:
        return None
    soul_time = float(dragon_kills[3].get("EventTime", 0))
    age_s = game_time - soul_time
    if age_s > DRAGON_SOUL_SIGNAL_S:
        return None
    gold = _team_gold_diff(snapshot)
    return Recommendation(
        text="Dragon Soul gesichert — Group + Baron/Elder forcen!",
        severity="info",
        category="objective",
        confidence=0.82,
        risk="LOW",
        ttl_s=max(0.0, DRAGON_SOUL_SIGNAL_S - age_s),
        kind="dragon_soul",
        reasons=(
            f"Dragon Soul aktiv ({ally_stacks} Drachen)!",
            "Baron + Dragon Soul = maximale Siegchance",
            f"Gold-Diff: {gold:+d}",
        ),
    )


def rule_void_grubs(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Early-game Void Grub objective (Season 14+, 4:30–14:00 window).

    Tracks ally/enemy VoidGrub event counts and surfaces:
      - Enemy Hornguard (≥3 grubs) → warn to play turrets defensively
      - Ally Hornguard (≥3 grubs)  → info to press tower advantages
      - Contest phase (neither at 3) → info to keep contesting

    Note: LCDA event name is 'VoidGrub' (not 'VoidGrubKill').
    If Riot renames it in a future patch, update the EventName strings
    in _ally_grub_count / _enemy_grub_count."""
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    if not (VOID_GRUB_WINDOW_START_S <= game_time <= VOID_GRUB_WINDOW_END_S):
        return None
    ally_grubs = _ally_grub_count(snapshot)
    enemy_grubs = _enemy_grub_count(snapshot)
    total_killed = ally_grubs + enemy_grubs
    gold = _team_gold_diff(snapshot)
    remaining_window = max(0.0, VOID_GRUB_WINDOW_END_S - game_time)

    # Enemy Hornguard — defensive alert
    if enemy_grubs >= VOID_GRUB_HORNGUARD:
        return Recommendation(
            text=f"Gegner Hornguard ({enemy_grubs} Grubs) — Türme vorsichtig verteidigen!",
            severity="warn",
            category="safety",
            confidence=0.82,
            risk="HIGH",
            ttl_s=min(60.0, remaining_window),
            kind="enemy_hornguard",
            reasons=(
                f"Gegner hat {enemy_grubs} Void Grubs — Hornguard-Voidmites aktiv!",
                "Voidmites greifen Türme an — defensiv spielen",
                f"Gold-Diff: {gold:+d}",
            ),
        )

    # Ally Hornguard — push signal
    if ally_grubs >= VOID_GRUB_HORNGUARD:
        return Recommendation(
            text=f"Hornguard aktiv ({ally_grubs} Grubs) — Türme pushen!",
            severity="info",
            category="objective",
            confidence=0.78,
            risk="LOW",
            ttl_s=min(60.0, remaining_window),
            kind="ally_hornguard",
            reasons=(
                f"Wir haben {ally_grubs} Void Grubs — Voidmites attacken Türme!",
                "Split-Push oder Group für maximalen Türm-Druck",
                f"Gold-Diff: {gold:+d}",
            ),
        )

    # Contest phase — grubs still available
    if total_killed < 6:
        needed = max(0, VOID_GRUB_HORNGUARD - ally_grubs)
        return Recommendation(
            text=f"Void Grubs — noch {needed} für Hornguard! ({int(remaining_window)}s)",
            severity="info",
            category="objective",
            confidence=0.72,
            risk="MEDIUM",
            ttl_s=min(60.0, remaining_window),
            kind="void_grub_contest",
            reasons=(
                f"Void Grubs: Wir {ally_grubs} — Gegner {enemy_grubs} (von 6 total)",
                f"{VOID_GRUB_HORNGUARD} Grubs = Hornguard (Voidmites greifen Türme an)",
                f"Fenster endet ca. 14:00 ({int(remaining_window)}s übrig)",
            ),
        )
    return None


def rule_enemy_jungler_down(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy jungler is dead — push/contest window (B2 contribution).

    Detects the enemy jungler via Smite spell presence. When their respawn
    timer exceeds JUNGLER_DOWN_MIN_S this rule fires:
    - alert + "take objective" when any objective spawns within 60s
    - warn + "push lane" otherwise

    TTL = respawn_timer so the card expires when they come back.
    Suppressed by numbers_disadv (we're short-handed too), ace (already
    acting on full momentum), and ally_inhib_down (defend first)."""
    enemies = list(getattr(snapshot, "enemies", []) or [])
    jungler = next((p for p in enemies if _is_jungler(p)), None)
    if jungler is None:
        return None
    respawn = float(getattr(jungler, "respawn_timer", 0.0) or 0.0)
    if respawn < JUNGLER_DOWN_MIN_S:
        return None
    champ = getattr(jungler, "champion_name", "") or "Jungler"
    gold = _team_gold_diff(snapshot)
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    objs = list(getattr(snapshot, "objectives", []) or [])
    obj_soon = any(
        (rem := o.remaining(game_time)) is not None and 0.0 <= rem <= JUNGLER_DOWN_OBJ_WINDOW_S
        for o in objs
    )
    if obj_soon:
        text = f"JUNGLER DOWN ({champ} — {int(respawn)}s) — JETZT Objective sichern!"
        severity = "alert"
    else:
        text = f"Jungler down ({champ} — {int(respawn)}s) — Lane pushen!"
        severity = "warn"
    return Recommendation(
        text=text,
        severity=severity,
        category="objective",
        confidence=0.85,
        risk="LOW",
        ttl_s=respawn,
        kind="jungler_down",
        reasons=(
            f"{champ} respawnt in {int(respawn)}s",
            "Kein Gank-Risiko — aggressiv pushen / Objective nehmen",
            f"Gold-Diff: {gold:+d}",
        ),
    )


def rule_enemy_dragon_soul(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy is at soul point (3 drakes) — persistent denial reminder (B3).

    Fires between drake spawns when enemy has exactly 3 stacks. Silent when
    dragon_window is about to open (within ENEMY_SOUL_POINT_HANDOFF_S) since
    that rule provides more specific "VERHINDERN" messaging, and silent once
    the enemy secures soul (4+ stacks, game-over scenario handled elsewhere).
    """
    enemy_stacks = _enemy_drake_stack_count(snapshot)
    if enemy_stacks != 3:
        return None
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    drake_obj = next(
        (o for o in (getattr(snapshot, "objectives", []) or [])
         if getattr(o, "name", "") == "Dragon"),
        None,
    )
    remaining = drake_obj.remaining(game_time) if drake_obj else None
    if remaining is not None and remaining <= ENEMY_SOUL_POINT_HANDOFF_S:
        return None  # dragon_window takes over
    gold = _team_gold_diff(snapshot)
    ttl = min(ENEMY_SOUL_POINT_HANDOFF_S, remaining - ENEMY_SOUL_POINT_HANDOFF_S) if remaining else 90.0
    return Recommendation(
        text="Feind bei 3 Drachen — SOUL POINT! Nächsten Drake um jeden Preis verhindern!",
        severity="warn",
        category="objective",
        confidence=0.88,
        risk="HIGH",
        ttl_s=max(30.0, ttl),
        kind="enemy_soul_point",
        reasons=(
            f"Gegner: {enemy_stacks} Drake-Stacks — ein Drache = Soul!",
            "Drake-Soul ist oft spielentscheidend — unbedingt verhindern",
            f"Gold-Diff: {gold:+d}",
        ),
    )


def rule_ally_inhib_respawning(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Ally inhibitor respawns soon — transition from defense to objectives (B4).

    Fires in the final ALLY_INHIB_RESPAWN_ALERT_S (60s) before the soonest
    ally inhib respawn, signaling that the defensive pressure window is closing
    and the team can plan a Baron/Dragon call.
    """
    remaining = _earliest_ally_inhib_respawn_remaining(snapshot)
    if remaining is None or remaining > ALLY_INHIB_RESPAWN_ALERT_S:
        return None
    return Recommendation(
        text=f"Ally Inhib respawnt in {int(remaining)}s — dann Objectives möglich!",
        severity="info",
        category="tempo",
        confidence=0.88,
        risk="LOW",
        ttl_s=remaining,
        kind="ally_inhib_respawning",
        reasons=(
            f"Eigener Inhibitor respawnt in {int(remaining)}s",
            "Super-Minions stoppen → Baron/Dragon-Fenster öffnet sich",
            "Ults + Wellen bereit halten",
        ),
    )


def rule_ally_inhib_down(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy destroyed one or more of OUR inhibitors — defensive alert (B4).

    Super-minions now spawn for the enemy in our lanes. Risk: enemy can
    siege our base towers without any effort. Correct play is wave-clear
    priority over mid-map objectives until the inhibitor respawns.
    Suppressed when numbers_disadv is also active (already showing safety rec).
    """
    count = _active_ally_inhibitors_down(snapshot)
    if count <= 0:
        return None
    label = f"{count}x" if count > 1 else "Dein"
    return Recommendation(
        text=f"{label} Inhib DOWN — Wellen clearen! Basis verteidigen!",
        severity="alert" if count >= 2 else "warn",
        category="safety",
        confidence=0.90,
        risk="HIGH",
        ttl_s=90.0,
        kind="ally_inhib_down",
        reasons=(
            f"{count} eigener Inhibitor zerstört",
            "Feind-Super-Minions spawnen in deiner Lane",
            "Wellen clearen → Nexus-Türme schützen",
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


def rule_enemy_inhib_expiring(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy inhibitor is about to respawn — push NOW before it comes back (B4).

    Inhibitors respawn 300s after being killed. Fires in the final
    INHIB_EXPIRY_ALERT_S (60s) as a last-chance push reminder. Once the
    inhib is back, the super-minion pressure ends and the siege window
    closes.

    Suppressed by numbers_disadv and ally_inhib_down — when defending
    our own base or short-handed, attacking theirs is wrong priority.
    """
    remaining = _earliest_enemy_inhib_respawn_remaining(snapshot)
    if remaining is None or remaining > INHIB_EXPIRY_ALERT_S:
        return None
    severity = "alert" if remaining <= 30 else "warn"
    return Recommendation(
        text=f"Feind-Inhib respawnt in {int(remaining)}s — JETZT Nexus-Türme!",
        severity=severity,
        category="lane",
        confidence=0.90,
        risk="LOW",
        ttl_s=remaining,
        kind="inhib_expiring",
        reasons=(
            f"Enemy Inhibitor respawnt in {int(remaining)}s",
            "Super-Minion-Pressure endet — Fenster schließt sich",
            "Nexus-Türme jetzt angreifen oder Vorteil verlieren",
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


def rule_baron_buff_expiring(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Ally Baron buff (Hand of Baron) is about to run out — push NOW (B4).

    The buff lasts 180s. Fires during the final BARON_BUFF_EXPIRY_ALERT_S (60s)
    as a last-chance reminder to convert waves or take a structure before the
    enhanced-minion pressure is lost. Suppressed by numbers_disadv — pushing
    into an alive enemy team while short-handed is still bad.
    """
    remaining = _ally_baron_buff_remaining(snapshot)
    if remaining is None or remaining > BARON_BUFF_EXPIRY_ALERT_S:
        return None
    severity = "alert" if remaining <= 30 else "warn"
    return Recommendation(
        text=f"Baron-Buff läuft ab in {int(remaining)}s — JETZT pushen!",
        severity=severity,
        category="tempo",
        confidence=0.92,
        risk="LOW",
        ttl_s=remaining,
        kind="baron_buff_expiring",
        reasons=(
            f"Hand of Baron endet in {int(remaining)}s",
            "Supercharged Minions — letztes Push-Fenster nutzen",
            "Nexus-Türme jetzt oder Buff verschwendet",
        ),
    )


def rule_enemy_baron_buff(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy has active Baron Nashor buff — defend our base (B4).

    Fires for the full 180s buff duration. Severity escalates in the
    final 60s when the buff is expiring and a counter-engage window opens:
    - remaining > 60s → warn "defend base"
    - remaining ≤ 60s → alert "counter-engage window" (they're losing the buff)

    NOT suppressed by numbers_disadv — knowing the enemy has Baron is
    still critical defensive information when short-handed.
    """
    remaining = _enemy_baron_buff_remaining(snapshot)
    if remaining is None:
        return None
    if remaining > BARON_BUFF_EXPIRY_ALERT_S:
        return Recommendation(
            text=f"Feind Baron-Buff ({int(remaining)}s) — Basis sichern! Kein Mid-Map!",
            severity="warn",
            category="safety",
            confidence=0.95,
            risk="HIGH",
            ttl_s=remaining,
            kind="enemy_baron_buff",
            reasons=(
                f"Gegner hat Hand of Baron — {int(remaining)}s verbleibend",
                "Feind-Super-Minions + Buff — Basis-Türme defensiv halten",
                "Kein Objective contest — erst Baron-Buff ablaufen lassen",
            ),
        )
    return Recommendation(
        text=f"Feind Baron-Buff läuft ab in {int(remaining)}s — Konter-Engage Fenster!",
        severity="alert",
        category="tempo",
        confidence=0.88,
        risk="MEDIUM",
        ttl_s=remaining,
        kind="enemy_baron_buff",
        reasons=(
            f"Feind Baron-Buff endet in {int(remaining)}s",
            "Buff-Vorteil endet — Konter-Engage wird möglich",
            "Wellen clearen + Ults bereit → dann Engage",
        ),
    )


def rule_enemy_elder_buff(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy has Elder Drake buff — do NOT fight, stall until it expires (B4).

    Elder buff lasts 150s and grants execute damage (true damage at low HP).
    Fighting while the enemy has Elder is almost always fatal. Two phases:
    - >30s remaining → alert: stall, do NOT engage
    - ≤30s remaining → alert: buff expires soon, counter-engage window opens
    """
    remaining = _enemy_elder_buff_remaining(snapshot)
    if remaining is None:
        return None
    if remaining > 30:
        return Recommendation(
            text=f"FEIND ELDER-BUFF ({int(remaining)}s) — NICHT KÄMPFEN! Stallen!",
            severity="alert",
            category="safety",
            confidence=0.95,
            risk="HIGH",
            ttl_s=remaining,
            kind="enemy_elder_buff",
            reasons=(
                f"Gegner hat Elder-Buff — {int(remaining)}s verbleibend",
                "Elder Execute = True Damage — jeder Fight tödlich!",
                "Wellen clearen + Basis sichern → Buff ablaufen lassen",
            ),
        )
    return Recommendation(
        text=f"Feind Elder-Buff endet in {int(remaining)}s — Konter-Engage Fenster!",
        severity="alert",
        category="tempo",
        confidence=0.90,
        risk="MEDIUM",
        ttl_s=remaining,
        kind="enemy_elder_buff",
        reasons=(
            f"Elder-Buff endet in {int(remaining)}s",
            "Execute-Vorteil endet — Engage wird sicherer",
            "Ults bereit halten → sofort Engage wenn Buff weg",
        ),
    )


def rule_elder_buff_expiring(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Ally Elder Drake buff about to expire — push NOW (B4).

    Elder buff lasts 150s; fires in the final ELDER_BUFF_EXPIRY_ALERT_S (60s).
    Elder-buffed executes (true damage at low HP) make teamfights decisive —
    the team should be grouping and fighting, not farming waves.
    """
    remaining = _ally_elder_buff_remaining(snapshot)
    if remaining is None or remaining > ELDER_BUFF_EXPIRY_ALERT_S:
        return None
    severity = "alert" if remaining <= 30 else "warn"
    return Recommendation(
        text=f"Elder-Buff läuft ab in {int(remaining)}s — JETZT teamfighten!",
        severity=severity,
        category="tempo",
        confidence=0.93,
        risk="LOW",
        ttl_s=remaining,
        kind="elder_buff_expiring",
        reasons=(
            f"Elder Drake Buff endet in {int(remaining)}s",
            "Elder Execute = True Damage bei niedrigem HP — Fights gewinnen",
            "Letztes Fenster mit Elder-Vorteil — jetzt gruppieren!",
        ),
    )


def rule_enemy_flash_down(
    snapshot: "LcdaSnapshot",
    spell_tracker: "SpellTracker",
) -> Recommendation | None:
    """Alert when one or more enemies have Flash on cooldown (B2 — engage window).

    Requires a SpellTracker with user-tracked spell casts. Fires when at least
    one tracked enemy flash has more than FLASH_DOWN_ALERT_S remaining so the
    alert is still actionable. Suppressed by _suppress_dominated when the team
    is behind (numbers_disadv) — flash-down is an opportunity only when safe.
    """
    enemies = list(getattr(snapshot, "enemies", []) or [])
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    if not enemies or not game_time:
        return None

    flashes_down: list[tuple[str, float]] = []
    for enemy in enemies:
        for spell in (getattr(enemy, "spell_one", None), getattr(enemy, "spell_two", None)):
            if spell is None or getattr(spell, "name", "") != "Flash":
                continue
            name = getattr(enemy, "summoner_name", "") or getattr(enemy, "champion_name", "")
            remaining = spell_tracker.remaining(
                getattr(enemy, "summoner_name", ""), "Flash", game_time
            )
            if remaining > FLASH_DOWN_ALERT_S:
                flashes_down.append((name, remaining))
            break  # each enemy has at most one Flash

    if not flashes_down:
        return None

    count = len(flashes_down)
    names = ", ".join(n for n, _ in flashes_down[:3])
    min_remaining = min(r for _, r in flashes_down)

    if count == 1:
        name, remaining = flashes_down[0]
        text = f"Flash down: {name} ({int(remaining)}s)"
    else:
        text = f"{count}× Flash down — Engage-Fenster!"

    return Recommendation(
        text=text,
        severity="warn",
        category="tempo",
        confidence=0.85,
        risk="MEDIUM",
        ttl_s=min_remaining,
        kind="flash_down",
        reasons=(
            f"{names} ohne Flash",
            f"Flash bereit in ~{int(min_remaining)}s",
            "Gutes Fenster zum Engagen oder Diven",
        ),
    )


def rule_enemy_tp_down(
    snapshot: "LcdaSnapshot",
    spell_tracker: "SpellTracker",
) -> Recommendation | None:
    """Advisory when one or more enemies have Teleport on cooldown (B2).

    TP down blocks global rotations — the enemy can't react to your
    side-lane pressure or TP to save a collapsing teamfight. Fires when
    remaining CD > TP_DOWN_ALERT_S (90s) so the info is still actionable.
    Suppressed by numbers_disadv — don't split when short-handed.

    Severity scales with count:
    - 1 TP down → info (single-person advisory)
    - 2+ TP down → warn (major tempo window)
    """
    enemies = list(getattr(snapshot, "enemies", []) or [])
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    if not enemies or not game_time:
        return None

    tps_down: list[tuple[str, float]] = []
    for enemy in enemies:
        for spell in (getattr(enemy, "spell_one", None), getattr(enemy, "spell_two", None)):
            if spell is None or getattr(spell, "name", "") != "Teleport":
                continue
            name = (
                getattr(enemy, "summoner_name", "")
                or getattr(enemy, "champion_name", "")
            )
            remaining = spell_tracker.remaining(
                getattr(enemy, "summoner_name", ""), "Teleport", game_time,
            )
            if remaining > TP_DOWN_ALERT_S:
                tps_down.append((name, remaining))
            break

    if not tps_down:
        return None

    count = len(tps_down)
    names = ", ".join(n for n, _ in tps_down[:3])
    min_remaining = min(r for _, r in tps_down)
    severity = "warn" if count >= 2 else "info"

    if count == 1:
        name, remaining = tps_down[0]
        text = f"TP down: {name} ({int(remaining)}s) — kein Flank-TP!"
    else:
        text = f"{count}× TP down ({int(min_remaining)}s) — keine globale Rotation!"

    return Recommendation(
        text=text,
        severity=severity,
        category="tempo",
        confidence=0.88,
        risk="LOW",
        ttl_s=min_remaining,
        kind="tp_down",
        reasons=(
            f"{names} ohne Teleport",
            f"TP bereit in ~{int(min_remaining)}s",
            "Kein TP = kein globaler Eingriff — Side-Lanes frei!",
        ),
    )


def rule_enemy_combat_spell_down(
    snapshot: "LcdaSnapshot",
    spell_tracker: "SpellTracker",
) -> Recommendation | None:
    """Advisory when enemy has a tracked combat summoner spell on CD (B2).

    Covers Exhaust, Heal, Ignite, Barrier, and Cleanse — spells that
    directly affect trade outcomes. Each has a specific tactical message:
    - Exhaust down → enemy can't kite/reduce your carry
    - Heal down → no sustain/movement speed boost for ADC
    - Ignite down → no kill threat; trades are safer
    - Barrier/Cleanse down → burst/CC window

    Fires when remaining CD > COMBAT_SPELL_ALERT_S (60s). Groups multiple
    down-spells into one card to avoid flooding the overlay. Severity is
    always "info" — these are advisory notes, not urgent signals.
    """
    enemies = list(getattr(snapshot, "enemies", []) or [])
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    if not enemies or not game_time:
        return None

    _SPELL_HINTS: dict[str, str] = {
        "Exhaust":  "kein Exhaust — Carry kann all-in gehen",
        "Heal":     "kein Heal — ADC hat keine Sustain",
        "Ignite":   "kein Ignite — kein Kill-Threat in Lane",
        "Barrier":  "kein Barrier — Burst-Fenster!",
        "Cleanse":  "kein Cleanse — CC trifft sicher",
    }

    down: list[tuple[str, str, float]] = []  # (name, spell, remaining)
    for enemy in enemies:
        for spell in (getattr(enemy, "spell_one", None), getattr(enemy, "spell_two", None)):
            spell_name = getattr(spell, "name", "") if spell is not None else ""
            if spell_name not in _COMBAT_SPELLS:
                continue
            summoner = getattr(enemy, "summoner_name", "") or getattr(enemy, "champion_name", "")
            remaining = spell_tracker.remaining(
                getattr(enemy, "summoner_name", ""), spell_name, game_time,
            )
            if remaining > COMBAT_SPELL_ALERT_S:
                down.append((summoner, spell_name, remaining))
            break

    if not down:
        return None

    min_remaining = min(r for _, _, r in down)

    if len(down) == 1:
        name, spell_name, remaining = down[0]
        hint = _SPELL_HINTS.get(spell_name, f"kein {spell_name}")
        text = f"{spell_name} down: {name} ({int(remaining)}s) — {hint}"
    else:
        summary = ", ".join(f"{s}({n})" for n, s, _ in down[:3])
        text = f"Spells down: {summary}"

    return Recommendation(
        text=text,
        severity="info",
        category="tempo",
        confidence=0.83,
        risk="LOW",
        ttl_s=min_remaining,
        kind="combat_spell_down",
        reasons=tuple(
            f"{s} down: {n} — {_SPELL_HINTS.get(s, s)} ({int(r)}s)"
            for n, s, r in down
        ),
    )


def rule_fight_window_closing(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Numbers advantage that is about to disappear because a dead enemy
    respawns within FIGHT_WINDOW_CLOSING_S seconds.

    Complements rule_ace_detected (fires while all are dead) and
    rule_numbers_advantage (fires on a sustained lead). This rule fires
    during the transition: we're still ahead, but the clock is running.
    Suppressed by ace (which is already urging the push).
    """
    allies = list(getattr(snapshot, "allies", []) or [])
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not allies or not enemies:
        return None

    # Require live respawn data — fall back gracefully if LCDA omits it.
    has_enemy_respawn = any(
        getattr(e, "respawn_timer", None) is not None for e in enemies
    )
    if not has_enemy_respawn:
        return None

    allies_alive = _alive_count(allies, default_to_full_team=False)
    enemies_alive = _alive_count(enemies, default_to_full_team=False)
    if allies_alive <= enemies_alive:
        return None

    # Find the enemy whose respawn is most imminent (but not yet alive).
    imminent = [
        e for e in enemies
        if not getattr(e, "is_alive", True)
        and 0 < getattr(e, "respawn_timer", 0.0) <= FIGHT_WINDOW_CLOSING_S
    ]
    if not imminent:
        return None

    soonest = min(imminent, key=lambda e: getattr(e, "respawn_timer", 99.0))
    timer = int(getattr(soonest, "respawn_timer", 1.0))
    name = (
        getattr(soonest, "champion_name", "")
        or getattr(soonest, "summoner_name", "")
        or "?"
    )

    return Recommendation(
        text=f"Jetzt pushen — {name} zurück in {timer}s!",
        severity="alert",
        category="tempo",
        confidence=0.90,
        risk="LOW",
        ttl_s=float(timer + 3),
        kind="window_closing",
        reasons=(
            f"{allies_alive}v{enemies_alive} alive",
            f"{name} respawnt in {timer}s — Fenster schließt sich",
        ),
    )


def rule_power_spike(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Alert when the active player just crossed a power-spike threshold
    (level 6/11/16 or first/second/third legendary item).

    The ``PowerSpikePanel`` in the main overlay hides during gameplay —
    this rule surfaces the same signal in the floating RecommendationPanel
    so the alert is visible while a game is running.

    TTL is short (20-30s) so the card expires before the window closes.
    """
    spikes = getattr(snapshot, "new_spikes", []) or []
    if not spikes:
        return None
    # Level spikes (6/11/16) outweigh item spikes; higher value wins within each kind.
    spike = max(spikes, key=lambda s: (1 if getattr(s, "kind", "") == "level" else 0, getattr(s, "value", 0)))
    kind = getattr(spike, "kind", "")
    value = getattr(spike, "value", 0)
    label = getattr(spike, "label", "Power Spike")
    detail = getattr(spike, "detail", "")

    text = f"{label} — {detail}" if detail else label

    if kind == "level" and value == 6:
        severity, ttl_s = "alert", 20.0
    elif kind == "level":
        severity, ttl_s = "warn", 25.0
    elif kind == "items" and value == 2:
        severity, ttl_s = "warn", 30.0
    else:
        severity, ttl_s = "info", 30.0

    return Recommendation(
        text=text,
        severity=severity,
        category="tempo",
        confidence=1.0,
        risk="LOW",
        ttl_s=ttl_s,
        kind="power_spike",
        reasons=(detail,) if detail else (),
    )


def rule_enemy_item_spike(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Warn when an enemy champion just completed their 1st/2nd/3rd legendary
    item. The most dangerous spike (highest legendary count) surfaces first
    so the player knows which enemy is now scarier.

    2nd legendary is the critical threshold for carries — that's typically
    their mid-game power peak. 1st legendary fires as info only.
    """
    spikes = getattr(snapshot, "enemy_spikes", []) or []
    if not spikes:
        return None

    # Most dangerous spike first (highest legendary count, then alphabetical).
    top = max(spikes, key=lambda s: (getattr(s, "legendary_count", 0), getattr(s, "champion_name", "")))
    champ = getattr(top, "champion_name", "Gegner")
    count = getattr(top, "legendary_count", 1)

    if count >= 2:
        severity, ttl_s = "warn", 30.0
        text = f"{champ} hat {count}. Item — Vorsicht, starker Spike!"
    else:
        severity, ttl_s = "info", 25.0
        text = f"{champ} hat 1. Item fertig"

    return Recommendation(
        text=text,
        severity=severity,
        category="safety",
        confidence=0.90,
        risk="MEDIUM" if count >= 2 else "LOW",
        ttl_s=ttl_s,
        kind="enemy_spike",
        reasons=(f"{champ}: {count}. Legendary Item abgeschlossen",),
    )


# Game-phase boundaries used by tilt-rule messaging. These are
# coaching cutoffs, not hard mechanical phases — late-game advice
# (group 5, no splits) gets dangerous before 25:00 in solo-queue.
_TILT_LANE_PHASE_END_S: float = 840.0    # 14:00 — first item, lane priority shifts
_TILT_MID_GAME_END_S: float = 1500.0     # 25:00 — Baron + late-game grouping


def _tilt_phase_advice(game_time: float) -> str:
    """One-liner of *what to do during the next walk-back* given the
    current game phase. Returned advice is concrete, not motivational."""
    if game_time <= _TILT_LANE_PHASE_END_S:
        return "Welle unter Turm freezen, Jungler pingen, kein 1v1"
    if game_time <= _TILT_MID_GAME_END_S:
        return "Mit Team gruppieren, kein Side-Lane, Vision setzen"
    return "Death-Timer 50s+ — niemals alleine zeigen, nur 5er Plays"


# ─── Recall-window thresholds (B5 — Recommendation Service) ──────────────────
# These match the way pros actually think about resource state, not raw HP/mana
# numbers. Tuned conservatively: false positives are worse than missed calls
# because the player will mute a noisy assistant within one game.

HP_CRITICAL_PCT: float = 0.30   # below this you die to a single combo
HP_LOW_PCT: float = 0.50        # below this, trades aren't safe
MANA_DEPLETED_PCT: float = 0.20 # below this, you can't trade or escape
MANA_LOW_PCT: float = 0.30      # below this, you're at most 1 ability away from dry

# Gold tiers — generic component thresholds the player can map to their build.
GOLD_BACK_WORTH: float = 1100.0       # Sheen / Tear / first boots
GOLD_COMPONENT_SPIKE: float = 1300.0  # Lost Chapter / Caulfield's tier
GOLD_LARGE_SPIKE: float = 1600.0      # Pickaxe / BF Sword tier

# Recall coaching is most valuable in lane + early mid-game. After 20:00,
# back timing is dictated by team rotations, not personal resources.
RECALL_PHASE_END_S: float = 1200.0    # 20:00


# Hysteresis state for the recall rule — module-level dedup so each
# tier doesn't re-fire every 2 s while its trigger condition persists.
# All four tiers re-arm only after the player crosses the corresponding
# rearm threshold (HP > 35 %, mana > 30 %, gold spent below threshold).
_RECALL_CRITICAL_ARMED: bool = True
_RECALL_RESOURCE_ARMED: bool = True
_RECALL_GOLD_ARMED: bool = True
_RECALL_MANA_ARMED: bool = True
HP_RECALL_REARM_PCT: float = 0.35
MANA_RECALL_REARM_PCT: float = 0.30
GOLD_RECALL_REARM_BUFFER: float = 200.0  # gold must drop this far below threshold


def rule_recall_check(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Recall-window coaching driven by HP %, mana %, and gold (Charter B5).

    Picks at most one of four signals, in priority order:

    1. **Critical HP** (alert) — HP < 30 %; surfaces "back NOW" regardless
       of gold or game phase. Any next interaction kills you.

    2. **Resource depleted + back-worth gold** (warn) — HP < 50 % OR mana
       < 25 %, AND gold ≥ 1100. The classic "you need a reset, and you
       have value to bank" signal. Pros recall here every time.

    3. **Pure gold opportunity** (info) — gold ≥ 1300 in lane phase, even
       at full HP. The next trip back is worth a real spike; don't sit
       on uncashed gold while losing tempo.

    4. **Mana check** (info) — mana < 20 % in lane phase. Tells the
       player they're now in their opponent's all-in window, and to
       freeze the wave / use Doran's regen until mana is back.

    Skipped while dead (hp_pct ≤ 0). Does **not** fire after 20:00 except
    for tier 1 (critical HP); recall timing past 20:00 is dictated by
    team rotation, not personal resources.
    """
    global _RECALL_CRITICAL_ARMED, _RECALL_RESOURCE_ARMED
    global _RECALL_GOLD_ARMED, _RECALL_MANA_ARMED
    state = getattr(snapshot, "active_combat", None)
    if state is None:
        return None
    hp_pct = float(getattr(state, "hp_pct", 1.0))
    mana_pct = float(getattr(state, "mana_pct", 1.0))
    gold = float(getattr(state, "gold", 0.0))
    is_mana_user = bool(getattr(state, "is_mana_user", False))
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)

    # Dead players get no advice — they can't act on it before respawn.
    if hp_pct <= 0.0:
        # Reset hysteresis on death — next life starts fresh.
        _RECALL_CRITICAL_ARMED = True
        _RECALL_RESOURCE_ARMED = True
        _RECALL_GOLD_ARMED = True
        _RECALL_MANA_ARMED = True
        return None

    # Re-arm each tier once its rearm threshold is crossed. Without this
    # the rules fire every snapshot tick while their trigger condition
    # persists, producing dozens of identical recs per game.
    if hp_pct >= HP_RECALL_REARM_PCT:
        _RECALL_CRITICAL_ARMED = True
    if hp_pct >= HP_LOW_PCT and (not is_mana_user or mana_pct >= MANA_LOW_PCT):
        _RECALL_RESOURCE_ARMED = True
    if gold < GOLD_COMPONENT_SPIKE - GOLD_RECALL_REARM_BUFFER:
        _RECALL_GOLD_ARMED = True
    if not is_mana_user or mana_pct >= MANA_RECALL_REARM_PCT:
        _RECALL_MANA_ARMED = True

    # Tier 1 — Critical HP. Fires once per "below 30 %" episode.
    if hp_pct < HP_CRITICAL_PCT and not _RECALL_CRITICAL_ARMED:
        return None
    if hp_pct < HP_CRITICAL_PCT:
        _RECALL_CRITICAL_ARMED = False
        pct = int(hp_pct * 100)
        return Recommendation(
            text=f"{pct}% HP — RECALL JETZT, nächster Trade tötet dich",
            severity="alert",
            category="safety",
            confidence=0.95,
            risk="HIGH",
            ttl_s=15.0,
            kind="recall_critical",
            reasons=(
                f"HP: {pct}%",
                f"Gold dabei: {int(gold)}g",
                "Jeder Skillshot / Auto = Tod",
            ),
        )

    # Tier 2 — Resource depleted + back-worth gold (warn).
    resource_low = hp_pct < HP_LOW_PCT or (is_mana_user and mana_pct < MANA_LOW_PCT)
    if (
        resource_low and gold >= GOLD_BACK_WORTH
        and game_time <= RECALL_PHASE_END_S
        and _RECALL_RESOURCE_ARMED
    ):
        _RECALL_RESOURCE_ARMED = False
        triggers: list[str] = []
        if hp_pct < HP_LOW_PCT:
            triggers.append(f"HP {int(hp_pct*100)}%")
        if is_mana_user and mana_pct < MANA_LOW_PCT:
            triggers.append(f"Mana {int(mana_pct*100)}%")
        spike_tier = (
            "Large Item" if gold >= GOLD_LARGE_SPIKE
            else "Component Spike" if gold >= GOLD_COMPONENT_SPIKE
            else "Component"
        )
        return Recommendation(
            text=f"Recall lohnt — {' + '.join(triggers)}, {int(gold)}g für {spike_tier}",
            severity="warn",
            category="safety",
            confidence=0.85,
            risk="MEDIUM",
            ttl_s=20.0,
            kind="recall_resource",
            reasons=(
                *triggers,
                f"Gold: {int(gold)}g (≥{int(GOLD_BACK_WORTH)}g back-worth)",
                "Reset-Tempo > vor-pushen + halb-tot bleiben",
            ),
        )

    # Tier 3 — Pure gold opportunity (info). Lane phase only.
    if (
        gold >= GOLD_COMPONENT_SPIKE
        and game_time <= RECALL_PHASE_END_S
        and _RECALL_GOLD_ARMED
    ):
        _RECALL_GOLD_ARMED = False
        return Recommendation(
            text=f"{int(gold)}g — Recall-Fenster, Component-Spike kaufen + sicher zurück",
            severity="info",
            category="tempo",
            confidence=0.70,
            risk="LOW",
            ttl_s=20.0,
            kind="recall_gold",
            reasons=(
                f"Gold: {int(gold)}g (≥{int(GOLD_COMPONENT_SPIKE)}g Component-Spike)",
                f"HP {int(hp_pct*100)}% — sicherer Reset möglich",
            ),
        )

    # Tier 4 — Mana check (info). Lane phase only, mana users only.
    if (
        is_mana_user and mana_pct < MANA_DEPLETED_PCT
        and game_time <= RECALL_PHASE_END_S
        and _RECALL_MANA_ARMED
    ):
        _RECALL_MANA_ARMED = False
        return Recommendation(
            text=f"Mana {int(mana_pct*100)}% — Gegner-All-In-Fenster offen, Welle freezen + warten",
            severity="info",
            category="safety",
            confidence=0.65,
            risk="MEDIUM",
            ttl_s=15.0,
            kind="mana_check",
            reasons=(
                f"Mana: {int(mana_pct*100)}% (<{int(MANA_DEPLETED_PCT*100)}%)",
                "Kein Trade-Antwort verfügbar — defensive Position",
            ),
        )

    return None


def rule_tilt_detection(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Surface the active player's death pattern as a coaching call.

    Five tiers:
      * caution  — first lane death; freeze + ping
      * tilt     — 2 deaths in 90s; the classic tilt window
      * re_engage — 2 deaths in 60s; "1-and-done", do nothing for 30s
      * spiral   — 3 deaths in 180s OR 2 in 60s; hard reset

    Modifiers append to the rec text:
      * bounty_lost: "+ Bounty (3+ Streak) verloren — ~600g extra für Gegner"
      * solo_death:  "+ Alleine gestorben — keine Side-Lane mehr"

    The phase-aware advice line replaces the generic "play safe" because
    "play safe" means different things in lane vs mid-game vs late-game.
    Solo-queue users who *know* what playing safe means don't need
    coaching; the rest get concrete actions.
    """
    state = getattr(snapshot, "tilt_state", None)
    if state is None or getattr(state, "severity", "ok") == "ok":
        return None

    severity_tier = state.severity
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    phase_advice = _tilt_phase_advice(game_time)

    if severity_tier == "spiral":
        text = f"DEATH SPIRAL — {state.deaths_recent_180s} Tode in 3min. STOP. 60s NICHT zeigen — {phase_advice}"
        severity, ttl_s, confidence, risk = "alert", 90.0, 0.95, "HIGH"
    elif severity_tier == "re_engage":
        text = f"1-AND-DONE — 2 Tode in 60s. Direkt nach Respawn wieder rein = Disaster. 30s warten — {phase_advice}"
        severity, ttl_s, confidence, risk = "alert", 75.0, 0.90, "HIGH"
    elif severity_tier == "tilt":
        text = f"Tilt-Fenster — 2 Tode in 90s. Länger basen, 2 Components kaufen, {phase_advice}"
        severity, ttl_s, confidence, risk = "warn", 60.0, 0.85, "HIGH"
    else:  # caution — single lane death
        text = f"Erster Tod — {phase_advice}, kein Comeback-1v1 versuchen"
        severity, ttl_s, confidence, risk = "info", 30.0, 0.65, "MEDIUM"

    # Modifier suffixes — appended only when the modifier is true.
    # Reasons get the plain facts; text gets the actionable suffix.
    modifiers: list[str] = []
    reasons: list[str] = [
        f"Tode total: {state.deaths_total}",
        f"Tode in 90s: {state.deaths_recent_90s}",
    ]
    if state.bounty_lost:
        modifiers.append("+ Bounty (3+ Streak) verloren — ~600g extra Gegner")
        reasons.append("Bounty: 3+ unanswered kills vor letztem Tod verloren")
    if state.solo_death:
        modifiers.append("+ Alleine gestorben — keine Side-Lane mehr")
        reasons.append("Letzter Tod ohne Ally-Beteiligung — Positionierungsfehler")
    if modifiers:
        text = text + "  " + "  ".join(modifiers)

    return Recommendation(
        text=text,
        severity=severity,
        category="safety",
        confidence=confidence,
        risk=risk,
        ttl_s=ttl_s,
        kind="tilt",
        reasons=tuple(reasons),
    )


def rule_gank_risk(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Warn when the enemy jungler has been unaccounted-for long enough
    to be approaching a lane undetected (Charter B2).

    Uses the GankAlert computed by LcdaSource from ChampionKill event
    timestamps. 60s MIA → info; 90s MIA → warn. Only fires during
    laning phase (4–20 min) for lane roles (TOP, MID, BOT).
    """
    alert = getattr(snapshot, "gank_alert", None)
    if alert is None:
        return None
    jungler = getattr(alert, "jungler_name", "Jungler")
    mia = int(getattr(alert, "seconds_mia", 0))
    severity = str(getattr(alert, "severity", "info"))
    if severity == "warn":
        text = f"{jungler} seit {mia}s verschwunden — Gank möglich! Welle räumen oder zurückziehen."
        risk = "HIGH"
        ttl_s = 15.0
    else:
        text = f"{jungler} seit {mia}s nicht gesehen — Vorsicht in der Lane."
        risk = "MEDIUM"
        ttl_s = 12.0
    return Recommendation(
        text=text,
        severity=severity,
        category="safety",
        confidence=0.72,
        risk=risk,
        ttl_s=ttl_s,
        kind="gank_risk",
        reasons=(f"Jungler {jungler} nicht in Kill-Events seit {mia}s",),
    )


# Rule registry — extend by appending a function. Order doesn't affect
# ``evaluate``'s output (caller sorts by severity).
ALL_RULES: tuple[Callable[["LcdaSnapshot"], Recommendation | None], ...] = (
    # Game-end summary — trumps everything when the match is over.
    rule_game_ended,
    # Ace detection — highest-priority window, overrides most other calls.
    rule_ace_detected,
    # Closing window — enemy about to respawn, finish the push.
    rule_fight_window_closing,
    # Power spike — ult ready / item completed; brief action window.
    rule_power_spike,
    # Enemy item spike — enemy carry just completed a legendary.
    rule_enemy_item_spike,
    # B2 gank window — enemy jungler MIA during laning phase.
    rule_gank_risk,
    # B4 tilt detection — active player's death pattern coaching.
    rule_tilt_detection,
    # B5 recall window — HP/mana/gold-driven back timing.
    rule_recall_check,
    # Numbers-asymmetry — safety overrides objective calls.
    rule_numbers_disadvantage,
    rule_numbers_advantage,
    # Pro-level window rules (replace the simpler drake/baron 4-pack).
    rule_elder_window,
    rule_dragon_window,
    rule_baron_window,
    rule_herald_priority,
    rule_fight_opportunity,
    # General tempo + safety rules.
    rule_gold_lead_push,
    rule_far_behind_safe,
    rule_level_deficit,
    rule_lane_level_advantage,
    rule_kill_lead_snowball,
    rule_kill_deficit_defensive,
    rule_cs_deficit,
    rule_late_game_group,
    # Post-soul pressure — fires for 2 minutes after securing Dragon Soul.
    rule_dragon_soul_pressure,
    # Early-game Void Grub objective (4:30–14:00 window).
    rule_void_grubs,
    # B2 contribution — enemy jungler is dead, push/contest window.
    rule_enemy_jungler_down,
    # B3 — enemy at soul point (3 drakes), persistent denial reminder.
    rule_enemy_dragon_soul,
    # Lane/base pressure — structural map-state (turrets + inhibs + herald).
    rule_enemy_herald_danger,
    rule_ally_herald_window,
    rule_enemy_inhibitor_down,
    rule_enemy_inhib_expiring,
    rule_ally_turret_lost,
    rule_ally_inhib_respawning,
    rule_ally_inhib_down,
    rule_baron_buff_expiring,
    rule_enemy_baron_buff,
    rule_enemy_elder_buff,
    rule_elder_buff_expiring,
    rule_enemy_base_exposed,
    rule_lane_pressure,
)


_SEVERITY_RANK = {"alert": 0, "warn": 1, "info": 2}


# ─── Situational Build Rule ───────────────────────────────────────────────────

def rule_situational_build(
    snapshot: "LcdaSnapshot",
    build_result: object,
) -> Recommendation | None:
    """Recommend situational items based on game state and enemy team comp.

    Fires after the first 2 minutes when enemy champions are confirmed.
    ``build_result`` is a ``BuildResult`` from the build engine; passed in
    by ``evaluate`` so the rule stays pure — no async I/O.

    Suppressed early game and when no situational items are computed.
    """
    from ..advisor.build_engine import BuildResult  # local import avoids circular

    if not isinstance(build_result, BuildResult):
        return None

    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    if game_time < 120.0:
        return None

    situational = build_result.situational_items
    if not situational:
        return None

    # Pick the top 3 situational items and build a concise recommendation.
    top = situational[:3]

    # Collect context-driven reasons (lines that mention enemy comp adjustments).
    context_lines = [
        r for s in top
        for r in s.reasons
        if any(kw in r for kw in ("Gegner", "Sustain", "Tank", "Penetration", "Golddefizit"))
    ]

    item_list = " / ".join(s.item_name for s in top)
    if context_lines:
        headline = context_lines[0]
        text = f"Situational: {item_list} — {headline}"
    else:
        text = f"Situational Items: {item_list}"

    all_reasons = tuple(
        r for s in top for r in s.reasons[:2]
    )

    return Recommendation(
        text=text,
        severity="info",
        category="lane",
        confidence=0.80,
        risk="LOW",
        ttl_s=120.0,
        kind="situational_build",
        reasons=all_reasons,
    )


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
        _ace_drop = {
            "fight", "fight_bad", "numbers_adv", "gold_lead", "kill_lead",
            "jungler_down",
            "window_closing",  # ace already urges the push — closing window is redundant
            "gank_risk",       # full-team push moment — laning gank caution is irrelevant
            "tilt",            # ace push moment dominates personal-tilt coaching
            "recall_resource", # 4v0 push moment — back can wait until after the play
            "recall_gold",     # ditto — don't recall through an ace window
            "mana_check",      # mana doesn't matter when team is doing the work
        }
        recs = [r for r in recs if r.kind not in _ace_drop]
        kinds = {r.kind for r in recs}

    # Rule 2 — safety first
    if "numbers_disadv" in kinds:
        _offensive = {
            "fight", "numbers_adv", "gold_lead", "kill_lead",
            "dragon_take", "dragon_free", "baron_take", "baron_free",
            "elder_take",        # Elder is still a take — don't go while short-handed
            "flash_down",        # engage window irrelevant when short-handed
            "tp_down",           # side-lane freedom irrelevant when short-handed
            "combat_spell_down", # trade windows irrelevant when short-handed
            "baron_buff_expiring",  # pushing while outnumbered is still bad
            "elder_buff_expiring",  # same — don't fight when short-handed
            "ally_herald",       # don't split to place herald while down
            "inhib_expiring",    # don't push their base while short-handed
            "dragon_soul",       # don't Baron/Elder rush while short-handed
            "ally_hornguard",    # tower push irrelevant when down a player
            "void_grub_contest", # contesting grubs while short-handed is bad
            "jungler_down",      # push suggestion irrelevant when short-handed
            "enemy_soul_point",  # drake denial requires going aggro — not when short-handed
            "power_spike",       # "fight now!" irrelevant when team is short-handed
            "cs_deficit",        # farming advice irrelevant while team is down
            "lane_level_adv",    # lane trades irrelevant when short-handed
        }
        return [r for r in recs if r.kind not in _offensive]

    # Rule 3 — free-window objective absorbs standalone numbers_adv
    if "dragon_free" in kinds or "baron_free" in kinds or "elder_take" in kinds:
        recs = [r for r in recs if r.kind != "numbers_adv"]

    # Rule 4 — fight rec subsumes generic lead signals
    if "fight" in kinds:
        recs = [r for r in recs if r.kind not in {"gold_lead", "kill_lead"}]

    # Rule 5 — "don't fight" contradicts an active objective-take call;
    # suppress fight_bad when we're already recommending taking an objective.
    _obj_take = {"dragon_take", "dragon_free", "baron_take", "baron_free", "elder_take"}
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

    # Rule 8 — ally_inhib_down is a defensive alert; suppress mid-map objective
    # "take" signals, inhib_expiring push, and the turret alert (inhib is worse).
    if "ally_inhib_down" in kinds:
        _obj_take_kinds = {
            "dragon_take", "baron_take", "elder_take",
            "lane_open", "inhib_expiring", "ally_turret_lost",
            "dragon_soul", "ally_hornguard", "void_grub_contest",
            "jungler_down",      # don't push while defending super-minions
            "enemy_soul_point",  # drake denial requires committing — not when base is open
            "cs_deficit",        # farming advice irrelevant while defending base
            "lane_level_adv",    # lane-trade window irrelevant while base is open
            "gank_risk",         # laning gank warning irrelevant when base is open
            # NOTE: ally_inhib_respawning intentionally coexists with ally_inhib_down:
            # "defend now + inhib back in 30s" are complementary, not conflicting.
        }
        recs = [r for r in recs if r.kind not in _obj_take_kinds]

    # Rule 9 — cross-objective priority: when multiple objective-take kinds
    # fire simultaneously, keep only the highest-priority one.
    # Priority order: elder_take > baron_take/baron_free > dragon_take/dragon_free.
    kinds = {r.kind for r in recs}
    _present_baron = kinds & {"baron_take", "baron_free"}
    _present_dragon = kinds & {"dragon_take", "dragon_free"}
    if _present_baron and _present_dragon:
        # Baron beats dragon — drop the dragon objective cards.
        recs = [r for r in recs if r.kind not in _present_dragon]
        kinds = {r.kind for r in recs}

    # Rule 10 — enemy Elder buff is active: suppress all "fight/push" signals.
    # Fighting while enemy has Elder execute is nearly always fatal.
    if "enemy_elder_buff" in kinds:
        _fight_kinds = {
            "fight", "numbers_adv", "gold_lead", "kill_lead",
            "dragon_take", "dragon_free", "baron_take", "baron_free", "elder_take",
            "baron_buff_expiring", "elder_buff_expiring",
            "dragon_soul", "jungler_down",
        }
        recs = [r for r in recs if r.kind not in _fight_kinds]

    # Rule 11 — spiral-level tilt (alert) on the active player suppresses
    # offensive prompts. Telling a feeding player "fight now!" is the
    # worst possible combo; the tilt rec is asking them to do nothing.
    has_spiral_tilt = any(
        r.kind == "tilt" and r.severity == "alert" for r in recs
    )
    if has_spiral_tilt:
        _spiral_drop = {
            "fight", "numbers_adv", "gold_lead", "kill_lead",
            "power_spike",       # ult-up "play now" contradicts "do nothing"
            "lane_level_adv",    # lane-trade window irrelevant while spiraling
            "flash_down", "tp_down", "combat_spell_down",
            "jungler_down", "dragon_soul",
        }
        recs = [r for r in recs if r.kind not in _spiral_drop]

    return recs


def evaluate(
    snapshot: "LcdaSnapshot | None",
    *,
    rules: tuple = ALL_RULES,
    spell_tracker: "SpellTracker | None" = None,
    situational_build: object = None,
) -> list[Recommendation]:
    """Run every rule against ``snapshot`` and return the non-None
    results sorted by severity (alerts first). Pure function — safe
    to call on the LCDA-snapshot tick without any state.

    None snapshot → empty list (pre-game window). Rules that raise
    are silently skipped — a buggy rule must not break the engine.

    ``spell_tracker``: when provided, enables context-aware rules that
    require user-tracked summoner spell cooldowns (e.g. flash_down).

    ``situational_build``: a ``BuildResult`` from the build engine.
    When provided, fires ``rule_situational_build`` with item recs
    adjusted for the live enemy team composition.
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
    if spell_tracker is not None:
        for _spell_rule in (
            rule_enemy_flash_down,
            rule_enemy_tp_down,
            rule_enemy_combat_spell_down,
        ):
            try:
                rec = _spell_rule(snapshot, spell_tracker)
                if rec is not None:
                    out.append(rec)
            except Exception:  # noqa: BLE001
                pass
    if situational_build is not None:
        try:
            rec = rule_situational_build(snapshot, situational_build)
            if rec is not None:
                out.append(rec)
        except Exception:  # noqa: BLE001
            pass
    out.sort(key=lambda r: _SEVERITY_RANK.get(r.severity, 99))
    return _suppress_dominated(out)
