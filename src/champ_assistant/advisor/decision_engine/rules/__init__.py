"""Domain-grouped rule modules.

Each submodule owns a coherent slice of rule functions (objectives,
inhibitors, bounty, summoner cooldowns, combat, lane, personal, meta).
``_rules`` re-imports from these modules to assemble ``ALL_RULES`` and
preserve the legacy ``from ._rules import rule_xyz`` call sites.

The split is intentionally incremental — a domain lands here as soon as
it is extracted; what hasn't been moved yet still lives in ``_rules``.
"""
