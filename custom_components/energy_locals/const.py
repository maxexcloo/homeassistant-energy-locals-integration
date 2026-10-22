"""Constants for the Energy Locals integration."""

from zoneinfo import ZoneInfo

DOMAIN = "energy_locals"
NAME = "Energy Locals"

CONF_ACCOUNT = "account_id"
CONF_PASSWORD = "password"
CONF_PRICE_SUPPLY_DOLLARS = "supply_price_dollars"
CONF_PRICE_USAGE_DOLLARS = "usage_price_dollars"
CONF_START_DATE = "start_date"
CONF_TARIFF_EFFECTIVE_DATE = "tariff_effective_date"
CONF_TARIFFS = "tariffs"
CONF_USERNAME = "username"

DEFAULT_PRICE_SUPPLY_DOLLARS = 0.94
DEFAULT_PRICE_USAGE_DOLLARS = 0.359

TZ_SYDNEY = ZoneInfo("Australia/Sydney")


def entry_title(account_id: str) -> str:
    """Return the config entry and device title for a utility account."""
    return f"{NAME} ({account_id})"
