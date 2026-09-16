"""Turning hourly export energy into a cumulative compensation series.

Pure on purpose - no Home Assistant imports - so the retro-correction logic can be
tested without a running Home Assistant.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo

# Long-term statistics are stored as floats; rounding every step keeps the running sum
# stable and lets us compare against what is already stored without a tolerance dance.
PRECISION = 6


@dataclass(frozen=True, slots=True)
class EnergyHour:
    """One hour of exported energy."""

    start: datetime
    """Start of the hour, timezone aware, UTC."""

    kwh: float


@dataclass(frozen=True, slots=True)
class CompensationRow:
    """One hour of the cumulative compensation series."""

    start: datetime
    sum: float


@dataclass(frozen=True, slots=True)
class BuildResult:
    rows: tuple[CompensationRow, ...]
    """Only the rows that differ from what is already stored."""

    considered: int
    baseline_after: float
    """Cumulative sum at the end of ``finalize_through``."""


PriceLookup = Callable[[int, int], float | None]


def build_series(
    hours: Sequence[EnergyHour],
    price_for: PriceLookup,
    *,
    baseline: float = 0.0,
    existing: Mapping[datetime, float] | None = None,
    tz: tzinfo = timezone.utc,
    finalize_through: tuple[int, int] | None = None,
) -> BuildResult:
    """Build the cumulative compensation series for ``hours``.

    ``price_for`` maps a (year, month) to EUR/kWh, or to ``None`` when there is no
    basis to value that month at all - those hours leave the sum flat rather than
    guessing.

    Statistics buckets sit on UTC hour boundaries while OeMAG's months are calendar
    months in local time, so each hour is assigned to a month via ``tz``.
    """
    existing = existing or {}
    running = round(baseline, PRECISION)
    baseline_after = running
    rows: list[CompensationRow] = []

    for hour in sorted(hours, key=lambda item: item.start):
        local = hour.start.astimezone(tz)
        month = (local.year, local.month)

        price = price_for(*month)
        if price is not None:
            running = round(running + hour.kwh * price, PRECISION)

        stored = existing.get(hour.start)
        if stored is None or round(stored, PRECISION) != running:
            rows.append(CompensationRow(hour.start, running))

        if finalize_through is not None and month <= finalize_through:
            baseline_after = running

    return BuildResult(tuple(rows), len(hours), baseline_after)


def provisional_price_lookup(
    prices: Mapping[tuple[int, int], float],
) -> PriceLookup:
    """Look prices up, falling back to the most recent published month.

    OeMAG publishes a month's price at the beginning of the following month, so the
    running month never has a price of its own. It is valued with the latest published
    one and rewritten as soon as the real price appears.
    """
    published = sorted(prices)

    def lookup(year: int, month: int) -> float | None:
        key = (year, month)
        if key in prices:
            return prices[key]
        earlier = [candidate for candidate in published if candidate < key]
        if not earlier:
            return None
        return prices[earlier[-1]]

    return lookup


def next_month(month: tuple[int, int]) -> tuple[int, int]:
    year, number = month
    return (year + 1, 1) if number == 12 else (year, number + 1)


def contiguous_through(
    prices: Mapping[tuple[int, int], float], previous: tuple[int, int] | None
) -> tuple[int, int] | None:
    """Latest month that may be treated as settled.

    A month counts as settled only if it and every month before it back to ``previous``
    has a published price. The gap this guards against is the turn of the year: the page
    only ever shows the running year's table, so December's price - published in January
    - can be missing from it entirely. Without this check December would be marked
    settled while still valued with November's price, and never corrected.
    """
    if not prices:
        return previous

    candidate = previous
    month = next_month(previous) if previous else min(prices)
    while month in prices:
        candidate = month
        month = next_month(month)
    return candidate


def merge_prices(
    stored: Mapping[tuple[int, int], float],
    scraped: Mapping[tuple[int, int], float],
    manual: Iterable[tuple[int, int]],
) -> tuple[dict[tuple[int, int], float], set[tuple[int, int]]]:
    """Fold a fresh scrape into what we already know.

    Merged rather than replaced, because the page only ever lists the running year -
    dropping what it no longer shows would throw away every earlier price.

    A published price wins over a manually entered one for the same month: once OeMAG
    states a figure, that is the figure, and the manual entry has served its purpose.
    """
    known = {**stored, **scraped}
    remaining = {month for month in manual if month not in scraped}
    return known, remaining
