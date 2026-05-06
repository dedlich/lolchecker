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

def _active_player(snapshot: "LcdaSnapshot") -> object | None:
    """Return the active player's LivePlayer record from the allies list."""
    name = getattr(snapshot, "active_summoner", "") or ""
    if not name:
        return None
    for p in (getattr(snapshot, "allies", []) or []):
        if getattr(p, "summoner_name", "") == name:
            return p
    return None


# Bounty tiers — Riot's announcer terms anchor the bounty rules' messages.
# 3+ unanswered kills → +150g (Killing Spree); 5+ → +200-300g (Unstoppable);
# 7+ → +400-500g (Godlike). Multiple rules across bounty / combat domains
# reference these tiers, so they live here.
BOUNTY_TIER_INFO_S: int = 3      # Killing Spree
BOUNTY_TIER_WARN_S: int = 5      # Unstoppable
BOUNTY_TIER_GODLIKE_S: int = 7   # Godlike+


def _team_id_set(players: list) -> set[str]:
    """Build the set of identifiers for a team (summoner_name + champion_name
    of every member). Used by rules that decide which side caused an event."""
    ids: set[str] = set()
    for p in players:
        sn = str(getattr(p, "summoner_name", "") or "")
        cn = str(getattr(p, "champion_name", "") or "")
        if sn:
            ids.add(sn)
        if cn:
            ids.add(cn)
    return ids


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

