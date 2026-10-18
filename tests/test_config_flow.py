"""Account validation across setup, reauthentication and options."""

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
    CONF_START_DATE,
    CONF_USERNAME,
    DOMAIN,
    TZ_SYDNEY,
)


@pytest.mark.parametrize("step", ["user", "reauth", "options"])
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
    yesterday = datetime.datetime.now(TZ_SYDNEY).date() - datetime.timedelta(days=1)
    data = {
        CONF_ACCOUNT: "123",
        CONF_PASSWORD: "test-password",
        CONF_PRICE_SUPPLY_DOLLARS: 0.94,
        CONF_PRICE_USAGE_DOLLARS: 0.359,
        CONF_START_DATE: yesterday.isoformat(),
        CONF_USERNAME: "user@example.com",
    }
    api = Mock()
    if failure:
        method = api.login if failure is EnergyLocalsAuthError else api.get_data
        method.side_effect = failure("Validation failed")
    with (
        patch(
            "homeassistant.components.recorder.async_setup",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.energy_locals.config_flow.EnergyLocalsAPI",
            return_value=api,
        ) as factory,
        patch(
            "custom_components.energy_locals.async_setup_entry",
            new=AsyncMock(return_value=True),
        ),
    ):
        entry = None
        submitted = dict(data)
        if step == "user":
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}, data=submitted
            )
        else:
            entry = MockConfigEntry(
                domain=DOMAIN, data=dict(data), unique_id="123", version=2
            )
            entry.add_to_hass(hass)
            if step == "reauth":
                submitted = {
                    CONF_PASSWORD: "replacement-password",
                    CONF_USERNAME: "replacement@example.com",
                }
                result = await hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"entry_id": entry.entry_id, "source": "reauth"},
                    data=entry.data,
                )
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], submitted
                )
            else:
                submitted.pop(CONF_PASSWORD)
                submitted.pop(CONF_USERNAME)
                submitted[CONF_ACCOUNT] = "456"
                result = await hass.config_entries.options.async_init(entry.entry_id)
                result = await hass.config_entries.options.async_configure(
                    result["flow_id"], submitted
                )
        await hass.async_block_till_done()

    expected = {**data, **submitted}
    factory.assert_called_once_with(
        expected[CONF_USERNAME], expected[CONF_PASSWORD], expected[CONF_ACCOUNT]
    )
    api.login.assert_called_once_with()
    if failure is EnergyLocalsAuthError:
        api.get_data.assert_not_called()
    else:
        api.get_data.assert_called_once_with(yesterday)
    if error:
        assert result["type"] == "form"
        assert result["errors"] == {"base": error}
        if entry:
            assert entry.data == data
            assert entry.unique_id == "123"
        else:
            assert not hass.config_entries.async_entries(DOMAIN)
    elif step == "reauth":
        assert result["type"] == "abort"
        assert result["reason"] == "reauth_successful"
        assert entry.data[CONF_PASSWORD] == submitted[CONF_PASSWORD]
        assert entry.data[CONF_USERNAME] == submitted[CONF_USERNAME]
    else:
        assert result["type"] == "create_entry"
        if entry:
            assert entry.data[CONF_ACCOUNT] == "456"
            assert entry.unique_id == "456"
        else:
            assert result["data"][CONF_ACCOUNT] == "123"
