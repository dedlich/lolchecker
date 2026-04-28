"""Coverage gate: every canonical UI state must have a baseline,
and every baseline must map to a canonical state.

Failure modes
-------------
1. **Declared vector with no binding**: ``CANONICAL_VECTORS`` lists a
   state that no test produces a baseline for. Action: add a
   ``test_…_snapshot`` for it.
2. **Bound test with no canonical vector**: a baseline test maps to a
   StateVector that isn't in ``CANONICAL_VECTORS``. Action: either add
   the vector to the canonical set (deliberately extending coverage) or
   remove the test as redundant.
3. **Baseline JSON on disk with no binding**: an orphan ``.json`` file
   under ``baseline/`` that no test references. Either someone deleted
   the test but forgot the baseline, or someone added a baseline by
   hand. Action: bind it to a vector, or remove it.

The whole point: silently-uncovered code paths and silently-orphaned
baselines both stop being possible. Drift in coverage over time
(spec #5) is now a hard CI failure on the commit that introduced it.
"""
from __future__ import annotations

from pathlib import Path

from .coverage import BASELINE_BINDINGS, CANONICAL_VECTORS

BASELINE_DIR = Path(__file__).parent / "baseline"


def test_every_canonical_vector_has_a_baseline() -> None:
    """Each declared StateVector must be reachable from at least one
    baseline binding. Catches: a new code path was added to the
    canonical set but the test that exercises it was never written."""
    bound_vectors = set(BASELINE_BINDINGS.values())
    missing = [v for v in CANONICAL_VECTORS if v not in bound_vectors]
    if missing:
        joined = "\n".join(f"  {v}" for v in missing)
        raise AssertionError(
            "coverage gap: canonical vectors with no baseline test:\n"
            f"{joined}\n\n"
            "Add a test_… in tests/visual/test_widget_snapshots.py that "
            "emits a baseline for each vector, then bind it in "
            "BASELINE_BINDINGS in tests/visual/coverage.py."
        )


def test_every_binding_maps_to_a_canonical_vector() -> None:
    """Each baseline binding must point at a vector in CANONICAL_VECTORS.
    Catches: a test was added that snapshots a state we never declared
    intentional — silent scope creep in the canonical surface."""
    canonical = set(CANONICAL_VECTORS)
    stray = {
        name: vec
        for name, vec in BASELINE_BINDINGS.items()
        if vec not in canonical
    }
    if stray:
        joined = "\n".join(f"  {name} -> {vec}" for name, vec in stray.items())
        raise AssertionError(
            "coverage drift: baselines bound to non-canonical vectors:\n"
            f"{joined}\n\n"
            "Either add the vector to CANONICAL_VECTORS (deliberate "
            "extension), or remove the redundant test."
        )


def test_every_baseline_file_is_bound() -> None:
    """No orphan ``.json`` files allowed under baseline/. Catches:
    test deleted but baseline left behind, or baseline added by hand
    without a corresponding test+binding."""
    if not BASELINE_DIR.exists():
        return  # first-run, no baselines yet
    on_disk = {p.stem for p in BASELINE_DIR.glob("*.json")}
    bound = set(BASELINE_BINDINGS.keys())
    orphan = sorted(on_disk - bound)
    if orphan:
        joined = "\n".join(
            f"  tests/visual/baseline/{name}.json" for name in orphan
        )
        raise AssertionError(
            "orphan baseline file(s) on disk — no test references them:\n"
            f"{joined}\n\n"
            "Either bind each in BASELINE_BINDINGS in "
            "tests/visual/coverage.py, or delete the files."
        )


def test_every_binding_has_baseline_file_on_disk() -> None:
    """Each entry in BASELINE_BINDINGS must point at a real
    ``.json`` file. Catches: vector declared, binding wired, but the
    snapshot test was never actually written or its baseline was
    never committed."""
    bound = set(BASELINE_BINDINGS.keys())
    on_disk = {p.stem for p in BASELINE_DIR.glob("*.json")} if BASELINE_DIR.exists() else set()
    missing = sorted(bound - on_disk)
    if missing:
        joined = "\n".join(
            f"  {name}  -> tests/visual/baseline/{name}.json"
            for name in missing
        )
        raise AssertionError(
            "binding declared but no baseline file on disk:\n"
            f"{joined}\n\n"
            "Either:\n"
            "  * Write a snapshot test that emits this baseline (pair "
            "test name with the binding name), then run\n"
            "    UPDATE_VISUAL_BASELINES=1 .venv/bin/python -m pytest tests/visual/\n"
            "    to generate the file, then commit it.\n"
            "  * Or remove the binding if the vector isn't actually needed."
        )


def test_no_duplicate_canonical_vectors() -> None:
    """Two identical entries in CANONICAL_VECTORS would mean two
    baselines covering the same state — wasted maintenance, no extra
    drift catch. The dataclass is hashable so a set comparison is
    enough."""
    deduped = set(CANONICAL_VECTORS)
    assert len(deduped) == len(CANONICAL_VECTORS), (
        "CANONICAL_VECTORS contains duplicate entries — each StateVector "
        "should be unique. If two visually distinct snapshots share the "
        "same state vector, the vector is missing a dimension that "
        "actually distinguishes them."
    )


def test_no_duplicate_bindings_for_same_vector() -> None:
    """Two baseline files bound to the same vector means duplicate
    coverage — usually fine to remove one. Flagged so it's a
    deliberate decision, not silent."""
    seen: dict = {}
    duplicates: list[tuple[str, str]] = []
    for name, vec in BASELINE_BINDINGS.items():
        if vec in seen:
            duplicates.append((seen[vec], name))
        seen[vec] = name
    if duplicates:
        joined = "\n".join(f"  {a} ↔ {b}" for a, b in duplicates)
        raise AssertionError(
            "duplicate baseline coverage — these pairs cover identical "
            f"state vectors:\n{joined}"
        )
