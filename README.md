# eWebtel water meter for Home Assistant

Custom integration that reads a water meter from the
[eWebtel](https://ewebtel.com/) customer portal (WebTel by Plum Sp. z o.o.),
which Polish water utilities use for radio meter reading.

- Imports the **hourly meter readings** as long-term statistics, at the
  hours the water was used, including the **full history** the portal holds.
  The meter reports in batches a few times a day, so a live sensor alone
  would put the consumption at the wrong hours.
- Works with the **Energy dashboard** (Water consumption).
- Adds sensors per meter: meter reading, time of the last reading,
  consumption today and yesterday.
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
polls every hour and adds only the new hours.

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
| Last reading | Time of that reading. Expect it to be hours old: the meter sends data in batches. |
| Consumption today | Latest reading minus the reading at local midnight. |
| Consumption yesterday | Reading at midnight minus the reading a day earlier. |

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
