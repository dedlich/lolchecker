"""Detect when the active player (or an enemy) just hit a power spike.

Inputs come from the LCDA ``activePlayer`` block — level + items count.
We compare the latest snapshot against the previous one and emit a
``PowerSpike`` for each freshly crossed threshold so the UI can show a
brief celebration without re-firing every tick.

``EnemySpike`` extends the same concept to tracked enemies: fires when
an enemy player crosses a legendary-item threshold (1st / 2nd / 3rd),
letting the engine surface a safety warning (e.g. "Jinx has 2 items").
"""
from __future__ import annotations

from dataclasses import dataclass

LEVEL_SPIKES: tuple[int, ...] = (6, 11, 16)
ITEM_SPIKES: tuple[int, ...] = (1, 2, 3)  # first / two / three core items

# Legendary item price floor — same threshold as for active player.
LEGENDARY_PRICE_FLOOR = 2300


@dataclass(frozen=True)
class PowerSpike:
    kind: str    # "level" or "items"
    value: int   # 6 / 11 / 16 / 1 / 2 / 3
    label: str   # human-friendly headline
    detail: str  # one-liner under the headline


def _label_for_level(level: int) -> tuple[str, str]:
    if level == 6:
        return "Ultimate is up", "Look for an all-in or roam to a side lane."
    if level == 11:
        return "Mid-game spike", "R rank-2 + first item — push the next objective."
    if level == 16:
        return "Late-game ult", "R rank-3 — pick fights you can win."
    return f"Level {level}", ""


def _label_for_items(count: int) -> tuple[str, str]:
    if count == 1:
        return "First item online", "You out-trade most lane opponents now."
    if count == 2:
        return "Two-item spike", "Strongest mid-game window — group, not farm."
    if count == 3:
        return "Three items", "Find a Baron call or close the side lane."
    return f"{count} items", ""


def detect_spikes(
    *,
    prev_level: int,
    new_level: int,
    prev_items: int,
    new_items: int,
) -> list[PowerSpike]:
    """Return spikes crossed between the two snapshots."""
    spikes: list[PowerSpike] = []
    for threshold in LEVEL_SPIKES:
        if prev_level < threshold <= new_level:
            label, detail = _label_for_level(threshold)
            spikes.append(PowerSpike("level", threshold, label, detail))
    for threshold in ITEM_SPIKES:
        if prev_items < threshold <= new_items:
            label, detail = _label_for_items(threshold)
            spikes.append(PowerSpike("items", threshold, label, detail))
    return spikes


@dataclass(frozen=True)
class EnemySpike:
    """An enemy player just completed their Nth legendary item."""
    champion_name: str
    legendary_count: int   # 1 / 2 / 3 (threshold just crossed)


def count_legendaries(items: list[dict]) -> int:
    """Count completed legendary items (price ≥ LEGENDARY_PRICE_FLOOR)."""
    count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        price = item.get("price") or 0
        if isinstance(price, (int, float)) and price >= LEGENDARY_PRICE_FLOOR:
            count += 1
    return count


def detect_enemy_spikes(
    prev_counts: dict[str, int],
    players: list[dict],
) -> tuple[list[EnemySpike], dict[str, int]]:
    """Compare enemy player legendary counts against the previous tick.

    Returns ``(spikes, new_counts)``.
    ``prev_counts`` maps champion_name → previous legendary count.
    ``new_counts`` should replace ``prev_counts`` in the caller.
    Spikes fire only when the count crosses a tracked threshold (1 / 2 / 3).
    """
    spikes: list[EnemySpike] = []
    new_counts: dict[str, int] = {}
    for entry in players:
        name = str(entry.get("championName") or "")
        if not name:
            continue
        items = entry.get("items") or []
        new_count = count_legendaries(items)
        new_counts[name] = new_count
        prev = prev_counts.get(name, 0)
        for threshold in (1, 2, 3):
            if prev < threshold <= new_count:
                spikes.append(EnemySpike(champion_name=name, legendary_count=threshold))
    return spikes, new_counts


def extract_active_state(active_player: dict | None) -> tuple[int, int]:
    """Pull (level, completed-item-count) from LCDA's activePlayer block.

    LCDA exposes ``level`` and ``items`` (a list of dicts). For power-spike
    purposes we count *legendary*-tier completed items only — Boots and
    components don't qualify. LCDA marks legendaries with rawDescription
    starting with ``GeneratedTip_Item_<id>_``; cheaper to use ``price`` >
    2400 as a coarse heuristic when the field is present.
    """
    if not active_player:
        return 0, 0
    level = int(active_player.get("level") or 0)
    items = active_player.get("items") or []
    legendary_count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        price = item.get("price") or 0
        # Boots are usually 1100 gold base. Legendaries start ~2500+.
        # A few mythics/legendary supports cost ~2300, so use 2300 as floor.
        if isinstance(price, (int, float)) and price >= 2300:
            legendary_count += 1
    return level, legendary_count
