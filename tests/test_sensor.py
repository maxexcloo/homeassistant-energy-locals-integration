"""Tests for Energy Locals entities."""

import datetime
from unittest.mock import AsyncMock, patch

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_locals.const import (
    CONF_ACCOUNT,
    CONF_PASSWORD,
    CONF_START_DATE,
    CONF_TARIFFS,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.energy_locals.tariffs import upsert_tariff


async def test_entities_use_documented_ids_and_report_sync_data(hass):
    """Entity IDs follow the README and missing totals stay unknown."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ACCOUNT: "123",
            CONF_PASSWORD: "secret",
            CONF_START_DATE: "2026-01-01",
            CONF_USERNAME: "user@example.com",
        },
        options={CONF_TARIFFS: upsert_tariff([], "2026-01-01", 0.359, 0.94)},
        unique_id="123",
        version=3,
    )
    entry.add_to_hass(hass)
    synced = datetime.datetime(2026, 10, 18, 12, tzinfo=dt_util.UTC)
    with (
        patch(
            "homeassistant.components.recorder.async_setup",
            new=AsyncMock(return_value=True),
        ),
        patch("custom_components.energy_locals.EnergyLocalsAPI"),
        patch(
            "custom_components.energy_locals.EnergyLocalsCoordinator._perform_sync",
            new=AsyncMock(
                return_value={
                    "last_synced": synced,
                    "price": 0.359,
                    "total_cost": 12.3456,
                    "total_kwh": None,
                }
            ),
        ) as perform_sync,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        registry = er.async_get(hass)
        assert {
            entity.entity_id
            for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
        } == {
            "button.energy_locals_123_rebuild_statistics",
            "sensor.energy_locals_123_cost",
            "sensor.energy_locals_123_last_synced",
            "sensor.energy_locals_123_usage",
            "sensor.energy_locals_123_usage_price",
        }
        assert hass.states.get("sensor.energy_locals_123_cost").state == "12.35"
        assert hass.states.get("sensor.energy_locals_123_last_synced").state == (
            synced.isoformat()
        )
        assert hass.states.get("sensor.energy_locals_123_usage").state == "unknown"
        price = hass.states.get("sensor.energy_locals_123_usage_price")
        assert price.state == "0.359"
        assert price.attributes["unit_of_measurement"] == "AUD/kWh"

        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.energy_locals_123_rebuild_statistics"},
            blocking=True,
        )
        assert perform_sync.await_args_list[-1].kwargs == {"force_rebuild": True}
