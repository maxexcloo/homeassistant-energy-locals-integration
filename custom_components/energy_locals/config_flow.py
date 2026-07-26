"""Config flow for the Energy Locals integration."""

import datetime
from zoneinfo import ZoneInfo

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.selector import DateSelector

from .const import (
    DOMAIN,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_ACCOUNT,
    CONF_START_DATE,
    CONF_PRICE_USAGE_DOLLARS,
    CONF_PRICE_SUPPLY_DOLLARS,
    CONF_TARIFFS,
    CONF_TARIFF_EFFECTIVE_DATE,
    CONF_RESET_STATISTICS,
    CONF_RESET_ACCOUNT,
)
from .api import EnergyLocalsAPI
from .tariffs import (
    TARIFF_EFFECTIVE_FROM,
    normalise_tariffs,
    tariff_for_date,
    upsert_tariff,
)

TZ_SYDNEY = ZoneInfo("Australia/Sydney")


class EnergyLocalsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Energy Locals."""

    VERSION = 2

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
                user_input[CONF_TARIFFS] = normalise_tariffs(user_input)
                return self.async_create_entry(
                    title=f"Energy Locals ({user_input[CONF_ACCOUNT]})", data=user_input
                )
            except Exception:
                errors["base"] = "cannot_connect"

        default_date = (
            datetime.datetime.now(TZ_SYDNEY).date() - datetime.timedelta(days=30)
        ).isoformat()

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_ACCOUNT): str,
                vol.Required(CONF_START_DATE, default=default_date): DateSelector(),
                vol.Required(CONF_PRICE_USAGE_DOLLARS, default=0.359): vol.All(
                    vol.Coerce(float), vol.Range(min=0)
                ),
                vol.Required(CONF_PRICE_SUPPLY_DOLLARS, default=0.94): vol.All(
                    vol.Coerce(float), vol.Range(min=0)
                ),
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class EnergyLocalsOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            user_input = dict(user_input)
            title = f"Energy Locals ({user_input[CONF_ACCOUNT]})"
            effective_date = user_input.pop(CONF_TARIFF_EFFECTIVE_DATE, "")
            usage_price = user_input.pop(CONF_PRICE_USAGE_DOLLARS)
            supply_price = user_input.pop(CONF_PRICE_SUPPLY_DOLLARS)
            tariffs = normalise_tariffs(self._config_entry.data)
            if effective_date:
                tariffs = upsert_tariff(
                    tariffs, effective_date, usage_price, supply_price
                )

            current_tariff = tariff_for_date(
                tariffs, datetime.datetime.now(TZ_SYDNEY).date()
            )
            new_data = {
                **self._config_entry.data,
                **user_input,
                CONF_TARIFFS: tariffs,
                # Retain these keys for backwards compatibility with older releases.
                CONF_PRICE_USAGE_DOLLARS: current_tariff[
                    CONF_PRICE_USAGE_DOLLARS
                ],
                CONF_PRICE_SUPPLY_DOLLARS: current_tariff[
                    CONF_PRICE_SUPPLY_DOLLARS
                ],
            }
            if user_input.get(CONF_RESET_STATISTICS):
                new_data[CONF_RESET_ACCOUNT] = self._config_entry.data.get(CONF_ACCOUNT)
            self.hass.config_entries.async_update_entry(
                self._config_entry, title=title, data=new_data
            )
            return self.async_create_entry(title="", data={})

        data = self._config_entry.data
        tariffs = normalise_tariffs(data)
        current_tariff = tariff_for_date(
            tariffs, datetime.datetime.now(TZ_SYDNEY).date()
        )
        schedule = "\n".join(
            (
                f"{item[TARIFF_EFFECTIVE_FROM]}: "
                f"${item[CONF_PRICE_USAGE_DOLLARS]:g}/kWh, "
                f"${item[CONF_PRICE_SUPPLY_DOLLARS]:g}/day"
            )
            for item in tariffs
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ACCOUNT, default=data.get(CONF_ACCOUNT)
                ): cv.string,
                vol.Required(
                    CONF_START_DATE, default=data.get(CONF_START_DATE)
                ): DateSelector(),
                vol.Required(
                    CONF_PRICE_USAGE_DOLLARS,
                    default=current_tariff[CONF_PRICE_USAGE_DOLLARS],
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required(
                    CONF_PRICE_SUPPLY_DOLLARS,
                    default=current_tariff[CONF_PRICE_SUPPLY_DOLLARS],
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Optional(CONF_TARIFF_EFFECTIVE_DATE): DateSelector(),
                vol.Optional(CONF_RESET_STATISTICS, default=False): cv.boolean,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={"tariff_schedule": schedule},
        )
