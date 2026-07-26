"""Tests for effective-dated tariff handling."""

import datetime
import pathlib
import sys
import types
import unittest

# Load the pure helper modules without executing the integration's Home Assistant
# package initialiser. Home Assistant is not required for these unit tests.
INTEGRATION_DIR = (
    pathlib.Path(__file__).parents[1] / "custom_components" / "energy_locals"
)
energy_locals_package = types.ModuleType("custom_components.energy_locals")
energy_locals_package.__path__ = [str(INTEGRATION_DIR)]
sys.modules.setdefault("custom_components.energy_locals", energy_locals_package)

from custom_components.energy_locals.const import (
    CONF_PRICE_SUPPLY_DOLLARS,
    CONF_PRICE_USAGE_DOLLARS,
    CONF_START_DATE,
    CONF_TARIFFS,
)
from custom_components.energy_locals.tariffs import (
    TARIFF_EFFECTIVE_FROM,
    daily_supply_charge,
    interval_usage_cost,
    normalise_tariffs,
    tariff_for_date,
    upsert_tariff,
)


class TariffTests(unittest.TestCase):
    def test_legacy_prices_become_initial_tariff(self):
        tariffs = normalise_tariffs(
            {
                CONF_START_DATE: "2026-01-10",
                CONF_PRICE_USAGE_DOLLARS: 0.31,
                CONF_PRICE_SUPPLY_DOLLARS: 0.82,
            }
        )

        self.assertEqual(
            tariffs,
            [
                {
                    TARIFF_EFFECTIVE_FROM: "2026-01-10",
                    CONF_PRICE_USAGE_DOLLARS: 0.31,
                    CONF_PRICE_SUPPLY_DOLLARS: 0.82,
                }
            ],
        )

    def test_tariff_boundary_uses_local_calendar_date(self):
        tariffs = normalise_tariffs(
            {
                CONF_TARIFFS: [
                    {
                        TARIFF_EFFECTIVE_FROM: "2026-01-01",
                        CONF_PRICE_USAGE_DOLLARS: 0.31,
                        CONF_PRICE_SUPPLY_DOLLARS: 0.82,
                    },
                    {
                        TARIFF_EFFECTIVE_FROM: "2026-08-01",
                        CONF_PRICE_USAGE_DOLLARS: 0.37,
                        CONF_PRICE_SUPPLY_DOLLARS: 1.05,
                    },
                ]
            }
        )

        july = tariff_for_date(tariffs, datetime.date(2026, 7, 31))
        august = tariff_for_date(tariffs, datetime.date(2026, 8, 1))
        self.assertEqual(july[CONF_PRICE_USAGE_DOLLARS], 0.31)
        self.assertEqual(august[CONF_PRICE_USAGE_DOLLARS], 0.37)

    def test_upsert_replaces_same_date_and_sorts(self):
        tariffs = normalise_tariffs(
            {
                CONF_TARIFFS: [
                    {
                        TARIFF_EFFECTIVE_FROM: "2026-08-01",
                        CONF_PRICE_USAGE_DOLLARS: 0.37,
                        CONF_PRICE_SUPPLY_DOLLARS: 1.05,
                    },
                    {
                        TARIFF_EFFECTIVE_FROM: "2026-01-01",
                        CONF_PRICE_USAGE_DOLLARS: 0.31,
                        CONF_PRICE_SUPPLY_DOLLARS: 0.82,
                    },
                ]
            }
        )
        tariffs = upsert_tariff(tariffs, "2026-08-01", 0.39, 1.07)

        self.assertEqual(
            [item[TARIFF_EFFECTIVE_FROM] for item in tariffs],
            ["2026-01-01", "2026-08-01"],
        )
        self.assertEqual(tariffs[-1][CONF_PRICE_USAGE_DOLLARS], 0.39)

    def test_no_tariff_before_first_period_is_rejected(self):
        tariffs = normalise_tariffs(
            {
                CONF_TARIFFS: [
                    {
                        TARIFF_EFFECTIVE_FROM: "2026-08-01",
                        CONF_PRICE_USAGE_DOLLARS: 0.37,
                        CONF_PRICE_SUPPLY_DOLLARS: 1.05,
                    }
                ]
            }
        )

        with self.assertRaises(ValueError):
            tariff_for_date(tariffs, datetime.date(2026, 7, 31))

    def test_zero_usage_day_still_has_supply_charge(self):
        tariff = {
            TARIFF_EFFECTIVE_FROM: "2026-08-01",
            CONF_PRICE_USAGE_DOLLARS: 0.37,
            CONF_PRICE_SUPPLY_DOLLARS: 1.05,
        }

        total_cost = interval_usage_cost(0.0, tariff) + daily_supply_charge(tariff)
        self.assertEqual(total_cost, 1.05)


if __name__ == "__main__":
    unittest.main()
