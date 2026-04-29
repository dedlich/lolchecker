"""Vision-based camp detection (Stage A — color heuristics).

This subsystem is opt-in via Settings → Experimental. When enabled and
on Windows, it captures small pixel regions of the in-game minimap,
runs a deterministic HSV color-presence check, and synchronizes the
JungleTimelineEngine when a camp transitions from visible to absent
(= cleared).

Honest scope notes
==================
* "Color heuristic" not "template matching" — false-positive rate is
  higher than what Blitz/Porofessor do, traded for zero asset
  pipeline + no opencv dependency.
* User must set CAMP_REGIONS in their config to match their League
  resolution + minimap-scale combo. Defaults target 1080p with a
  default minimap. No automatic minimap detection — that's Stage C.
* Disabled by default. Disabled on non-Windows (mss capture is
  unreliable on Wayland/macOS Sonoma+).
"""
