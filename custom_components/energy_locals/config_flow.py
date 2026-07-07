"""Config flow for the Energy Locals integration."""

import datetime
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv


def _validate_date(value: str) -> str:
    try:
        datetime.date.fromisoformat(value)
        return value
    except (ValueError, TypeError):
        raise vol.Invalid("Use YYYY-MM-DD format")


from .const import (
    DOMAIN,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_ACCOUNT,
    CONF_START_DATE,
    CONF_PRICE_USAGE_DOLLARS,
    CONF_PRICE_SUPPLY_DOLLARS,
    CONF_RESET_STATISTICS,
    CONF_RESET_ACCOUNT,
)
from .api import EnergyLocalsAPI


class EnergyLocalsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Energy Locals."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return EnergyLocalsOptionsFlow(config_entry)

    async def async_step_user(self, user_input=None):
        """Handle the initial setup."""
        errors = {}

        if user_input is not None:
            for entry in self._async_current_entries():
                if entry.data.get(CONF_ACCOUNT) == user_input[CONF_ACCOUNT]:
                    return self.async_abort(reason="already_configured")

            api = EnergyLocalsAPI(
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                user_input[CONF_ACCOUNT],
            )
            try:
                await self.hass.async_add_executor_job(api.login)
                return self.async_create_entry(
                    title=f"Energy Locals ({user_input[CONF_ACCOUNT]})", data=user_input
                )
            except Exception:
                errors["base"] = "cannot_connect"

        default_date = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_ACCOUNT): str,
                vol.Required(CONF_START_DATE, default=default_date): _validate_date,
                vol.Required(CONF_PRICE_USAGE_DOLLARS, default=0.359): vol.Coerce(
                    float
                ),
                vol.Required(CONF_PRICE_SUPPLY_DOLLARS, default=0.94): vol.Coerce(
                    float
                ),
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class EnergyLocalsOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            title = f"Energy Locals ({user_input[CONF_ACCOUNT]})"
            new_data = {**self._config_entry.data, **user_input}
            if user_input.get(CONF_RESET_STATISTICS):
                new_data[CONF_RESET_ACCOUNT] = self._config_entry.data.get(CONF_ACCOUNT)
            self.hass.config_entries.async_update_entry(
                self._config_entry, title=title, data=new_data
            )
            return self.async_create_entry(title="", data={})

        data = self._config_entry.data
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ACCOUNT, default=data.get(CONF_ACCOUNT)
                ): cv.string,
                vol.Required(
                    CONF_START_DATE, default=data.get(CONF_START_DATE)
                ): cv.string,
                vol.Required(
                    CONF_PRICE_USAGE_DOLLARS, default=data.get(CONF_PRICE_USAGE_DOLLARS)
                ): vol.Coerce(float),
                vol.Required(
                    CONF_PRICE_SUPPLY_DOLLARS,
                    default=data.get(CONF_PRICE_SUPPLY_DOLLARS),
                ): vol.Coerce(float),
                vol.Optional(CONF_RESET_STATISTICS, default=False): cv.boolean,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
