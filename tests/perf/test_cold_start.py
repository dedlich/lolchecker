"""Cold-start regression test (OPTIMIZATION.md §2.3).

Charter A target: cold-import the orchestrator module in under 1000 ms.
The boot summary log line is wired (``boot._log_startup_summary``); this
test asserts the underlying number stays small even when the import
graph drifts.

A spawned subprocess is the only way to actually measure cold start —
``importlib.reload`` doesn't unload C extensions (Qt, numpy, mss), and
the in-process ``sys.modules`` cache hides every regression. Each test
case forks a fresh interpreter with ``-S`` (skip site) optional and
PYTHONDONTWRITEBYTECODE so no .pyc cache speedup masks the real cost.

Threshold: 1500 ms locally (CI runners are slower; headroom over the
1000 ms target). Ratchet down once we have 30 days of CI numbers.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

# Allow the threshold to be tightened from CI without editing the test —
# nightly soak workflow could set COLD_START_BUDGET_MS=1000 to assert
# the charter target directly once we trust CI runners not to flake.
_BUDGET_MS = float(os.environ.get("COLD_START_BUDGET_MS", "1500"))

# Number of subprocess samples per case. The minimum is what we assert —
# transient OS jitter (CI noise, antivirus scan) can spike a single
# sample by 100-200 ms; taking the min reflects the actual import cost.
_SAMPLES = 3


def _time_subprocess_import(module: str) -> float:
    """Spawn a fresh interpreter, measure wall-clock to import ``module``,
    return elapsed milliseconds."""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    t0 = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        env=env,
        capture_output=True,
        timeout=30.0,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if result.returncode != 0:
        raise AssertionError(
            f"importing {module} crashed: rc={result.returncode}\n"
            f"stderr:\n{result.stderr.decode('utf-8', errors='replace')}"
        )
    return elapsed_ms


def _min_of_n(module: str, n: int = _SAMPLES) -> float:
    return min(_time_subprocess_import(module) for _ in range(n))


@pytest.mark.slow
def test_cold_import_app_under_budget() -> None:
    """``champ_assistant.app`` is the orchestrator module — importing it
    pulls the full dependency graph (PyQt6, advisor, lcda, data,
    profiling). Cold-import has to fit the 1500 ms budget."""
    elapsed_ms = _min_of_n("champ_assistant.app")
    assert elapsed_ms < _BUDGET_MS, (
        f"Cold import of champ_assistant.app took {elapsed_ms:.0f}ms "
        f"(budget {_BUDGET_MS:.0f}ms over {_SAMPLES} samples). "
        f"Per OPTIMIZATION.md §2.3, charter A targets <1000 ms — investigate "
        f"new top-level imports added since the last perf baseline."
    )


@pytest.mark.slow
def test_cold_import_decision_engine_under_budget() -> None:
    """The decision engine should import very fast — it's pure-Python,
    no PyQt, no numpy. Tighter budget than the orchestrator."""
    # Engine is much smaller than the full orchestrator — give it 1/3 the budget.
    elapsed_ms = _min_of_n("champ_assistant.advisor.decision_engine")
    sub_budget_ms = _BUDGET_MS / 3.0
    assert elapsed_ms < sub_budget_ms, (
        f"Cold import of decision_engine took {elapsed_ms:.0f}ms "
        f"(budget {sub_budget_ms:.0f}ms = budget/3). The engine is pure "
        f"Python; if a heavyweight import sneaks in it'll hit every tick."
    )
