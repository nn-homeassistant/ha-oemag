"""Config flow for the OeMAG Marktpreis integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_ENERGY_ENTITY,
    CONF_TECHNOLOGY,
    DOMAIN,
    TECHNOLOGIES,
    TECHNOLOGY_PV,
)

TITLE = "OeMAG Marktpreis"


def _schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    energy_entity = defaults.get(CONF_ENERGY_ENTITY)
    return vol.Schema(
        {
            vol.Required(
                CONF_ENERGY_ENTITY,
                default=energy_entity if energy_entity else vol.UNDEFINED,
            ): EntitySelector(
                EntitySelectorConfig(
                    domain="sensor", device_class=[SensorDeviceClass.ENERGY]
                )
            ),
            vol.Required(
                CONF_TECHNOLOGY,
                default=defaults.get(CONF_TECHNOLOGY, TECHNOLOGY_PV),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=TECHNOLOGIES,
                    translation_key="technology",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


class OemagConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the export sensor and the technology."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title=TITLE, data={}, options=user_input)

        return self.async_show_form(step_id="user", data_schema=_schema())

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return OemagOptionsFlow()


class OemagOptionsFlow(OptionsFlow):
    """Allow changing the export sensor and the technology later on."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init", data_schema=_schema(self.config_entry.options)
        )
