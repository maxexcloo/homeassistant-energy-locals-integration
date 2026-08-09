"""Tests for paired statistic series handling."""

import pathlib
import sys
import types
import unittest

INTEGRATION_DIR = (
    pathlib.Path(__file__).parents[1] / "custom_components" / "energy_locals"
)
energy_locals_package = types.ModuleType("custom_components.energy_locals")
energy_locals_package.__path__ = [str(INTEGRATION_DIR)]
sys.modules.setdefault("custom_components.energy_locals", energy_locals_package)

from custom_components.energy_locals.statistics import statistic_series_out_of_sync


class StatisticSeriesTests(unittest.TestCase):
    def test_empty_series_are_aligned(self):
        self.assertFalse(statistic_series_out_of_sync(None, None))

    def test_equal_series_are_aligned(self):
        self.assertFalse(statistic_series_out_of_sync(123.0, 123.0))

    def test_missing_cost_series_requires_rebuild(self):
        self.assertTrue(statistic_series_out_of_sync(123.0, None))

    def test_missing_usage_series_requires_rebuild(self):
        self.assertTrue(statistic_series_out_of_sync(None, 123.0))

    def test_mismatched_series_require_rebuild(self):
        self.assertTrue(statistic_series_out_of_sync(123.0, 456.0))


if __name__ == "__main__":
    unittest.main()
