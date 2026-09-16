"""Tests for parsing the OeMAG market price page."""

from pathlib import Path

import pytest
from scraper import (
    TECHNOLOGY_PV,
    TECHNOLOGY_WIND,
    ScrapeError,
    parse_prices,
)

FIXTURE = (Path(__file__).parent / "fixture_price_page.html").read_text(
    encoding="utf-8"
)


def test_reads_every_published_month():
    prices = parse_prices(FIXTURE, TECHNOLOGY_PV, default_year=1999)

    assert len(prices) == 8
    assert prices[(2026, 1)] == 0.08842
    assert prices[(2026, 8)] == 0.08997


def test_year_comes_from_the_heading_not_the_fallback():
    # The table itself carries no year - only the "Marktpreise 2026" heading above it.
    prices = parse_prices(FIXTURE, TECHNOLOGY_PV, default_year=1999)

    assert {year for year, _ in prices} == {2026}


def test_wind_is_a_separate_column():
    pv = parse_prices(FIXTURE, TECHNOLOGY_PV, default_year=2026)
    wind = parse_prices(FIXTURE, TECHNOLOGY_WIND, default_year=2026)

    # The photovoltaic heading reads "... (außer Windkraft)", so matching on the word
    # "Windkraft" alone would pick the wrong column.
    assert wind[(2026, 8)] == 0.08951
    assert pv[(2026, 8)] == 0.08997
    assert all(wind[month] < pv[month] for month in pv)


def test_ignores_the_second_table_on_the_page():
    # The application-process table carries nearly the same CSS classes, so picking
    # the price table by class would be a coin flip.
    prices = parse_prices(FIXTURE, TECHNOLOGY_PV, default_year=2026)

    assert all(1 <= month <= 12 for _, month in prices)


def test_months_without_a_price_yet_are_left_out():
    # September is already listed on the page, but its cell is still empty.
    prices = parse_prices(FIXTURE, TECHNOLOGY_PV, default_year=2026)

    assert (2026, 9) not in prices


def test_converts_ct_to_eur():
    prices = parse_prices(FIXTURE, TECHNOLOGY_PV, default_year=2026)

    # 5,720 ct/kWh -> 0.0572 EUR/kWh
    assert prices[(2026, 3)] == 0.0572


def test_skips_months_without_a_price():
    html = """
    <table><tr><th>Monat</th><th>Marktpreis Photovoltaik</th></tr>
    <tr><td>Jänner</td><td>8,842 ct/kWh</td></tr>
    <tr><td>Februar</td><td></td></tr></table>
    """
    prices = parse_prices(html, TECHNOLOGY_PV, default_year=2026)

    assert prices == {(2026, 1): 0.08842}


def test_accepts_januar_as_well_as_jaenner():
    html = """
    <table><tr><th>Monat</th><th>Marktpreis Photovoltaik</th></tr>
    <tr><td>Januar</td><td>1,000 ct/kWh</td></tr></table>
    """
    assert parse_prices(html, TECHNOLOGY_PV, default_year=2026) == {(2026, 1): 0.01}


def test_raises_when_the_table_is_gone():
    with pytest.raises(ScrapeError):
        parse_prices("<html><body>Umbau</body></html>", TECHNOLOGY_PV, 2026)


def test_raises_when_the_column_is_gone():
    html = "<table><tr><th>Monat</th><th>Irgendwas</th></tr></table>"
    with pytest.raises(ScrapeError):
        parse_prices(html, TECHNOLOGY_PV, 2026)


@pytest.mark.live
def test_the_real_page_still_parses():
    """Opt-in check against the live site: pytest -m live

    The committed fixture is synthetic, so it cannot notice OeMAG restructuring their
    page. This test can - at the cost of needing the network, which is why it does not
    run by default.
    """
    import urllib.request

    request = urllib.request.Request(
        "https://www.oem-ag.at/marktpreis/",
        headers={"User-Agent": "Mozilla/5.0 (compatible; ha-oemag tests)"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        html = response.read().decode("utf-8", errors="replace")

    pv = parse_prices(html, TECHNOLOGY_PV, default_year=1999)
    wind = parse_prices(html, TECHNOLOGY_WIND, default_year=1999)

    assert pv, "keine Preise gefunden - die Seite wurde vermutlich umgebaut"
    assert 1999 not in {year for year, _ in pv}, (
        "Jahr nicht aus der Überschrift gelesen"
    )
    assert all(0.0 < price < 1.0 for price in pv.values()), "Preise nicht in EUR/kWh"
    assert set(wind) == set(pv), "PV- und Windspalte decken nicht dieselben Monate ab"
