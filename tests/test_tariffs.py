"""Tests for effective-dated tariff handling."""

import datetime
import unittest

from custom_components.energy_locals.const import CONF_PRICE_USAGE_DOLLARS
from custom_components.energy_locals.tariffs import (
    TARIFF_EFFECTIVE_FROM,
    tariff_for_date,
    upsert_tariff,
)


class TariffTests(unittest.TestCase):
    def test_non_finite_prices_are_rejected(self):
        with self.assertRaises(ValueError):
            upsert_tariff([], "2026-01-10", float("inf"), 0.82)

    def test_tariff_boundary_uses_local_calendar_date(self):
        tariffs = upsert_tariff([], "2026-01-01", 0.31, 0.82)
        tariffs = upsert_tariff(tariffs, "2026-08-01", 0.37, 1.05)

        july = tariff_for_date(tariffs, datetime.date(2026, 7, 31))
        august = tariff_for_date(tariffs, datetime.date(2026, 8, 1))
        self.assertEqual(july[CONF_PRICE_USAGE_DOLLARS], 0.31)
        self.assertEqual(august[CONF_PRICE_USAGE_DOLLARS], 0.37)

    def test_upsert_replaces_same_date_and_sorts(self):
        tariffs = upsert_tariff([], "2026-08-01", 0.37, 1.05)
        tariffs = upsert_tariff(tariffs, "2026-01-01", 0.31, 0.82)
        tariffs = upsert_tariff(tariffs, "2026-08-01", 0.39, 1.07)

        self.assertEqual(
            [item[TARIFF_EFFECTIVE_FROM] for item in tariffs],
            ["2026-01-01", "2026-08-01"],
        )
        self.assertEqual(tariffs[-1][CONF_PRICE_USAGE_DOLLARS], 0.39)

    def test_no_tariff_before_first_period_is_rejected(self):
        tariffs = upsert_tariff([], "2026-08-01", 0.37, 1.05)

        with self.assertRaises(ValueError):
            tariff_for_date(tariffs, datetime.date(2026, 7, 31))


if __name__ == "__main__":
    unittest.main()
