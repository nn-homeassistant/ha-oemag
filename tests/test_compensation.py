"""Tests for the compensation series - above all its retroactive correction."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from compensation import (
    EnergyHour,
    build_series,
    contiguous_through,
    merge_prices,
    next_month,
    provisional_price_lookup,
)

VIENNA = ZoneInfo("Europe/Vienna")

JULY = 0.06146
AUGUST = 0.08997


def hours(start: datetime, kwh_per_hour: float, count: int) -> list[EnergyHour]:
    return [
        EnergyHour(start + timedelta(hours=offset), kwh_per_hour)
        for offset in range(count)
    ]


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def test_sum_is_cumulative():
    result = build_series(
        hours(utc(2026, 8, 1, 0), 2.0, 3),
        provisional_price_lookup({(2026, 8): AUGUST}),
        tz=VIENNA,
    )

    assert [row.sum for row in result.rows] == [
        round(2.0 * AUGUST, 6),
        round(4.0 * AUGUST, 6),
        round(6.0 * AUGUST, 6),
    ]


def test_running_month_is_valued_with_the_previous_price():
    # September has no price yet, so July's is the most recent published one.
    lookup = provisional_price_lookup({(2026, 7): JULY})

    assert lookup(2026, 9) == JULY


def test_the_past_is_rewritten_once_the_real_price_arrives():
    """The whole point of the integration.

    August is first valued provisionally with July's price. When August's own price is
    published, the same hours must come back with different sums.
    """
    august = hours(utc(2026, 8, 1, 0), 1.0, 24)

    provisional = build_series(
        august, provisional_price_lookup({(2026, 7): JULY}), tz=VIENNA
    )
    assert provisional.rows[-1].sum == round(24 * JULY, 6)

    stored = {row.start: row.sum for row in provisional.rows}
    corrected = build_series(
        august,
        provisional_price_lookup({(2026, 7): JULY, (2026, 8): AUGUST}),
        existing=stored,
        tz=VIENNA,
    )

    # Every hour moved, so every hour is rewritten - at the same timestamps, which is
    # what makes the recorder overwrite instead of duplicate.
    assert len(corrected.rows) == 24
    assert corrected.rows[-1].sum == round(24 * AUGUST, 6)
    assert [row.start for row in corrected.rows] == [
        row.start for row in provisional.rows
    ]


def test_unchanged_hours_are_not_rewritten():
    august = hours(utc(2026, 8, 1, 0), 1.0, 5)
    prices = {(2026, 8): AUGUST}

    first = build_series(august, provisional_price_lookup(prices), tz=VIENNA)
    stored = {row.start: row.sum for row in first.rows}
    second = build_series(
        august, provisional_price_lookup(prices), existing=stored, tz=VIENNA
    )

    assert second.rows == ()
    assert second.considered == 5


def test_only_the_new_hours_are_written_in_steady_state():
    prices = provisional_price_lookup({(2026, 8): AUGUST})
    known = hours(utc(2026, 8, 1, 0), 1.0, 5)
    stored = {row.start: row.sum for row in build_series(known, prices, tz=VIENNA).rows}

    grown = known + hours(utc(2026, 8, 1, 5), 1.0, 2)
    result = build_series(grown, prices, existing=stored, tz=VIENNA)

    assert [row.start for row in result.rows] == [
        utc(2026, 8, 1, 5),
        utc(2026, 8, 1, 6),
    ]


def test_months_are_assigned_in_local_time():
    """22:00 UTC on 31 August is already September in Vienna (UTC+2 in summer)."""
    seen: list[tuple[int, int]] = []

    def spy(year: int, month: int) -> float:
        seen.append((year, month))
        return 1.0

    build_series(
        [EnergyHour(utc(2026, 8, 31, 21), 1.0), EnergyHour(utc(2026, 8, 31, 22), 1.0)],
        spy,
        tz=VIENNA,
    )

    assert seen == [(2026, 8), (2026, 9)]


def test_hours_without_any_price_basis_leave_the_sum_flat():
    # Energy from before the earliest known price is not valued with a guess.
    result = build_series(
        hours(utc(2026, 6, 1, 0), 5.0, 3),
        provisional_price_lookup({(2026, 8): AUGUST}),
        tz=VIENNA,
    )

    assert [row.sum for row in result.rows] == [0.0, 0.0, 0.0]


def test_baseline_after_is_the_sum_at_the_end_of_the_final_month():
    """That value becomes the next run's starting point, so the window can shrink."""
    result = build_series(
        hours(utc(2026, 8, 1, 0), 1.0, 2) + hours(utc(2026, 9, 1, 0), 1.0, 2),
        provisional_price_lookup({(2026, 8): AUGUST}),
        tz=VIENNA,
        finalize_through=(2026, 8),
    )

    assert result.baseline_after == round(2 * AUGUST, 6)
    # September is provisional and continues on top of it.
    assert result.rows[-1].sum == round(4 * AUGUST, 6)


