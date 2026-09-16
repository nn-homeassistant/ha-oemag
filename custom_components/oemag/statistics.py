"""Writing the OeMAG compensation as a Home Assistant long-term statistic.

The Energy Dashboard's own cost sensor multiplies each energy delta by the price that
is current at that moment and never looks back. Since OeMAG publishes a month's price
only during the following month, that would permanently value every month with the
previous month's price. So we compute the compensation ourselves and write it as an
external statistic, which lets us rewrite the past once the real price is known.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from functools import partial

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .compensation import (
    EnergyHour,
    build_series,
    contiguous_through,
    merge_prices,
    next_month,
    provisional_price_lookup,
)
from .const import DOMAIN, STATISTIC_ID, STATISTIC_NAME

_LOGGER = logging.getLogger(__name__)

Month = tuple[int, int]


def _format_month(month: Month) -> str:
    return f"{month[0]:04d}-{month[1]:02d}"


def _parse_month(text: str) -> Month:
    year, number = text.split("-")
    return int(year), int(number)


def _month_start(month: Month) -> datetime:
    """Local midnight on the first of the month, as an aware datetime."""
    return dt_util.start_of_local_day(datetime(month[0], month[1], 1))


def _as_utc(value: float | int | datetime) -> datetime:
    """Normalise a statistics row start, which the recorder hands out as a timestamp."""
    if isinstance(value, datetime):
        return dt_util.as_utc(value)
    return dt_util.utc_from_timestamp(float(value))


def _same_price(left: float, right: float) -> bool:
    return abs(left - right) < 1e-9


class CompensationWriter:
    """Keeps the ``oemag:grid_export_compensation`` statistic in sync."""

    def __init__(self, hass: HomeAssistant, store: Store) -> None:
        self.hass = hass
        self._store = store
        self._prices: dict[Month, float] = {}
        self._manual: set[Month] = set()
        self._finalized_through: Month | None = None
        self._baseline = 0.0

    @property
    def finalized_through(self) -> Month | None:
        """Latest month that is valued with a published (final) price."""
        return self._finalized_through

    @property
    def prices(self) -> dict[Month, float]:
        """Every price we know, published and manually entered alike."""
        return dict(self._prices)

    @property
    def manual_months(self) -> set[Month]:
        """Months whose price was entered by hand rather than scraped."""
        return set(self._manual)

    async def async_load(self) -> None:
        stored = await self._store.async_load() or {}
        self._prices = {
            _parse_month(key): float(value)
            for key, value in (stored.get("prices") or {}).items()
        }
        self._manual = {_parse_month(month) for month in (stored.get("manual") or [])}
        finalized = stored.get("finalized_through")
        self._finalized_through = _parse_month(finalized) if finalized else None
        self._baseline = float(stored.get("baseline") or 0.0)

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "prices": {
                    _format_month(month): price
                    for month, price in sorted(self._prices.items())
                },
                "manual": [_format_month(month) for month in sorted(self._manual)],
                "finalized_through": (
                    _format_month(self._finalized_through)
                    if self._finalized_through
                    else None
                ),
                "baseline": self._baseline,
            }
        )

    async def async_update(
        self,
        prices: Mapping[Month, float],
        energy_entity: str,
        *,
        full: bool = False,
    ) -> None:
        """Recompute and write the compensation statistic.

        Normally only the months that are not settled yet are touched - that is the
        running month plus, once its price appears, the one that just ended. Everything
        older is final and never rewritten, which keeps the write window at two months
        instead of the whole history.
        """
        if not prices:
            return

        # OeMAG occasionally corrects an already published month. Because the sum is
        # cumulative, every later hour shifts too - so that case earns a full rebuild
        # rather than a clever partial one.
        corrected = sorted(
            month
            for month, value in self._prices.items()
            if month in prices and not _same_price(prices[month], value)
        )

        known, manual = merge_prices(self._prices, prices, self._manual)
        settled_through = contiguous_through(known, self._finalized_through)

        rebuild = full or self._finalized_through is None or bool(corrected)
        if rebuild:
            if corrected:
                _LOGGER.info(
                    "OeMAG hat %s nachträglich korrigiert - die Vergütung wird "
                    "vollständig neu berechnet",
                    ", ".join(_format_month(month) for month in corrected),
                )
            window_start: datetime | None = None
            baseline = 0.0
        else:
            window_start = _month_start(next_month(self._finalized_through))
            baseline = self._baseline

        hours = await self._async_energy_hours(energy_entity, window_start)
        existing = await self._async_existing_sums(window_start)

        result = build_series(
            hours,
            provisional_price_lookup(known),
            baseline=baseline,
            existing=existing,
            tz=dt_util.get_default_time_zone(),
            finalize_through=settled_through,
        )

        if result.rows:
            async_add_external_statistics(
                self.hass,
                StatisticMetaData(
                    mean_type=StatisticMeanType.NONE,
                    has_sum=True,
                    name=STATISTIC_NAME,
                    source=DOMAIN,
                    statistic_id=STATISTIC_ID,
                    unit_of_measurement=self.hass.config.currency,
                ),
                [StatisticData(start=row.start, sum=row.sum) for row in result.rows],
            )

        _LOGGER.debug(
            "Vergütung aktualisiert: %s von %s Stunden geschrieben (ab %s), "
            "endgültig bewertet bis %s",
            len(result.rows),
            result.considered,
            "Beginn" if window_start is None else window_start.isoformat(),
            _format_month(settled_through) if settled_through else "-",
        )

        self._prices = known
        self._manual = manual
        self._finalized_through = settled_through
        self._baseline = result.baseline_after
        await self._async_save()

    async def async_set_price(self, month: Month, eur_per_kwh: float) -> None:
        """Record a price by hand, for a month the page does not (yet) show."""
        self._prices[month] = round(eur_per_kwh, 6)
        self._manual.add(month)
        # The month may sit before the watermark, and the sum is cumulative, so the
        # caller has to rebuild afterwards - which is what the service does.
        if self._finalized_through is not None and month <= self._finalized_through:
            self._finalized_through = None
            self._baseline = 0.0
        await self._async_save()

    async def async_clear_price(self, month: Month) -> bool:
        """Drop a manually entered price again. Published prices are left alone."""
        if month not in self._manual:
            return False
        self._manual.discard(month)
        self._prices.pop(month, None)
        self._finalized_through = None
        self._baseline = 0.0
        await self._async_save()
        return True

    async def _async_energy_hours(
        self, energy_entity: str, start: datetime | None
    ) -> list[EnergyHour]:
        """Read hourly exported energy, always in kWh.

        The meter may well report Wh natively; asking the recorder for kWh makes the
        unit a non-issue instead of a factor-1000 trap.
        """
        rows = await self._async_statistics(
            start, energy_entity, {"energy": "kWh"}, {"change"}
        )
        return [
            EnergyHour(_as_utc(row["start"]), float(row.get("change") or 0.0))
            for row in rows
        ]

    async def _async_existing_sums(
        self, start: datetime | None
    ) -> dict[datetime, float]:
        """Read back what we wrote last time, so we only rewrite what actually moved."""
        rows = await self._async_statistics(start, STATISTIC_ID, None, {"sum"})
        return {
            _as_utc(row["start"]): float(row["sum"])
            for row in rows
            if row.get("sum") is not None
        }

    async def _async_statistics(
        self,
        start: datetime | None,
        statistic_id: str,
        units: dict[str, str] | None,
        types: set[str],
    ) -> list[dict]:
        if start is None:
            start = dt_util.utc_from_timestamp(0)
        result = await get_instance(self.hass).async_add_executor_job(
            partial(
                statistics_during_period,
                self.hass,
                start,
                None,
                {statistic_id},
                "hour",
                units,
                types,
            )
        )
        return list(result.get(statistic_id, []))
