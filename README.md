# eWebtel water meter for Home Assistant

Custom integration that reads a water meter from the
[eWebtel](https://ewebtel.com/) customer portal (WebTel by Plum Sp. z o.o.),
which Polish water utilities use for radio meter reading.

- Imports the **hourly meter readings** as long-term statistics, at the
  hours the water was used, including the **full history** the portal holds.
  The meter sends **one batch a day**, just after 05:00, with the hourly
  readings of the past 24 hours, so a live sensor alone would put the
  consumption at the wrong hours.
- Works with the **Energy dashboard** (Water consumption).
- Adds sensors per meter: meter reading, time of the last reading,
  consumption over the last 24 hours and yesterday.
- Raises a repair issue when the daily batch does not arrive.
- Set up from the UI (e-mail and password of the portal); asks for a new
  password when the portal rejects the stored one.

The portal has no public API. The integration uses the same requests as the
portal's web pages, so a portal update can break it.

## Requirements

- Home Assistant 2026.11 or newer.
- An account on ewebtel.com that shows your meter under *Measuring Sites*.

## Installation

### HACS (Marketplace)

1. HACS → ⋮ → **Custom repositories** → add
   `https://github.com/PiotrOrman/PlumWaterMeter`, type **Integration**.
2. Install **eWebtel water meter** and restart Home Assistant.

### Manual

Copy `custom_components/ewebtel` into `/config/custom_components/` (for
example with the Studio Code Server or File editor app) and restart Home
Assistant.

## Setup

1. **Settings → Devices & services → Add integration → eWebtel water meter**.
2. Enter the e-mail and password you use on ewebtel.com.

The first update imports the whole history. After that the integration
polls every hour and adds only the new hours; new data shows up once a day,
in the first update after the meter's morning batch (about 05:00-06:00).

## Energy dashboard

**Settings → Dashboards → Energy → Water consumption → Add water source**
and pick the statistic **eWebtel &lt;meter&gt; water**
(`ewebtel:<meter serial>_water`). Pick the statistic, not the
*Meter reading* sensor: the sensors have no `state_class` on purpose, so
they don't create a second, wrongly timed statistic.

## Sensors

| Sensor | Meaning |
|---|---|
| Meter reading | Latest reading of the meter (m³). The attribute `statistic_id` names the imported statistic. |
| Last reading | Time of that reading. Normally up to ~24 h old: the meter sends one batch a day. |
| Consumption last 24 h | Latest reading minus the reading 24 h before it (05:00 to 05:00). Changes once a day, with each batch. |
| Consumption yesterday | Reading at midnight minus the reading a day earlier. Empty from midnight until the morning batch arrives. |

Version 0.1.0 had a *Consumption today* sensor instead of *Consumption
last 24 h*. With one batch a day it only ever showed 00:00-05:00, so 0.2.0
replaces it and removes the old entity.

## Repair issue: no data

If the newest reading is more than 30 hours old (the morning batch did not
arrive), the integration raises a warning in **Settings → System →
Repairs** and logs it once. The issue goes away by itself with the next
reading. Check the meter in the ewebtel.com portal: if the portal has no
new data either, the meter or its radio module is not sending.

## How the data is cleaned

- Times from the portal are local (Europe/Warsaw) without an offset; they
  are converted to UTC. On the October DST change the portal merges the two
  02:00 hours into one point, so that hour is missing; the next hour carries
  its consumption.
- A reading lower than the previous one, or one that would need more than
  10 m³/h to reach, is ignored as a glitch (logged as a warning).
- Flagged points before the first valid reading (the installation points)
  are ignored.
- Gaps in the data ("data discontinuity" in the portal) are fine: the
  reading is absolute, so the next hour carries the missing consumption.

### Meter replacement

After a meter swap the reading starts again near zero and the integration
ignores it as a drop. Remove the integration, delete the statistic in
**Tools → Statistics**, and set the integration up again.

## Brand images

`custom_components/ewebtel/brand/` holds the icon and logo that Home
Assistant (2026.10+) shows for the integration, light and dark. They are
made from the Plum logo on the ewebtel.com login page; "WATER" is set in
Lato (SIL Open Font License), the portal's font. The Plum name and logo
are trademarks of Plum Sp. z o.o.; this integration is not affiliated with
or endorsed by Plum.

## Development

Two test suites:

- `tests/`: the pure modules (`api.py` against a fake portal, and
  `processing.py`). No Home Assistant needed:

  ```bash
  uv run --no-project --with pytest --with aiohttp --with yarl python -m pytest tests
  ```

- `tests_ha/`: config flow, setup, statistics import and sensors, run in
  [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component)
  (Python 3.14). Its newest release, 0.13.375, pins Home Assistant 2026.10.4;
  there is no release for 2026.11 yet.

  ```bash
  uv venv --python 3.14 .venv
  uv pip install --python .venv "pytest-homeassistant-custom-component==0.13.375"
  .venv/bin/python -m pytest tests_ha
  ```
