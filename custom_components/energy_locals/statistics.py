"""Pure helpers for Energy Locals statistic series."""

from __future__ import annotations


def statistic_series_out_of_sync(
    usage_start: float | None, cost_start: float | None
) -> bool:
    """Return whether paired usage and cost series need rebuilding."""
    if (usage_start is None) != (cost_start is None):
        return True
    return usage_start is not None and usage_start != cost_start
