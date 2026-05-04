"""Push a recommended item build into the user's League client as a
custom item set, visible from the in-game shop's "Item Sets" tab.

LCU endpoints used:
  GET  /lol-summoner/v1/current-summoner       -> {summonerId, accountId}
  GET  /lol-item-sets/v1/item-sets/{summonerId}/sets   list custom sets
  PUT  /lol-item-sets/v1/item-sets/{summonerId}/sets   replace ALL sets

The PUT verb is unusual — Riot doesn't expose "POST a single set", you
have to send the entire collection in one request. So we read the
existing sets, drop any prior "Champ Assistant" ones, append a fresh
one, and PUT the whole list back.

Set structure (one block per build phase):
  Starting | Core | Boots | Situational

Names not in our items_data table are silently skipped — League shows
empty slots for the gaps which the user can fill manually.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..data.items_data import ITEM_IDS, item_ids_for
from .client import LcuClient, LcuClientError

logger = logging.getLogger(__name__)

SET_TITLE_PREFIX = "Champ Assistant"

# Legendary items typically cost ≥ 2400g; everything else is component/boots.
LEGENDARY_GOLD_FLOOR = 2400


async def current_summoner(client: LcuClient) -> dict[str, Any] | None:
    """Look up the active user's summonerId + accountId via LCU."""
    try:
        response = await client.get("/lol-summoner/v1/current-summoner")
        response.raise_for_status()
        return response.json()
    except (LcuClientError, ValueError) as exc:
        logger.info("current_summoner_failed: %s", exc)
        return None


def _block(title: str, item_ids: list[int]) -> dict[str, Any]:
    """One section in the in-game item-set side panel."""
    return {
        "type": title,
        "items": [{"id": str(iid), "count": 1} for iid in item_ids],
    }


def build_item_set(
    *,
    champion_key: str,
    champion_id: int,
    item_names: list[str],
) -> dict[str, Any] | None:
    """Translate a builds.json item list into an LCU item-set payload."""
    ids = item_ids_for(item_names)
    if not ids:
        return None

    # Split into starting/boots/legendaries by ID heuristic. Boots have IDs
    # in the 3006/3009/3020/3047/3111/3117/3158 range — easier to detect
    # by name than by gold value.
    boot_keywords = (
        "Greaves", "Boots", "Treads", "Steelcaps", "Shoes", "Lucidity",
    )
    starting_kw = ("Doran", "Tear", "Cull", "Long Sword", "Dagger")

    starting: list[int] = []
    boots: list[int] = []
    legendaries: list[int] = []

    for name in item_names:
        if name not in ITEM_IDS:
            continue
        iid = ITEM_IDS[name]
        if any(kw in name for kw in boot_keywords):
            boots.append(iid)
        elif any(kw in name for kw in starting_kw):
            starting.append(iid)
        else:
            legendaries.append(iid)

    blocks = []
    if starting:
        blocks.append(_block("Starting Items", starting))
    if legendaries:
        blocks.append(_block("Core Build", legendaries))
    if boots:
        blocks.append(_block("Boots", boots))
    if not blocks:
        # Fallback: one big block with everything in original order.
        blocks.append(_block("Build", ids))

    return {
        "title": f"{SET_TITLE_PREFIX}: {champion_key}",
        "type": "custom",
        "associatedChampions": [champion_id] if champion_id > 0 else [],
        "associatedMaps": [],
        "blocks": blocks,
        "uid": f"champ-assistant-{champion_key}-{int(time.time())}",
        "sortrank": 1,
        "startedFrom": "blank",
        "preferredItemSlots": [],
    }


def _resolve_id(item_id: int, item_name: str) -> int:
    """Return item_id when valid, or fall back to ITEM_IDS name lookup."""
    if item_id:
        return item_id
    return ITEM_IDS.get(item_name, 0)


def build_item_set_from_result(
    *,
    champion_key: str,
    champion_id: int,
    build_result: object,
) -> dict[str, Any] | None:
    """Build an LCU item-set payload from a ``BuildResult`` produced by
    the Meraki-based build engine. Falls back to ITEM_IDS name lookup when
    item_id is 0. Returns None when the result has no scoreable items."""
    from ..advisor.build_engine import BuildResult
    if not isinstance(build_result, BuildResult):
        return None

    blocks: list[dict[str, Any]] = []

    starter_id = build_result.starter_id or 0
    boots_id = build_result.boots_id or 0
    if not starter_id and build_result.starter_name:
        starter_id = ITEM_IDS.get(build_result.starter_name, 0)
    if not boots_id and build_result.boots_name:
        boots_id = ITEM_IDS.get(build_result.boots_name, 0)

    if starter_id:
        blocks.append(_block("Starting Items", [starter_id]))

    # Build Order: exactly 6 item slots (boots counts as one slot).
    # With boots: item1 → boots → items 2-5 = 6 total.
    # Without boots (Cassiopeia): all 6 core items.
    core_list = list(build_result.core_items)
    ordered_ids: list[int] = []
    if boots_id:
        slot_items = core_list[:5]  # 5 items + boots = 6 slots
        if slot_items:
            rid = _resolve_id(slot_items[0].item_id, slot_items[0].item_name)
            if rid:
                ordered_ids.append(rid)
        ordered_ids.append(boots_id)
        ordered_ids.extend(
            rid for s in slot_items[1:]
            for rid in [_resolve_id(s.item_id, s.item_name)] if rid
        )
    else:
        ordered_ids.extend(
            rid for s in core_list[:6]
            for rid in [_resolve_id(s.item_id, s.item_name)] if rid
        )

    if ordered_ids:
        blocks.append(_block("Build Order", ordered_ids))

    sit_ids = [
        rid for s in build_result.situational_items
        for rid in [_resolve_id(s.item_id, s.item_name)] if rid
    ]
    if sit_ids:
        blocks.append(_block("Situational", sit_ids))

    if not blocks:
        logger.error(
            "build_item_set_from_result_empty champion=%s — no resolvable item IDs "
            "(core=%d sit=%d boots_id=%s starter_id=%s)",
            champion_key, len(core_list), len(build_result.situational_items),
            boots_id, starter_id,
        )
        return None

    return {
        "title": f"{SET_TITLE_PREFIX}: {champion_key}",
        "type": "custom",
        "associatedChampions": [champion_id] if champion_id > 0 else [],
        "associatedMaps": [],
        "blocks": blocks,
        "uid": f"champ-assistant-{champion_key}-{int(time.time())}",
        "sortrank": 1,
        "startedFrom": "blank",
        "preferredItemSlots": [],
    }


