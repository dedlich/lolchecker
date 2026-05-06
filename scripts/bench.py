"""Benchmark the decision engine.

Loads the LCDA fixture(s), constructs a snapshot from each, and times
``decision_engine.evaluate()`` over many iterations. Emits one JSONL
record per fixture × iteration to stdout, suitable for piping into the
existing ``scripts/telemetry_summary.py`` pipeline:

    .venv/bin/python scripts/bench.py [--iterations N] [--fixture PATH]
    .venv/bin/python scripts/bench.py | scripts/telemetry_summary.py

Per OPTIMIZATION.md §2.5 — fills the "no benchmark script" gap so a
nightly CI workflow can alert on >10 % regression in evaluate() time.
The output schema:

    {"fixture": "...", "iter": N, "ns_per_eval": 12345.6, "rule_count": 53,
     "ns_total": 657192, "rules_fired": 4}

A summary line at the end mirrors the perf_monitor style:
    [bench] mean=12.3µs/eval p95=14.8µs over N=2000 evals
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from champ_assistant.advisor.decision_engine import ALL_RULES, evaluate  # noqa: E402
from champ_assistant.lcda.client import LcdaClient  # noqa: E402
from champ_assistant.lcda.source import LcdaSource  # noqa: E402

DEFAULT_FIXTURE_DIR = ROOT / "tests" / "fixtures" / "lcda"


def _load_payloads(fixture_path: Path) -> list[tuple[str, dict]]:
    """Return a list of (label, payload) tuples. ``fixture_path`` may be a
    file or a directory of JSON files."""
    if fixture_path.is_file():
        return [(fixture_path.stem, json.loads(fixture_path.read_text()))]
    if fixture_path.is_dir():
        result: list[tuple[str, dict]] = []
        for p in sorted(fixture_path.glob("*.json")):
            try:
                result.append((p.stem, json.loads(p.read_text())))
            except json.JSONDecodeError:
                continue
        return result
    raise SystemExit(f"fixture path does not exist: {fixture_path}")


def _make_source() -> LcdaSource:
    """Build a stub source so we can call ``_snapshot_from`` in isolation.
    The client is never used (no I/O); ``_snapshot_from`` only consumes
    the dict argument and the source's spike-detection state."""
    import httpx
    client = LcdaClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})))

    async def _noop(_snap):
        return None

    return LcdaSource(client, _noop, poll_interval=0.0)


def _bench_one(label: str, payload: dict, iterations: int) -> list[dict]:
    """Return a list of per-iteration JSONL records for one fixture."""
    source = _make_source()
    snapshot = source._snapshot_from(payload)
    if snapshot is None:
        return []

    # Warm-up — first call is dominated by import / lazy init costs.
    evaluate(snapshot)
    rule_count = len(ALL_RULES)

    records: list[dict] = []
    for i in range(iterations):
        t0 = time.perf_counter_ns()
        recs = evaluate(snapshot)
        ns_total = time.perf_counter_ns() - t0
        records.append({
            "fixture": label,
            "iter": i,
            "ns_per_eval": float(ns_total),
            "ns_total": ns_total,
            "rule_count": rule_count,
            "rules_fired": len(recs),
        })
    return records


def _summary(records: list[dict]) -> str:
    if not records:
        return "[bench] no records — empty run"
    times_us = [r["ns_per_eval"] / 1000.0 for r in records]
    mean_us = statistics.mean(times_us)
    median_us = statistics.median(times_us)
    p95_us = sorted(times_us)[int(len(times_us) * 0.95)] if len(times_us) > 20 else max(times_us)
    n = len(records)
    return (
        f"[bench] mean={mean_us:.1f}µs/eval median={median_us:.1f}µs "
        f"p95={p95_us:.1f}µs over N={n} evals"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE_DIR,
                        help=f"Fixture file or directory (default: {DEFAULT_FIXTURE_DIR})")
    parser.add_argument("--iterations", type=int, default=1000,
                        help="Iterations per fixture (default: 1000)")
    parser.add_argument("--summary-only", action="store_true",
                        help="Print only the summary line; suppress JSONL.")
    args = parser.parse_args(argv)

    payloads = _load_payloads(args.fixture)
    if not payloads:
        print(f"[bench] no fixtures found at {args.fixture}", file=sys.stderr)
        return 1

    all_records: list[dict] = []
    for label, payload in payloads:
        records = _bench_one(label, payload, args.iterations)
        all_records.extend(records)
        if not args.summary_only:
            for r in records:
                print(json.dumps(r))

    print(_summary(all_records), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
