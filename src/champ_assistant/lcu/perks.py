"""Apply a recommended rune setup to the user's League client via LCU.

Endpoints used (require an open LeagueClient):
  GET    /lol-perks/v1/pages         list current pages
  GET    /lol-perks/v1/currentpage   currently active page
  POST   /lol-perks/v1/pages         create a new page
  DELETE /lol-perks/v1/pages/{id}    remove a page
  PUT    /lol-perks/v1/currentpage   activate a page

Strategy:
  1. Look for an existing "Champ Assistant" page; delete it (Riot caps
     custom pages so we replace rather than accumulate).
  2. Build a fresh page from the seed-build's runes:
     - Keystone determines the primary style.
     - First sub-style rune determines the sub style; others fill the
       9-perk array in order.
     - Stat shards default to a sensible balanced trio.
  3. POST it, then PUT /currentpage so it's actively selected.

Failure modes:
  - LCU not running → LcuClientError surfaces; caller shows a hint.
  - Unknown rune name → silently skipped (page applies what it can).
  - Page cap reached (Riot allows 5–9 depending on account) → delete
    the oldest "Champ Assistant" page first.
"""
from __future__ import annotations

import logging
from typing import Any

from ..data.perks_data import (
    PERK_IDS,
    STYLE_DOMINATION,
    STYLE_INSPIRATION,
    STYLE_PRECISION,
    STYLE_RESOLVE,
    STYLE_SORCERY,
    perk_ids_for,
    resolve_keystone,
)
from .client import LcuClient, LcuClientError

# Per-tree fallback perk IDs used when the build doesn't provide enough
# perks to fill all required slots. IDs chosen from bottom-row generics
# that are safe to slot in any position within their tree.
_TREE_FALLBACKS: dict[int, list[int]] = {
    STYLE_PRECISION:   [8014, 8017, 8299],   # Coup de Grace / Cut Down / Last Stand
    STYLE_DOMINATION:  [8105, 8106, 8134],   # Relentless / Ultimate / Ingenious Hunter
    STYLE_SORCERY:     [8232, 8236, 8237],   # Waterwalking / Gathering Storm / Scorch
    STYLE_RESOLVE:     [8444, 8453, 8242],   # Second Wind / Revitalize / Unflinching
    STYLE_INSPIRATION: [8345, 8347, 8410],   # Biscuit Delivery / Cosmic Insight / Approach Velocity
}

logger = logging.getLogger(__name__)

PAGE_NAME_PREFIX = "Champ Assistant"


def _split_runes(rune_names: list[str]) -> tuple[list[int], list[int]]:
    """Partition the seed runes into (primary_perks, sub_perks).
    The first rune is the keystone; the next three are primary; the
    remaining two are sub-style. Real builds.json entries are exactly
    six runes long, but we tolerate fewer."""
    primary = perk_ids_for(rune_names[:4])
    sub = perk_ids_for(rune_names[4:])
    return primary, sub


