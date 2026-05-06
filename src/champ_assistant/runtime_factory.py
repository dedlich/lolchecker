"""Runtime construction — wires the args namespace to a live ChampAssistant.

Lifted out of ``__main__`` per OPTIMIZATION.md §3.3 so the entry-point
module isn't responsible for knowing how to assemble every subsystem.
The boot module imports ``build_assistant`` and is otherwise unaware
of how the LCU source / counter / profile services are constructed.
"""
from __future__ import annotations

import argparse

from champ_assistant.app import ChampAssistant
from champ_assistant.cli import DEFAULT_FIXTURE_DIR
from champ_assistant.data.loader import (
    DataLoadError,
    load_builds,
    load_counters,
    load_tags,
    load_tiers,
)
from champ_assistant.data.models import BuildLibrary, Champion
from champ_assistant.data.runtime_counters import RuntimeCounterStore
from champ_assistant.lcu.sources import FixtureLcuSource, LcuSource, RealLcuSource
from champ_assistant.ui.overlay import MainOverlay


# Bootstrap-only champion dict. The orchestrator needs SOME champion table
# at construction time, before the async DataDragon hydration in
# _hydrate_champions_and_icons replaces it with the live ~170-champion
# roster. This 30-champion list IS NOT the production source of truth —
# any session that runs past hydration sees the full DataDragon list.
#
# Hydration failure (offline + empty cache) falls back to this list and
# logs a loud warning — see the "DEGRADED" path in
# ``_hydrate_champions_and_icons``. State invariant: by the time the user
# is in champ-select, ``assistant.champions`` should have grown past
# this list. ``docs/OPTIMIZATION.md §1.4`` proposes routing everything
# through ``data.datadragon.load_champion_index()`` (sync API) and
# failing loud on empty cache; tracked there as future work.
_STARTER_CHAMPIONS: list[Champion] = [
    Champion(id=1, key="Annie", name="Annie", tags=["Mage"]),
    Champion(id=3, key="Galio", name="Galio", tags=["Tank", "Mage"]),
    Champion(id=7, key="LeBlanc", name="LeBlanc", tags=["Assassin", "Mage"]),
    Champion(id=16, key="Soraka", name="Soraka", tags=["Support"]),
    Champion(id=21, key="MissFortune", name="Miss Fortune", tags=["Marksman"]),
    Champion(id=22, key="Ashe", name="Ashe", tags=["Marksman", "Support"]),
    Champion(id=51, key="Caitlyn", name="Caitlyn", tags=["Marksman"]),
    Champion(id=53, key="Blitzcrank", name="Blitzcrank", tags=["Tank", "Fighter"]),
    Champion(id=60, key="Elise", name="Elise", tags=["Mage", "Fighter"]),
    Champion(id=64, key="Lee Sin", name="Lee Sin", tags=["Fighter", "Assassin"]),
    Champion(id=67, key="Vayne", name="Vayne", tags=["Marksman", "Assassin"]),
    Champion(id=76, key="Nidalee", name="Nidalee", tags=["Assassin", "Fighter"]),
    Champion(id=81, key="Ezreal", name="Ezreal", tags=["Marksman", "Mage"]),
    Champion(id=86, key="Garen", name="Garen", tags=["Fighter", "Tank"]),
    Champion(id=89, key="Leona", name="Leona", tags=["Tank", "Support"]),
    Champion(id=90, key="Malzahar", name="Malzahar", tags=["Mage", "Assassin"]),
    Champion(id=103, key="Ahri", name="Ahri", tags=["Mage", "Assassin"]),
    Champion(id=111, key="Nautilus", name="Nautilus", tags=["Tank", "Fighter"]),
    Champion(id=117, key="Lulu", name="Lulu", tags=["Support", "Mage"]),
    Champion(id=120, key="Hecarim", name="Hecarim", tags=["Fighter", "Tank"]),
    Champion(id=122, key="Darius", name="Darius", tags=["Fighter", "Tank"]),
    Champion(id=145, key="Kaisa", name="Kai'Sa", tags=["Marksman"]),
    Champion(id=157, key="Yasuo", name="Yasuo", tags=["Fighter", "Assassin"]),
    Champion(id=164, key="Camille", name="Camille", tags=["Fighter", "Assassin"]),
    Champion(id=222, key="Jinx", name="Jinx", tags=["Marksman"]),
    Champion(id=234, key="Viego", name="Viego", tags=["Fighter", "Assassin"]),
    Champion(id=412, key="Thresh", name="Thresh", tags=["Tank", "Support"]),
    Champion(id=711, key="Vex", name="Vex", tags=["Mage"]),
    Champion(id=875, key="Sett", name="Sett", tags=["Fighter", "Tank"]),
    Champion(id=897, key="KSante", name="K'Sante", tags=["Tank", "Fighter"]),
]


def _make_source(args: argparse.Namespace) -> LcuSource:
    if args.dry_run:
        fixture = args.fixture or DEFAULT_FIXTURE_DIR
        return FixtureLcuSource(
            fixture, cycle=args.cycle, stress=args.stress,
            interval=args.interval, rate=args.rate,
        )
    return RealLcuSource()


def _starter_champion_index() -> dict[int, Champion]:
    return {c.id: c for c in _STARTER_CHAMPIONS}


def _build_profile_service():  # type: ignore[no-untyped-def]
    """Construct a ProfileService from persisted keyring credentials."""
    from champ_assistant import secrets
    from champ_assistant.profiling import ProfileService, RiotApiClient

    api_key = secrets.riot_api_key()
    region = secrets.riot_region()
    client = RiotApiClient(api_key, region=region)
    return ProfileService(client)


def _build_assistant(args: argparse.Namespace, overlay: MainOverlay) -> ChampAssistant:
    # Builds are optional — older bundles may not ship builds.json. Default
    # to an empty BuildLibrary so PickCards render without runes/items.
    builds: BuildLibrary
    try:
        builds = load_builds(args.data_dir / "builds.json")
    except DataLoadError:
        builds = BuildLibrary()

    # Runtime counter fetching is opt-in via GROQ_API_KEY (free tier at
    # https://console.groq.com). Without a key the store is constructed
    # disabled and never makes a network call — falls back to seed data.
    cache_dir = args.data_dir.parent / "ddragon_cache" / "runtime_counters"
    from champ_assistant import secrets as _sec
    runtime_counters = RuntimeCounterStore(
        cache_dir,
        api_key=_sec.llm_api_key(),
        provider=_sec.llm_provider(),
    )

    # Enemy profiling — opt-in via Settings dialog (Riot API key persisted
    # in keyring). Disabled service falls through silently.
    profile_service = _build_profile_service()

    return ChampAssistant(
        source=_make_source(args),
        overlay=overlay,
        counters=load_counters(args.data_dir / "counters.json"),
        tiers=load_tiers(args.data_dir / "tiers.json"),
        tags=load_tags(args.data_dir / "tags.json"),
        champions=_starter_champion_index(),
        builds=builds,
        runtime_counters=runtime_counters,
        profile_service=profile_service,
    )
