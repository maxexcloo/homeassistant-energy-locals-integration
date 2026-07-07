"""Constants for the Energy Locals integration."""

DOMAIN = "energy_locals"

# Auth Keys
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_ACCOUNT = "account_id"

# Calculation Keys
CONF_START_DATE = "start_date"
CONF_PRICE_USAGE_DOLLARS = "usage_price_dollars"
CONF_PRICE_SUPPLY_DOLLARS = "supply_price_dollars"
CONF_RESET_STATISTICS = "reset_statistics"
CONF_RESET_ACCOUNT = "reset_account_id"

# API
API_BASE = "https://uml-myaccount-api-app-au.azurewebsites.net"
LOGIN_URL = f"{API_BASE}/user/authenticate"
DATA_URL_TEMPLATE = f"{API_BASE}/utility-accounts/{{}}/usage-chart"