def build_page_payload(
    *,
    champion_key: str,
    rune_names: list[str],
) -> dict[str, Any] | None:
    """Render a complete LCU page payload from a builds.json entry.
    Returns None when the keystone can't be resolved — without it the
    page can't be valid so we abort early."""
    keystone = resolve_keystone(rune_names)
    if keystone is None:
        return None
    keystone_name, primary_style = keystone

    primary, sub = _split_runes(rune_names)
    # Ensure the keystone itself is the first selected perk.
    if primary and primary[0] != PERK_IDS[keystone_name]:
        primary = [PERK_IDS[keystone_name]] + [
            p for p in primary if p != PERK_IDS[keystone_name]
        ]
    elif not primary:
        primary = [PERK_IDS[keystone_name]]

    # Pick the sub-style by mapping the first sub-rune back to its tree.
    perk_to_style = {
        # rough heuristic: first digit of the perk id maps to its tree
        81: STYLE_PRECISION,    # 8005..
        82: STYLE_DOMINATION,   # 8112.. (leading 81 too — handled below)
    }
    # Actually a cleaner mapping: walk the perk ids and decide tree by
    # numeric range. IDs starting with 80/81/82/83/84 map to the 8xxx
    # series style for that tree.
    sub_style = primary_style
    for p in sub:
        if 8000 <= p < 8100:
            sub_style = STYLE_PRECISION; break
        if 8100 <= p < 8200 or p in (9923, 9101, 9111, 9104, 9105, 9103):
            sub_style = STYLE_DOMINATION; break
        if 8200 <= p < 8300:
            sub_style = STYLE_SORCERY; break
        if 8300 <= p < 8400:
            sub_style = STYLE_INSPIRATION; break
        if 8400 <= p < 8500:
            sub_style = STYLE_RESOLVE; break
    # Don't double up styles.
    if sub_style == primary_style:
        # Fall back to a sensible complementary tree.
        sub_style = {
            STYLE_PRECISION:   STYLE_DOMINATION,
            STYLE_DOMINATION:  STYLE_PRECISION,
            STYLE_SORCERY:     STYLE_INSPIRATION,
            STYLE_RESOLVE:     STYLE_PRECISION,
            STYLE_INSPIRATION: STYLE_SORCERY,
        }[primary_style]

    # The selectedPerkIds array is 9 entries: 4 primary, 2 sub, 3 shards.
    # LCU rejects any entry with ID 0 — pad missing slots with known-valid
    # fallback perks from the appropriate tree instead.
    primary_fallbacks = [p for p in _TREE_FALLBACKS.get(primary_style, [])
                         if p not in primary]
    sub_fallbacks = [p for p in _TREE_FALLBACKS.get(sub_style, [])
                     if p not in sub]

    selected = primary[:4]
    while len(selected) < 4 and primary_fallbacks:
        selected.append(primary_fallbacks.pop(0))

    selected += sub[:2]
    while len(selected) < 6 and sub_fallbacks:
        selected.append(sub_fallbacks.pop(0))

    if len(selected) < 6:
        logger.warning(
            "build_page_payload: not enough valid runes (got %d/6) — "
            "page may be rejected by LCU",
            len(selected),
        )

    # Sensible balanced shards: Adaptive / Adaptive / Health
    selected += [PERK_IDS["Adaptive Force"], PERK_IDS["Adaptive Force"],
                 PERK_IDS["Health"]]

    return {
        "name": f"{PAGE_NAME_PREFIX}: {champion_key}",
        "primaryStyleId": primary_style,
        "subStyleId": sub_style,
        "selectedPerkIds": selected,
        "current": True,
    }


async def apply_rune_page(
    client: LcuClient,
    *,
    champion_key: str,
    rune_names: list[str],
) -> dict[str, Any] | None:
    """Replace any prior Champ Assistant page with one for ``champion_key``,
    activate it. Returns the created page dict on success, None on no-op
    (e.g. unknown keystone). Raises LcuClientError on transport failure."""
    payload = build_page_payload(
        champion_key=champion_key, rune_names=rune_names,
    )
    if payload is None:
        logger.info("apply_rune_page: keystone unrecognised — skipping")
        return None

    # Remove any prior pages we own so we don't pile them up.
    pages = await _list_pages(client)
    for page in pages:
        name = page.get("name") or ""
        if isinstance(name, str) and name.startswith(PAGE_NAME_PREFIX):
            page_id = page.get("id")
            if page_id is not None:
                try:
                    await client.delete(f"/lol-perks/v1/pages/{page_id}")
                except LcuClientError as exc:
                    logger.info("apply_rune_page: delete-old failed: %s", exc)

    # Create the fresh page; LCU activates it on creation when ``current``
    # is true.
    logger.debug("apply_rune_page: POST payload=%r", payload)
    response = await client.post("/lol-perks/v1/pages", json=payload)
    if response.is_error:
        logger.error(
            "apply_rune_page: LCU returned %d — body: %s",
            response.status_code, response.text,
        )
    response.raise_for_status()
    created = response.json()
    logger.info(
        "apply_rune_page created id=%s name=%r",
        created.get("id"), created.get("name"),
    )
    return created


async def _list_pages(client: LcuClient) -> list[dict[str, Any]]:
    try:
        response = await client.get("/lol-perks/v1/pages")
    except LcuClientError:
        return []
    try:
        data = response.json()
    except ValueError:
        return []
    return list(data) if isinstance(data, list) else []
