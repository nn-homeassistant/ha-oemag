"""Constants for the OeMAG Marktpreis integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "oemag"

MARKTPREIS_URL: Final = "https://www.oem-ag.at/marktpreis/"

CONF_TECHNOLOGY: Final = "technology"
CONF_ENERGY_ENTITY: Final = "energy_entity"

TECHNOLOGY_PV: Final = "pv"
TECHNOLOGY_WIND: Final = "wind"
TECHNOLOGIES: Final = [TECHNOLOGY_PV, TECHNOLOGY_WIND]

# OeMAG publishes a month's price at the beginning of the following month, so there is
# nothing to gain from polling often. Twice a day keeps the lag below half a day.
UPDATE_INTERVAL: Final = timedelta(hours=12)

# The compensation we write ourselves. It is an external statistic (note the colon), so
# the Energy Dashboard can pick it under "Rückspeisung ins Netz" -> "Statistik".
STATISTIC_ID: Final = f"{DOMAIN}:grid_export_compensation"
STATISTIC_NAME: Final = "OeMAG Einspeisevergütung"

SERVICE_REBUILD_STATISTICS: Final = "rebuild_statistics"
SERVICE_SET_PRICE: Final = "set_price"
SERVICE_CLEAR_PRICE: Final = "clear_price"

ATTR_MONTH: Final = "month"
ATTR_PRICE: Final = "price"

STORAGE_VERSION: Final = 1
STORAGE_KEY: Final = f"{DOMAIN}.statistics"

MONTH_NAMES: Final = (
    "Jänner",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)


def month_label(month: tuple[int, int]) -> str:
    """Render a (year, month) tuple as e.g. 'August 2026'."""
    year, number = month
    return f"{MONTH_NAMES[number - 1]} {year}"
