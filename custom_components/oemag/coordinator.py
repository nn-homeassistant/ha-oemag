"""Fetching and parsing the OeMAG market price."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from functools import partial

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ENERGY_ENTITY,
    CONF_TECHNOLOGY,
    DOMAIN,
    MARKTPREIS_URL,
    TECHNOLOGY_PV,
    UPDATE_INTERVAL,
)
from .scraper import ScrapeError, parse_prices
from .statistics import CompensationWriter

_LOGGER = logging.getLogger(__name__)

# The site does not serve the table to a bare python-requests style agent.
USER_AGENT = "Mozilla/5.0 (compatible; ha-oemag)"

Month = tuple[int, int]


@dataclass(frozen=True, slots=True)
class OemagData:
    """Every published market price the page currently lists."""

    prices: dict[Month, float]
    technology: str
    fetched_at: datetime

    @property
    def latest_month(self) -> Month | None:
        return max(self.prices) if self.prices else None

    @property
    def latest_price(self) -> float | None:
        latest = self.latest_month
        return self.prices[latest] if latest else None


class OemagCoordinator(DataUpdateCoordinator[OemagData]):
    """Polls the market price page and keeps the compensation statistic in sync."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        writer: CompensationWriter,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
        self.writer = writer

    @property
    def technology(self) -> str:
        return self.config_entry.options.get(CONF_TECHNOLOGY, TECHNOLOGY_PV)

    @property
    def energy_entity(self) -> str | None:
        return self.config_entry.options.get(CONF_ENERGY_ENTITY)

    async def async_rebuild_statistics(self) -> None:
        """Recompute the whole compensation series from the first hour on."""
        if self.data is None:
            raise UpdateFailed("Es liegen noch keine Marktpreise vor")
        if not self.energy_entity:
            raise UpdateFailed("Es ist kein Einspeisesensor konfiguriert")
        await self.writer.async_update(self.data.prices, self.energy_entity, full=True)

    async def _async_update_data(self) -> OemagData:
        html = await self._async_fetch()

        try:
            prices = await self.hass.async_add_executor_job(
                partial(parse_prices, html, self.technology, dt_util.now().year)
            )
        except ScrapeError as err:
            raise UpdateFailed(
                f"Die Marktpreis-Seite ließ sich nicht auswerten: {err}"
            ) from err

        data = OemagData(
            prices=prices,
            technology=self.technology,
            fetched_at=dt_util.utcnow(),
        )

        if self.energy_entity:
            # The price sensors are useful on their own, so a problem while writing the
            # statistic must not take them down with it.
            try:
                await self.writer.async_update(prices, self.energy_entity)
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Die Einspeisevergütung konnte nicht geschrieben werden"
                )

        return data

    async def _async_fetch(self) -> str:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                MARKTPREIS_URL,
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                response.raise_for_status()
                # Decode explicitly rather than relying on charset sniffing.
                return (await response.read()).decode("utf-8", errors="replace")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(
                f"Die Marktpreis-Seite war nicht erreichbar: {err}"
            ) from err
