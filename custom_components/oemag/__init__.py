"""The OeMAG Marktpreis integration."""

from __future__ import annotations

import re

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.storage import Store

from .const import (
    ATTR_MONTH,
    ATTR_PRICE,
    DOMAIN,
    SERVICE_CLEAR_PRICE,
    SERVICE_REBUILD_STATISTICS,
    SERVICE_SET_PRICE,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .coordinator import OemagCoordinator
from .statistics import CompensationWriter

PLATFORMS: list[Platform] = [Platform.SENSOR]

type OemagConfigEntry = ConfigEntry[OemagCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: OemagConfigEntry) -> bool:
    """Set up OeMAG Marktpreis from a config entry."""
    writer = CompensationWriter(hass, Store(hass, STORAGE_VERSION, STORAGE_KEY))
    await writer.async_load()

    coordinator = OemagCoordinator(hass, entry, writer)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: OemagConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: OemagConfigEntry) -> None:
    """Reload when the options change, e.g. a different export sensor."""
    await hass.config_entries.async_reload(entry.entry_id)


_MONTH = re.compile(r"^(20\d{2})-(0[1-9]|1[0-2])$")

_MONTH_SCHEMA = vol.Schema({vol.Required(ATTR_MONTH): str})
_SET_PRICE_SCHEMA = _MONTH_SCHEMA.extend({vol.Required(ATTR_PRICE): vol.Coerce(float)})


def _parse_month(text: str) -> tuple[int, int]:
    match = _MONTH.match(text.strip())
    if match is None:
        raise ServiceValidationError(
            f"'{text}' ist kein Monat im Format JJJJ-MM, zum Beispiel 2026-12"
        )
    return int(match.group(1)), int(match.group(2))


def _loaded_coordinators(hass: HomeAssistant) -> list[OemagCoordinator]:
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    """Register the services once, not once per entry."""
    if hass.services.has_service(DOMAIN, SERVICE_REBUILD_STATISTICS):
        return

    async def _async_rebuild(call: ServiceCall) -> None:
        """Recompute the whole compensation history.

        Needed after OeMAG corrects an old price, after a bug on our side, or to
        backfill once - the regular update deliberately only touches the last two
        months.
        """
        for coordinator in _loaded_coordinators(hass):
            await coordinator.async_rebuild_statistics()

    async def _async_set_price(call: ServiceCall) -> None:
        """Record a price by hand and settle the past with it.

        The page only ever lists the running year, so December's price - published in
        January - can be missing from it entirely. This is the way to fill such a gap
        without waiting for OeMAG's CMS.
        """
        month = _parse_month(call.data[ATTR_MONTH])
        # Entered in ct/kWh, the way the page states it.
        eur_per_kwh = call.data[ATTR_PRICE] / 100

        for coordinator in _loaded_coordinators(hass):
            await coordinator.writer.async_set_price(month, eur_per_kwh)
            await coordinator.async_rebuild_statistics()
            coordinator.async_update_listeners()

    async def _async_clear_price(call: ServiceCall) -> None:
        """Remove a price that was entered by hand."""
        month = _parse_month(call.data[ATTR_MONTH])

        for coordinator in _loaded_coordinators(hass):
            if not await coordinator.writer.async_clear_price(month):
                raise ServiceValidationError(
                    f"Für {call.data[ATTR_MONTH]} wurde kein Preis von Hand "
                    "eingetragen - veröffentlichte Preise lassen sich nicht löschen"
                )
            await coordinator.async_rebuild_statistics()
            coordinator.async_update_listeners()

    hass.services.async_register(DOMAIN, SERVICE_REBUILD_STATISTICS, _async_rebuild)
    hass.services.async_register(
        DOMAIN, SERVICE_SET_PRICE, _async_set_price, schema=_SET_PRICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_PRICE, _async_clear_price, schema=_MONTH_SCHEMA
    )
