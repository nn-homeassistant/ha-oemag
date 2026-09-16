"""Parsing of the OeMAG Marktpreis page.

Deliberately free of Home Assistant imports so the parsing can be tested on its own.
"""

from __future__ import annotations

import re
from typing import Final

from bs4 import BeautifulSoup, Tag

TECHNOLOGY_PV: Final = "pv"
TECHNOLOGY_WIND: Final = "wind"

# The page is written in Austrian German, where January is "Jänner". "Januar" is
# accepted too, in case the wording is ever aligned with Germany.
_MONTHS: Final[dict[str, int]] = {
    "jänner": 1,
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}

# The table itself carries no year - it is only in the heading above it, which reads
# "Höhe des Marktpreises 2026" and "Marktpreise 2026".
_YEAR_HEADING: Final = re.compile(r"Marktpreis(?:e|es)?\s+(20\d{2})")

_PRICE: Final = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*ct/kWh\s*$", re.IGNORECASE)


class ScrapeError(Exception):
    """Raised when the page does not look the way we expect."""


def _cells(row: Tag) -> list[str]:
    return [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]


def _price_table(soup: BeautifulSoup) -> Tag:
    """Return the table whose first column is the month.

    The page contains a second table (the application process) that carries almost the
    same CSS classes, so picking by class would be a coin flip.
    """
    for table in soup.find_all("table"):
        first_row = table.find("tr")
        if first_row is None:
            continue
        cells = _cells(first_row)
        if cells and cells[0].strip().lower() == "monat":
            return table
    raise ScrapeError("Keine Preistabelle mit einer 'Monat'-Spalte gefunden")


def _year(table: Tag, default_year: int) -> int:
    for text in table.find_all_previous(string=_YEAR_HEADING):
        match = _YEAR_HEADING.search(str(text))
        if match:
            return int(match.group(1))
    return default_year


def _column_index(header: list[str], technology: str) -> int:
    for index, text in enumerate(header):
        lowered = text.lower()
        if "marktpreis" not in lowered:
            continue
        if technology == TECHNOLOGY_WIND:
            # The photovoltaic heading reads "... (außer Windkraft)", so it mentions
            # Windkraft as well. The wind column is the one that does *not* also
            # mention Photovoltaik.
            if "windkraft" in lowered and "photovoltaik" not in lowered:
                return index
        elif "photovoltaik" in lowered:
            return index
    raise ScrapeError(f"Keine Preisspalte für '{technology}' gefunden: {header}")


def _price(text: str) -> float | None:
    """Convert '8,842 ct/kWh' to 0.08842 EUR/kWh."""
    match = _PRICE.match(text)
    if match is None:
        return None
    return round(float(match.group(1).replace(",", ".")) / 100, 6)


def parse_prices(
    html: str, technology: str, default_year: int
) -> dict[tuple[int, int], float]:
    """Return {(year, month): EUR/kWh} for every published month on the page."""
    soup = BeautifulSoup(html, "html.parser")
    table = _price_table(soup)

    rows = table.find_all("tr")
    if not rows:
        raise ScrapeError("Die Preistabelle hat keine Zeilen")

    column = _column_index(_cells(rows[0]), technology)
    year = _year(table, default_year)

    prices: dict[tuple[int, int], float] = {}
    for row in rows[1:]:
        cells = _cells(row)
        if len(cells) <= column:
            continue
        month = _MONTHS.get(cells[0].strip().lower())
        if month is None:
            continue
        price = _price(cells[column])
        if price is None:
            # Months that have not been published yet are present but empty.
            continue
        prices[(year, month)] = price

    if not prices:
        raise ScrapeError("Die Preistabelle enthält keine lesbaren Preise")

    return prices
