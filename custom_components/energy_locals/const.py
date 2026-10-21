"""Constants for the Energy Locals integration."""

from zoneinfo import ZoneInfo

DOMAIN = "energy_locals"
NAME = "Energy Locals"

CONF_ACCOUNT = "account_id"
CONF_PASSWORD = "password"
CONF_PRICE_SUPPLY_DOLLARS = "supply_price_dollars"
CONF_PRICE_USAGE_DOLLARS = "usage_price_dollars"
CONF_RESET_ACCOUNT = "reset_account_id"
CONF_RESET_START_DATE = "reset_start_date"
CONF_RESET_STATISTICS = "reset_statistics"
CONF_START_DATE = "start_date"
CONF_TARIFF_EFFECTIVE_DATE = "tariff_effective_date"
CONF_TARIFFS = "tariffs"
CONF_USERNAME = "username"

DEFAULT_PRICE_SUPPLY_DOLLARS = 0.94
DEFAULT_PRICE_USAGE_DOLLARS = 0.359

API_BASE = "https://uml-myaccount-api-app-au.azurewebsites.net"
DATA_URL_TEMPLATE = f"{API_BASE}/utility-accounts/{{}}/usage-chart"
LOGIN_URL = f"{API_BASE}/user/authenticate"
PORTAL_URL = "https://urban.energylocals.com.au"

TZ_SYDNEY = ZoneInfo("Australia/Sydney")


def entry_title(account_id: str) -> str:
    """Return the config entry and device title for a utility account."""
    return f"{NAME} ({account_id})"