def test_baseline_carries_over_between_runs():
    prices = provisional_price_lookup({(2026, 8): AUGUST})
    first = build_series(
        hours(utc(2026, 8, 1, 0), 1.0, 2),
        prices,
        tz=VIENNA,
        finalize_through=(2026, 8),
    )

    second = build_series(
        hours(utc(2026, 9, 1, 0), 1.0, 1),
        prices,
        baseline=first.baseline_after,
        tz=VIENNA,
    )

    assert second.rows[0].sum == round(3 * AUGUST, 6)


def test_next_month_rolls_over_the_year():
    assert next_month((2026, 11)) == (2026, 12)
    assert next_month((2026, 12)) == (2027, 1)


def test_settles_through_the_last_contiguous_month():
    prices = {(2026, month): 0.05 for month in range(1, 9)}

    assert contiguous_through(prices, None) == (2026, 8)


def test_a_missing_december_is_not_silently_settled():
    """The turn of the year.

    The page only shows the running year's table, so in January the December price can
    be absent. December must stay provisional instead of being frozen at November's
    price.
    """
    prices = {(2026, 11): 0.05, (2027, 1): 0.07}

    assert contiguous_through(prices, (2026, 10)) == (2026, 11)


def test_december_settles_once_its_price_shows_up():
    prices = {(2026, 11): 0.05, (2026, 12): 0.06, (2027, 1): 0.07}

    assert contiguous_through(prices, (2026, 11)) == (2027, 1)


def test_settlement_never_moves_backwards():
    assert contiguous_through({}, (2026, 8)) == (2026, 8)
    assert contiguous_through({(2027, 5): 0.05}, (2026, 8)) == (2026, 8)


def test_a_gap_on_the_first_run_stops_the_settlement():
    assert contiguous_through({(2026, 1): 0.05, (2026, 3): 0.06}, None) == (2026, 1)


def test_merge_keeps_prices_the_page_no_longer_shows():
    """In January the page lists the new year only - 2026 must not be lost."""
    stored = {(2026, 11): 0.05, (2026, 12): 0.06}
    scraped = {(2027, 1): 0.07}

    known, _ = merge_prices(stored, scraped, manual=set())

    assert known == {(2026, 11): 0.05, (2026, 12): 0.06, (2027, 1): 0.07}


def test_a_published_price_replaces_the_manual_one():
    stored = {(2026, 12): 0.06}
    scraped = {(2026, 12): 0.0615}

    known, manual = merge_prices(stored, scraped, manual={(2026, 12)})

    assert known[(2026, 12)] == 0.0615
    assert manual == set()


def test_a_manual_price_survives_a_scrape_that_does_not_mention_it():
    stored = {(2026, 12): 0.06, (2027, 1): 0.07}
    scraped = {(2027, 1): 0.07}

    known, manual = merge_prices(stored, scraped, manual={(2026, 12)})

    assert known[(2026, 12)] == 0.06
    assert manual == {(2026, 12)}


def test_a_manually_filled_december_settles_the_gap():
    """After entering December by hand, January is reachable again."""
    known, _ = merge_prices(
        {(2026, 11): 0.05, (2027, 1): 0.07}, {}, manual={(2026, 12)}
    )
    assert contiguous_through(known, (2026, 10)) == (2026, 11)

    known[(2026, 12)] = 0.06
    assert contiguous_through(known, (2026, 10)) == (2027, 1)
