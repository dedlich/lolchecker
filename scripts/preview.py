"""Render the overlay with realistic mock data and save a screenshot.

Usage: ``.venv/bin/python scripts/preview.py [output.png]``

Defaults to /tmp/champ-assistant-preview.png. Used to capture UI snapshots
without booting the full LCU + LCDA pipeline.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from champ_assistant.advisor.composition import CompositionGap  # noqa: E402
from champ_assistant.advisor.picks import PickSuggestion  # noqa: E402
from champ_assistant.data.models import (  # noqa: E402
    ChampionBuild,
    ChampSelectSession,
    CounterEntry,
    TeamMember,
)
from champ_assistant.lcda.objectives import ObjectiveTimer  # noqa: E402
from champ_assistant.lcda.players import (  # noqa: E402
    LivePlayer,
    LiveSummonerSpell,
)
from champ_assistant.lcda.source import LcdaSnapshot  # noqa: E402
from champ_assistant.ui.overlay import MainOverlay  # noqa: E402
from champ_assistant.ui.view_model import SessionView  # noqa: E402

DDRAGON_PATCH = "14.24.1"


def _fetch_icon(url: str) -> QPixmap | None:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = r.read()
    except Exception:
        return None
    pm = QPixmap()
    if not pm.loadFromData(data):
        return None
    return pm.scaled(
        32, 32,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _champion_icons(keys: list[str]) -> dict[str, QPixmap]:
    out: dict[str, QPixmap] = {}
    for key in keys:
        pm = _fetch_icon(
            f"https://ddragon.leagueoflegends.com/cdn/{DDRAGON_PATCH}"
            f"/img/champion/{key}.png"
        )
        if pm is not None:
            out[key] = pm
    return out


def _spell_icons(names_to_files: dict[str, str]) -> dict[str, QPixmap]:
    out: dict[str, QPixmap] = {}
    for name, file in names_to_files.items():
        pm = _fetch_icon(
            f"https://ddragon.leagueoflegends.com/cdn/{DDRAGON_PATCH}"
            f"/img/spell/{file}"
        )
        if pm is not None:
            out[name] = pm
    return out


def _build_session() -> ChampSelectSession:
    """Five enemies locked in, our four teammates picked, our slot still open."""
    enemies = [
        TeamMember(cellId=5, championId=122),  # Darius TOP
        TeamMember(cellId=6, championId=64),   # Lee Sin JG
        TeamMember(cellId=7, championId=103),  # Ahri MID
        TeamMember(cellId=8, championId=51),   # Caitlyn BOT
        TeamMember(cellId=9, championId=412),  # Thresh SUP
    ]
    teammates = [
        TeamMember(cellId=0, championId=86),   # Garen
        TeamMember(cellId=1, championId=60),   # Elise
        TeamMember(cellId=2, championId=0),    # me — empty slot
        TeamMember(cellId=3, championId=22),   # Ashe
        TeamMember(cellId=4, championId=89),   # Leona
    ]
    return ChampSelectSession(
        phase="FINALIZATION",
        localPlayerCellId=2,
        myTeam=teammates,
        theirTeam=enemies,
    )


def _build_view() -> SessionView:
    enemy_names = {
        122: "Darius", 64: "Lee Sin", 103: "Ahri", 51: "Caitlyn", 412: "Thresh",
    }
    enemy_keys = {
        122: "Darius", 64: "LeeSin", 103: "Ahri", 51: "Caitlyn", 412: "Thresh",
    }
    enemy_roles = {5: "TOP", 6: "JUNGLE", 7: "MID", 8: "BOT", 9: "SUPPORT"}
    enemy_counters = {
        7: [  # against Ahri MID
            CounterEntry(champion="Kassadin", score=7.5, tier="S"),
            CounterEntry(champion="Fizz", score=7.0, tier="S"),
            CounterEntry(champion="Yasuo", score=6.5, tier="A"),
        ],
    }
    suggestions = [
        PickSuggestion(
            champion_key="Kassadin", score=78.0, tier="S",
            reasons=["S tier in MID", "Strong vs Ahri (7.5)"],
        ),
        PickSuggestion(
            champion_key="Fizz", score=72.0, tier="S",
            reasons=["S tier in MID", "Strong vs Ahri (7.0)"],
        ),
        PickSuggestion(
            champion_key="Yasuo", score=64.5, tier="A",
            reasons=["A tier in MID", "Counters Ahri"],
        ),
    ]
    suggestion_builds = {
        "Kassadin": ChampionBuild(
            runes=["Electrocute", "Sudden Impact", "Eyeball Collection",
                   "Ultimate Hunter", "Manaflow Band", "Scorch"],
            items=["Rod of Ages", "Sorcerer's Shoes", "Lich Bane",
                   "Rabadon's Deathcap"],
            summoners=["Flash", "Teleport"],
        ),
    }
    gaps = [
        CompositionGap(
            category="frontline",
            severity="important",
            description="Only one tank — consider Leona swap",
        ),
    ]
    return SessionView(
        connection_state="connected",
        session=_build_session(),
        enemy_counters=enemy_counters,
        suggestions=suggestions,
        gaps=gaps,
        enemy_names=enemy_names,
        enemy_keys=enemy_keys,
        enemy_roles=enemy_roles,
        suggestion_builds=suggestion_builds,
    )


def _build_lcda_snapshot() -> LcdaSnapshot:
    enemies = [
        LivePlayer("EnemyTop", "Darius", "CHAOS",
                   LiveSummonerSpell("Flash", 300.0),
                   LiveSummonerSpell("Ghost", 210.0)),
        LivePlayer("EnemyJg", "Lee Sin", "CHAOS",
                   LiveSummonerSpell("Flash", 300.0),
                   LiveSummonerSpell("Smite", 90.0)),
        LivePlayer("EnemyMid", "Ahri", "CHAOS",
                   LiveSummonerSpell("Flash", 300.0),
                   LiveSummonerSpell("Ignite", 180.0)),
        LivePlayer("EnemyBot", "Caitlyn", "CHAOS",
                   LiveSummonerSpell("Flash", 300.0),
                   LiveSummonerSpell("Heal", 240.0)),
        LivePlayer("EnemySup", "Thresh", "CHAOS",
                   LiveSummonerSpell("Flash", 300.0),
                   LiveSummonerSpell("Exhaust", 210.0)),
    ]
    objectives = [
        ObjectiveTimer(name="Dragon", next_spawn_seconds=1080.0,
                       last_killed_seconds=780.0, last_killer="Kindred",
                       detail="Cloud"),
        ObjectiveTimer(name="Baron", next_spawn_seconds=1500.0,
                       last_killed_seconds=None),
        ObjectiveTimer(name="Herald", next_spawn_seconds=None,
                       last_killed_seconds=920.0, last_killer="EnemyJg"),
    ]
    return LcdaSnapshot(
        game_time=970.0,  # 16:10
        game_mode="CLASSIC",
        objectives=objectives,
        enemies=enemies,
        active_summoner="Me",
        raw_events=[],
    )


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/champ-assistant-preview.png")

    app = QApplication.instance() or QApplication(sys.argv[:1])
    overlay = MainOverlay()

    # Champ-select content
    view = _build_view()
    champ_icons = _champion_icons(
        ["Darius", "LeeSin", "Ahri", "Caitlyn", "Thresh",
         "Garen", "Elise", "Ashe", "Leona",
         "Kassadin", "Fizz", "Yasuo"]
    )
    overlay.set_champion_icons(champ_icons)
    overlay.update_view(view)

    # In-game content
    snap = _build_lcda_snapshot()
    overlay.summoner_tracker.set_champion_icons({
        "Darius": champ_icons.get("Darius", QPixmap()),
        "Lee Sin": champ_icons.get("LeeSin", QPixmap()),
        "Ahri": champ_icons.get("Ahri", QPixmap()),
        "Caitlyn": champ_icons.get("Caitlyn", QPixmap()),
        "Thresh": champ_icons.get("Thresh", QPixmap()),
    })
    overlay.summoner_tracker.set_spell_icons(_spell_icons({
        "Flash": "SummonerFlash.png",
        "Ignite": "SummonerDot.png",
        "Heal": "SummonerHeal.png",
        "Smite": "SummonerSmite.png",
        "Ghost": "SummonerHaste.png",
        "Exhaust": "SummonerExhaust.png",
        "Teleport": "SummonerTeleport.png",
        "Cleanse": "SummonerBoost.png",
        "Barrier": "SummonerBarrier.png",
    }))
    overlay.update_lcda_snapshot(snap)

    # Pre-tick a couple of cooldowns so the timer color states are visible.
    tracker = overlay.summoner_tracker.tracker()
    tracker.mark_used("EnemyTop", "Flash", 300.0, snap.game_time - 240)   # ~60s left → blue
    tracker.mark_used("EnemyMid", "Ignite", 180.0, snap.game_time - 30)   # 150s left → red
    tracker.mark_used("EnemyBot", "Heal", 240.0, snap.game_time - 200)    # 40s left → orange
    overlay.update_lcda_snapshot(snap)  # rerender with cooldowns

    # Add fake profile data to the enemy rows for the preview
    from champ_assistant.profiling.profile import EnemyProfile, RankBadge, TopChampion
    profiles = {
        122: EnemyProfile("EnemyTop", level=540,
                          top_champions=[TopChampion(122, 580_000, 7),
                                         TopChampion(86, 200_000, 6),
                                         TopChampion(75, 180_000, 6)],
                          wins=11, losses=4, streak=3,
                          rank=RankBadge(tier="DIAMOND", division="II",
                                         league_points=24, wins=82, losses=70)),
        64: EnemyProfile("EnemyJg", level=320,
                         top_champions=[TopChampion(64, 350_000, 7),
                                        TopChampion(60, 180_000, 6)],
                         wins=6, losses=8, streak=-1,
                         rank=RankBadge(tier="EMERALD", division="III",
                                        league_points=42, wins=15, losses=18)),
        103: EnemyProfile("EnemyMid", level=410,
                          top_champions=[TopChampion(103, 420_000, 7),
                                         TopChampion(7, 150_000, 6)],
                          wins=4, losses=9, streak=-4,  # tilt
                          rank=RankBadge(tier="PLATINUM", division="I",
                                         league_points=88, wins=31, losses=29)),
    }
    enemy_champ_names = {
        122: "Darius", 86: "Garen", 75: "Nasus",
        64: "Lee Sin", 60: "Elise",
        103: "Ahri", 7: "LeBlanc",
    }
    for row in overlay.enemy_rows:
        cell_id = row._cell_id  # accessing private — preview only
        member_champ_id = next(
            (m.champion_id for m in view.session.their_team if m.cell_id == cell_id),
            None,
        )
        if member_champ_id in profiles:
            row.set_profile(profiles[member_champ_id], champion_names=enemy_champ_names)

    # Force in-game phase visibility off so champ-select panels remain shown for preview
    overlay.set_phase_visibility(in_champ_select=True, in_game=False)

    # Show power-spike panel by sending one with a fresh spike
    from champ_assistant.lcda.power_spikes import PowerSpike
    spike_snap = LcdaSnapshot(
        game_time=snap.game_time, game_mode="CLASSIC",
        objectives=snap.objectives, enemies=snap.enemies,
        active_summoner=snap.active_summoner, raw_events=[],
        active_level=11, active_items=2,
        new_spikes=[PowerSpike("level", 11, "Mid-game spike",
                               "R rank-2 + first item — push the next objective.")],
    )
    overlay.power_spike_panel.update_snapshot(spike_snap)

    overlay.status_bar.show_update_available(
        "v0.10.0",
        on_click=lambda: None,
    )

    overlay.resize(660, 880)
    overlay.show()

    app.processEvents()
    app.processEvents()  # second tick — let pixmap loads finalize

    pix = overlay.grab()
    out.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out))
    print(f"saved: {out}  ({pix.width()}x{pix.height()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
