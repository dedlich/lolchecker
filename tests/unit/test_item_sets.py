"""Tests for the LCU item-set payload builder."""
from __future__ import annotations

from champ_assistant.lcu.item_sets import build_item_set


def test_returns_none_for_unknown_items() -> None:
    payload = build_item_set(
        champion_key="X", champion_id=1,
        item_names=["TotallyMadeUpItem"],
    )
    assert payload is None


def test_categorizes_into_starting_boots_legendary() -> None:
    payload = build_item_set(
        champion_key="Garen", champion_id=86,
        item_names=[
            "Stridebreaker",       # legendary
            "Plated Steelcaps",    # boots
            "Sundered Sky",        # legendary
            "Black Cleaver",       # legendary
        ],
    )
    assert payload is not None
    assert payload["title"] == "Champ Assistant: Garen"
    assert payload["associatedChampions"] == [86]
    titles = {b["type"] for b in payload["blocks"]}
    assert "Core Build" in titles
    assert "Boots" in titles
    # Three legendaries in Core Build, one boots
    core = next(b for b in payload["blocks"] if b["type"] == "Core Build")
    assert len(core["items"]) == 3
    boots = next(b for b in payload["blocks"] if b["type"] == "Boots")
    assert len(boots["items"]) == 1


def test_unique_uid_per_call() -> None:
    a = build_item_set(
        champion_key="Garen", champion_id=86,
        item_names=["Stridebreaker"],
    )
    import time
    time.sleep(1.01)  # uid carries epoch seconds
    b = build_item_set(
        champion_key="Garen", champion_id=86,
        item_names=["Stridebreaker"],
    )
    assert a["uid"] != b["uid"]


def test_item_ids_are_strings_for_lcu() -> None:
    """LCU expects item ids as strings inside the item-set payload."""
    payload = build_item_set(
        champion_key="X", champion_id=1,
        item_names=["Sundered Sky"],
    )
    assert payload is not None
    for block in payload["blocks"]:
        for item in block["items"]:
            assert isinstance(item["id"], str)
            assert item["count"] == 1


def test_associated_champions_omitted_when_id_zero() -> None:
    """Some advisor flows don't have a champion id (e.g. fallback) —
    the LCU set should still be valid."""
    payload = build_item_set(
        champion_key="X", champion_id=0,
        item_names=["Stridebreaker"],
    )
    assert payload is not None
    assert payload["associatedChampions"] == []
