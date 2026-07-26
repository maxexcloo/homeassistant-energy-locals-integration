"""Helpers for effective-dated Energy Locals tariffs."""

from __future__ import annotations

import datetime
from typing import Any

from .const import (
    CONF_PRICE_SUPPLY_DOLLARS,
    CONF_PRICE_USAGE_DOLLARS,
    CONF_START_DATE,
    CONF_TARIFFS,
)

TARIFF_EFFECTIVE_FROM = "effective_from"


def _tariff(effective_from: str, usage_price: Any, supply_price: Any) -> dict:
    """Validate and return a tariff in its persisted form."""
    effective = datetime.date.fromisoformat(effective_from)
    usage = float(usage_price)
    supply = float(supply_price)
    if usage < 0 or supply < 0:
        raise ValueError("Tariff prices cannot be negative")
    return {
        TARIFF_EFFECTIVE_FROM: effective.isoformat(),
        CONF_PRICE_USAGE_DOLLARS: usage,
        CONF_PRICE_SUPPLY_DOLLARS: supply,
    }


def normalise_tariffs(config: dict) -> list[dict]:
    """Return a validated tariff schedule, migrating legacy prices in memory."""
    raw_tariffs = config.get(CONF_TARIFFS)
    if raw_tariffs:
        tariffs = [
            _tariff(
                item[TARIFF_EFFECTIVE_FROM],
                item[CONF_PRICE_USAGE_DOLLARS],
                item[CONF_PRICE_SUPPLY_DOLLARS],
            )
            for item in raw_tariffs
        ]
    else:
        tariffs = [
            _tariff(
                config[CONF_START_DATE],
                config.get(CONF_PRICE_USAGE_DOLLARS, 0.359),
                config.get(CONF_PRICE_SUPPLY_DOLLARS, 0.94),
            )
        ]

    by_date = {item[TARIFF_EFFECTIVE_FROM]: item for item in tariffs}
    return [by_date[key] for key in sorted(by_date)]


def upsert_tariff(
    tariffs: list[dict],
    effective_from: str,
    usage_price: Any,
    supply_price: Any,
) -> list[dict]:
    """Add or replace a tariff period and return the sorted schedule."""
    new_tariff = _tariff(effective_from, usage_price, supply_price)
    by_date = {item[TARIFF_EFFECTIVE_FROM]: item for item in tariffs}
    by_date[new_tariff[TARIFF_EFFECTIVE_FROM]] = new_tariff
    return [by_date[key] for key in sorted(by_date)]


def tariff_for_date(tariffs: list[dict], day: datetime.date) -> dict:
    """Return the tariff effective on a local calendar day."""
    selected = None
    for tariff in tariffs:
        if datetime.date.fromisoformat(tariff[TARIFF_EFFECTIVE_FROM]) <= day:
            selected = tariff
        else:
            break
    if selected is None:
        raise ValueError(f"No tariff configured for {day.isoformat()}")
    return selected


def interval_usage_cost(kwh: float, tariff: dict) -> float:
    """Calculate usage cost for an interval."""
    return kwh * tariff[CONF_PRICE_USAGE_DOLLARS]


def daily_supply_charge(tariff: dict) -> float:
    """Return the fixed supply charge for a published usage day."""
    return tariff[CONF_PRICE_SUPPLY_DOLLARS]
