# OeMAG Marktpreis

Home Assistant integration for the Austrian [OeMAG](https://www.oem-ag.at/marktpreis/)
market price — the rate your grid export is compensated with if you sell through
OeMAG's Marktpreis-Bilanzgruppe.

It does two things:

1. Publishes the current market price as sensors.
2. Calculates what your grid export actually earned and writes it as a long-term
   statistic that the **Energy Dashboard** can use directly.

## Why the second part exists

OeMAG publishes a month's price at the *beginning of the following month*. So while
September is running, only August's price is known.

Home Assistant's built-in cost tracking cannot cope with that. Its `EnergyCostSensor`
multiplies each energy delta by whatever the price entity reads at that moment and
never revisits it:

```
cur_value + (energy - old_energy_value) * converted_energy_price
```

Point it at a market price entity and every month is permanently valued with the
*previous* month's price. There is no retroactive correction.

So this integration computes the compensation itself, hour by hour, and writes it as an
external statistic (`oemag:grid_export_compensation`). Re-importing a statistics row
with an existing timestamp overwrites it, so when the real price appears the past is
simply rewritten — no correction entry, no spike in the daily view.

### What that costs

Recomputing ten years of hourly rows on every poll would be absurd, and unnecessary:
corrections always land at the *end* of the series, never in the middle. A month's
price is published once and then stands. So the regular update only touches the running
month plus, once its price appears, the month that just ended — a window of at most two
months. Within that window only rows whose value actually moved are written, which in
steady state is a handful per run.

A full recalculation is available as a service for the cases that genuinely need it.

## Installation

### HACS

Add this repository as a custom repository (category *Integration*), install it, and
restart Home Assistant.

### Manual

Copy `custom_components/oemag` into your Home Assistant `custom_components` directory
and restart.

## Setup

**Settings → Devices & Services → Add Integration → OeMAG Marktpreis**

| Option | Meaning |
| --- | --- |
| Grid export sensor | The sensor counting energy fed into the grid. Must have `state_class: total_increasing`. Wh and kWh both work — the statistics are read back in kWh regardless. |
| Technology | OeMAG publishes a separate, slightly lower price for wind. Default is photovoltaic. |

### Wiring it into the Energy Dashboard

**Settings → Dashboards → Energy → Return to grid**, then set the compensation to
**"Use a statistic"** and pick `oemag:grid_export_compensation`.

Leave the *entity* and *fixed price* options empty. Those route through the built-in
cost sensor, which is exactly the path that cannot be corrected.

## Entities

| Entity | Description |
| --- | --- |
| `sensor.oemag_marktpreis` | Latest published price in EUR/kWh |
| `sensor.oemag_preismonat` | Which month that price is for |
| `sensor.oemag_endgultig_bewertet_bis` | Last month valued with a published price. Everything after it is provisional. |
| `sensor.oemag_preistabelle` | Diagnostic. State is the number of known months; the attributes hold every stored price in ct/kWh, which of them were entered by hand, and which are still provisional. |

The running month is valued provisionally with the most recent published price and
rewritten automatically once the real one appears.

## Price overview

`sensor.oemag_preistabelle` carries the whole stored table in its attributes. A markdown
card turns it into something readable:

```yaml
type: markdown
content: |
  {% set t = state_attr('sensor.oemag_preistabelle', 'preise') or {} %}
  {% set manual = state_attr('sensor.oemag_preistabelle', 'manuell') or [] %}
  {% set prov = state_attr('sensor.oemag_preistabelle', 'vorlaeufig') or [] %}
  | Monat | ct/kWh | |
  |---|---:|---|
  {% for m in t | sort -%}
  | {{ m }} | {{ t[m] }} | {{ '✏️' if m in manual }}{{ '⏳' if m in prov }} |
  {% endfor %}
```

✏️ marks a price you entered yourself, ⏳ one that is still provisional and will be
rewritten when the published figure arrives.

## Services

### `oemag.set_price`

Records a price by hand — month as `YYYY-MM`, price in ct/kWh exactly as the page states
it — and recalculates the whole compensation history with it.

This is the escape hatch for the turn of the year (see below). A manually entered price
is replaced automatically as soon as OeMAG publishes one for the same month.

### `oemag.clear_price`

Removes a price you entered by hand and recalculates. Published prices are not affected.

### `oemag.rebuild_statistics`

Recalculates the entire compensation history from the first recorded hour. Use it to
backfill after installing. The integration also rebuilds on its own whenever a stored
price turns out to have changed, and after every `set_price` / `clear_price`.

## Notes

- Statistics buckets sit on UTC hour boundaries while OeMAG's months are calendar months
  in `Europe/Vienna`. Hours are assigned to months in local time, so month boundaries and
  DST changes land where you would expect.
- Hours before the earliest known price leave the sum flat rather than being valued with
  a guess.
### The turn of the year

The page only ever shows the running year's table; earlier years are kept as a PDF. A
month's price is published at the beginning of the following month — so December's price
appears in January, by which time the table may already have switched to the new year.

Whether it does is genuinely unknown: the Internet Archive holds a single snapshot of
the page (2026-02-25), and it shows the 2026 table containing only January. There is no
archived January page to settle it either way. Two sources were checked as an automatic
fallback and both are dead ends:

- the yearly PDF (`Marktpreise_2025.pdf`) holds its table as an **image**, with no text
  layer to parse;
- the monthly spreadsheet (`Berechnungsgrundlagen_MP_12_2025.xlsx`) exists under a
  guessable URL but only carries the *day-ahead average*, not the market price. For
  August 2026 that average is 9.405 ct/kWh while the published prices are 8.997 (PV) and
  8.951 (wind) — the "Aufwand Ausgleichsenergie" deduction is not in the file, and some
  months are formed by an entirely different rule (60% of the market price under § 41
  ÖSG). The price cannot be recomputed.

So the integration fails safe rather than guessing: a month it cannot see stays
*provisional*, `sensor.oemag_endgultig_bewertet_bis` stops advancing, and nothing gets
silently frozen at the wrong price. If the gap turns out to be real, one
`oemag.set_price` call per year closes it.
- Scraping breaks when the page changes. The parser identifies the table by its `Monat`
  column and the price column by its heading rather than by position, but it is still
  scraping.

## Development

The parsing and the compensation maths are kept free of Home Assistant imports, so the
tests run anywhere:

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-alpine sh -c \
  'pip -q install beautifulsoup4 pytest tzdata && python -m pytest tests -q'
```

The committed fixture is written for the tests rather than copied from oem-ag.at — it
reproduces the structural traits the parser has to survive (the year living in a heading
above the table, the photovoltaic column heading also containing the word *Windkraft*,
a second table with nearly the same CSS classes) and nothing else. The prices in it are
the figures OeMAG published for 2026, so the test still says something about reality.

Because a synthetic fixture cannot notice the site being restructured, there is an
opt-in test that checks the live page. It needs the network and is skipped by default:

```bash
pytest -m live -o addopts=
```

## Prior art

The idea started from [Mr_Kohli/oemag2mqtt](https://gitlab.com/Mr_Kohli/oemag2mqtt),
which publishes the same price to MQTT. This integration shares no code with it — the
approach here (statistics instead of MQTT, full price history instead of the latest
value) is different enough that it was written from scratch. Credit for the original
idea belongs there.

If you do not run Home Assistant, or want the price in Node-RED, ioBroker or Grafana,
the MQTT route remains the better fit.

## License

MIT