async def apply_item_set_from_result(
    client: LcuClient,
    *,
    champion_key: str,
    champion_id: int,
    build_result: object,
) -> dict[str, Any] | None:
    """Push a Meraki-derived BuildResult as an LCU item set (blueprint).

    Replaces any prior Champ Assistant sets for this champion with four
    blocks: Starting / Core Build / Boots / Situational. Returns the
    written set on success, None when nothing was written.
    """
    new_set = build_item_set_from_result(
        champion_key=champion_key,
        champion_id=champion_id,
        build_result=build_result,
    )
    if new_set is None:
        return None

    summoner = await current_summoner(client)
    if summoner is None:
        raise LcuClientError("current summoner unknown")
    summoner_id = summoner.get("summonerId")
    account_id = summoner.get("accountId")
    if not summoner_id:
        raise LcuClientError("current summoner has no summonerId")

    existing_payload: dict[str, Any] = {"accountId": account_id, "itemSets": []}
    try:
        response = await client.get(
            f"/lol-item-sets/v1/item-sets/{summoner_id}/sets"
        )
        response.raise_for_status()
        existing_payload = response.json() or existing_payload
    except (LcuClientError, ValueError) as exc:
        logger.info("read_item_sets_failed: %s — starting from scratch", exc)

    sets = list(existing_payload.get("itemSets") or [])
    sets = [
        s for s in sets
        if not (
            isinstance(s, dict)
            and isinstance(s.get("title"), str)
            and s["title"].startswith(SET_TITLE_PREFIX)
        )
    ]
    sets.append(new_set)

    put_response = await client.request(
        "PUT",
        f"/lol-item-sets/v1/item-sets/{summoner_id}/sets",
        json={"accountId": account_id, "itemSets": sets},
    )
    put_response.raise_for_status()
    logger.info(
        "apply_item_set_from_result written summoner=%s title=%r blocks=%d",
        summoner_id, new_set["title"], len(new_set["blocks"]),
    )
    return new_set


async def apply_item_set(
    client: LcuClient,
    *,
    champion_key: str,
    champion_id: int,
    item_names: list[str],
) -> dict[str, Any] | None:
    """Replace prior Champ Assistant item sets with a fresh one for the
    given champion. Returns the appended set on success, None on no-op."""
    summoner = await current_summoner(client)
    if summoner is None:
        raise LcuClientError("current summoner unknown")
    summoner_id = summoner.get("summonerId")
    account_id = summoner.get("accountId")
    if not summoner_id:
        raise LcuClientError("current summoner has no summonerId")

    new_set = build_item_set(
        champion_key=champion_key,
        champion_id=champion_id,
        item_names=item_names,
    )
    if new_set is None:
        return None

    # Read existing sets so we don't blow them away. /sets returns
    # {accountId, itemSets: [...]}.
    existing_payload: dict[str, Any] = {"accountId": account_id, "itemSets": []}
    try:
        response = await client.get(
            f"/lol-item-sets/v1/item-sets/{summoner_id}/sets"
        )
        response.raise_for_status()
        existing_payload = response.json() or existing_payload
    except (LcuClientError, ValueError) as exc:
        logger.info("read_item_sets_failed: %s — starting from scratch", exc)

    sets = list(existing_payload.get("itemSets") or [])
    # Drop our prior sets so we don't pile them up.
    sets = [
        s for s in sets
        if not (
            isinstance(s, dict)
            and isinstance(s.get("title"), str)
            and s["title"].startswith(SET_TITLE_PREFIX)
        )
    ]
    sets.append(new_set)

    put_response = await client.request(
        "PUT",
        f"/lol-item-sets/v1/item-sets/{summoner_id}/sets",
        json={"accountId": account_id, "itemSets": sets},
    )
    put_response.raise_for_status()
    logger.info(
        "apply_item_set written summoner=%s title=%r",
        summoner_id, new_set["title"],
    )
    return new_set
