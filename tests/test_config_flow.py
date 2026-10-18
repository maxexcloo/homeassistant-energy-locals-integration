"""Configuration, reauthentication, reconfiguration and tariff options flows."""

import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_locals.api import (
    EnergyLocalsAccountError,
    EnergyLocalsAPIError,
    EnergyLocalsAuthError,
)
from custom_components.energy_locals.const import (
    CONF_ACCOUNT,
    CONF_PASSWORD,
    CONF_PRICE_SUPPLY_DOLLARS,
    CONF_PRICE_USAGE_DOLLARS,
    CONF_RESET_ACCOUNT,
    CONF_RESET_STATISTICS,
    CONF_START_DATE,
    CONF_TARIFF_EFFECTIVE_DATE,
    CONF_TARIFFS,
    CONF_USERNAME,
    DOMAIN,
    TZ_SYDNEY,
)
from custom_components.energy_locals.tariffs import upsert_tariff


def _today():
    return datetime.datetime.now(TZ_SYDNEY).date()


def _entry(hass, start_date, account="123"):
    """Add a current-version entry with one tariff from the start date."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ACCOUNT: account,
            CONF_PASSWORD: "test-password",
            CONF_START_DATE: start_date.isoformat(),
            CONF_USERNAME: "user@example.com",
        },
        options={CONF_TARIFFS: upsert_tariff([], start_date.isoformat(), 0.359, 0.94)},
        unique_id=account,
        version=3,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def mock_setup():
    """Skip recorder and integration setup while flows finish."""
    with (
        patch(
            "homeassistant.components.recorder.async_setup",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.energy_locals.async_setup_entry",
            new=AsyncMock(return_value=True),
        ),
    ):
        yield


@pytest.mark.usefixtures("mock_setup")
@pytest.mark.parametrize("step", ["user", "reauth", "reconfigure"])
@pytest.mark.parametrize(
    ("failure", "error"),
    [
        (None, None),
        (EnergyLocalsAuthError, "invalid_auth"),
        (EnergyLocalsAccountError, "invalid_account"),
        (EnergyLocalsAPIError, "cannot_connect"),
    ],
)
async def test_account_validation(hass, step, failure, error):
    """Each entry path validates access and retains settings on failure."""
    yesterday = _today() - datetime.timedelta(days=1)
    api = Mock()
    if failure:
        method = api.login if failure is EnergyLocalsAuthError else api.get_data
        method.side_effect = failure("Validation failed")
    entry = None if step == "user" else _entry(hass, yesterday)
    before = None if entry is None else dict(entry.data)
    with patch(
        "custom_components.energy_locals.config_flow.EnergyLocalsAPI",
        return_value=api,
    ) as factory:
        if step == "user":
            submitted = {
                CONF_ACCOUNT: "123",
                CONF_PASSWORD: "test-password",
                CONF_PRICE_SUPPLY_DOLLARS: 0.94,
                CONF_PRICE_USAGE_DOLLARS: 0.359,
                CONF_START_DATE: yesterday.isoformat(),
                CONF_USERNAME: "user@example.com",
            }
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}, data=submitted
            )
            expected = ("user@example.com", "test-password", "123")
        elif step == "reauth":
            result = await entry.start_reauth_flow(hass)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_PASSWORD: "replacement-password",
                    CONF_USERNAME: "replacement@example.com",
                },
            )
            expected = ("replacement@example.com", "replacement-password", "123")
        else:
            result = await entry.start_reconfigure_flow(hass)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_ACCOUNT: "456",
                    CONF_RESET_STATISTICS: False,
                    CONF_START_DATE: yesterday.isoformat(),
                },
            )
            expected = ("user@example.com", "test-password", "456")
        await hass.async_block_till_done()

    factory.assert_called_once_with(*expected)
    api.login.assert_called_once_with()
    if failure is EnergyLocalsAuthError:
        api.get_data.assert_not_called()
    else:
        api.get_data.assert_called_once_with(yesterday)
    if error:
        assert result["type"] == "form"
        assert result["errors"] == {"base": error}
        if entry:
            assert entry.data == before
            assert entry.unique_id == "123"
        else:
            assert not hass.config_entries.async_entries(DOMAIN)
    elif step == "user":
        assert result["type"] == "create_entry"
        assert result["data"] == {
            CONF_ACCOUNT: "123",
            CONF_PASSWORD: "test-password",
            CONF_START_DATE: yesterday.isoformat(),
            CONF_USERNAME: "user@example.com",
        }
        assert result["options"] == {
            CONF_TARIFFS: upsert_tariff([], yesterday.isoformat(), 0.359, 0.94)
        }
    elif step == "reauth":
        assert result["reason"] == "reauth_successful"
        assert entry.data[CONF_PASSWORD] == "replacement-password"
        assert entry.data[CONF_USERNAME] == "replacement@example.com"
    else:
        assert result["reason"] == "reconfigure_successful"
        assert entry.data[CONF_ACCOUNT] == "456"
        assert entry.data[CONF_RESET_ACCOUNT] == "123"
        assert entry.data[CONF_RESET_STATISTICS]
        assert entry.title == "Energy Locals (456)"
        assert entry.unique_id == "456"


@pytest.mark.usefixtures("mock_setup")
@pytest.mark.parametrize(
    ("account", "start_offset", "error"),
    [
        ("456", 0, "already_configured"),
        ("123", -1, "start_date_before_tariff"),
        ("123", 2, "start_date_future"),
    ],
)
async def test_reconfigure_rejects_invalid_settings(hass, account, start_offset, error):
    """Reconfiguration keeps the entry unchanged when the settings are invalid."""
    yesterday = _today() - datetime.timedelta(days=1)
    entry = _entry(hass, yesterday)
    _entry(hass, yesterday, account="456")
    before = dict(entry.data)
    with patch("custom_components.energy_locals.config_flow.EnergyLocalsAPI") as api:
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_ACCOUNT: account,
                CONF_RESET_STATISTICS: False,
                CONF_START_DATE: (
                    yesterday + datetime.timedelta(days=start_offset)
                ).isoformat(),
            },
        )
    api.assert_not_called()
    assert result["errors"] == {"base": error}
    assert entry.data == before


@pytest.mark.usefixtures("mock_setup")
async def test_reconfigure_queues_a_reset_for_the_same_account(hass):
    """A reset without an account change needs no account validation."""
    yesterday = _today() - datetime.timedelta(days=1)
    entry = _entry(hass, yesterday)
    with patch("custom_components.energy_locals.config_flow.EnergyLocalsAPI") as api:
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_ACCOUNT: "123",
                CONF_RESET_STATISTICS: True,
                CONF_START_DATE: yesterday.isoformat(),
            },
        )
    api.assert_not_called()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_RESET_ACCOUNT] == "123"
    assert entry.data[CONF_RESET_STATISTICS]


@pytest.mark.usefixtures("mock_setup")
@pytest.mark.parametrize(
    ("prices", "effective_offset", "schedule"),
    [
        # Changed prices default to taking effect today.
        ((0.4, 1.0), None, [(-30, 0.359, 0.94), (0, 0.4, 1.0)]),
        ((0.4, 1.0), 5, [(-30, 0.359, 0.94), (5, 0.4, 1.0)]),
        # Same-date entries replace the existing tariff.
        ((0.4, 1.0), -30, [(-30, 0.4, 1.0)]),
        # Prices already in effect leave the schedule unchanged.
        ((0.359, 0.94), None, [(-30, 0.359, 0.94)]),
    ],
)
async def test_options_store_tariffs(hass, prices, effective_offset, schedule):
    """The options flow stores the tariff schedule in the entry options."""
    today = _today()
    entry = _entry(hass, today - datetime.timedelta(days=30))
    before = dict(entry.data)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    submitted = {
        CONF_PRICE_USAGE_DOLLARS: prices[0],
        CONF_PRICE_SUPPLY_DOLLARS: prices[1],
    }
    if effective_offset is not None:
        submitted[CONF_TARIFF_EFFECTIVE_DATE] = (
            today + datetime.timedelta(days=effective_offset)
        ).isoformat()
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], submitted
    )
    await hass.async_block_till_done()

    expected = []
    for offset, usage, supply in schedule:
        effective = (today + datetime.timedelta(days=offset)).isoformat()
        expected = upsert_tariff(expected, effective, usage, supply)
    assert result["type"] == "create_entry"
    assert entry.options == {CONF_TARIFFS: expected}
    assert entry.data == before
