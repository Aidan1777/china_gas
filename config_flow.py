"""Config flow for China Gas integration."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant

try:
    from homeassistant.data_entry_flow import FlowResult
except ImportError:
    FlowResult = dict  # type: ignore[assignment,misc]  # HA < 2023.3 兼容

from .const import (
    DOMAIN,
    CONF_IS_PREPAID,
    CONF_LADDER_LEVEL_1,
    CONF_LADDER_LEVEL_2,
    CONF_LADDER_PRICE_1,
    CONF_LADDER_PRICE_2,
    CONF_LADDER_PRICE_3,
    CONF_YEAR_LADDER_START,
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("user_id"): str,
        vol.Required("access_token"): str,
        vol.Required("x_mas_app_info"): str,
        vol.Required("cust_code"): str,
        vol.Required("cust_name"): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict) -> dict[str, any]:
    """Validate the user input allows us to connect."""
    return {"title": f"中国燃气 {data['cust_name']} ({data['cust_code']})"}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for China Gas."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
                self._basic_data = user_input
                return await self.async_step_billing_config()
            except Exception:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_billing_config(
        self, user_input: dict[str, any] | None = None
    ) -> FlowResult:
        """Handle billing configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            all_data = {**self._basic_data, **user_input}
            info = await validate_input(self.hass, self._basic_data)
            return self.async_create_entry(title=info["title"], data=all_data)

        return self.async_show_form(
            step_id="billing_config",
            data_schema=vol.Schema({
                vol.Optional(CONF_IS_PREPAID, default=False): bool,
                vol.Required(CONF_YEAR_LADDER_START, default="0101"): str,
                vol.Required(CONF_LADDER_LEVEL_1, default=480): vol.Coerce(float),
                vol.Required(CONF_LADDER_LEVEL_2, default=660): vol.Coerce(float),
                vol.Required(CONF_LADDER_PRICE_1, default=2.18): vol.Coerce(float),
                vol.Required(CONF_LADDER_PRICE_2, default=2.62): vol.Coerce(float),
                vol.Required(CONF_LADDER_PRICE_3, default=3.27): vol.Coerce(float),
            }),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle an options flow for China Gas."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        data = self.config_entry.data

        options_schema = vol.Schema(
            {
                vol.Optional("user_id", default=data.get("user_id", "")): str,
                vol.Optional("access_token", default=data.get("access_token", "")): str,
                vol.Optional("x_mas_app_info", default=data.get("x_mas_app_info", "")): str,
                vol.Optional("cust_code", default=data.get("cust_code", "")): str,
                vol.Optional("cust_name", default=data.get("cust_name", "")): str,
                vol.Optional(CONF_IS_PREPAID, default=data.get(CONF_IS_PREPAID, False)): bool,
                vol.Required(CONF_YEAR_LADDER_START, default=data.get(CONF_YEAR_LADDER_START, "0101")): str,
                vol.Required(CONF_LADDER_LEVEL_1, default=data.get(CONF_LADDER_LEVEL_1, 480)): vol.Coerce(float),
                vol.Required(CONF_LADDER_LEVEL_2, default=data.get(CONF_LADDER_LEVEL_2, 660)): vol.Coerce(float),
                vol.Required(CONF_LADDER_PRICE_1, default=data.get(CONF_LADDER_PRICE_1, 2.18)): vol.Coerce(float),
                vol.Required(CONF_LADDER_PRICE_2, default=data.get(CONF_LADDER_PRICE_2, 2.62)): vol.Coerce(float),
                vol.Required(CONF_LADDER_PRICE_3, default=data.get(CONF_LADDER_PRICE_3, 3.27)): vol.Coerce(float),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
        )
