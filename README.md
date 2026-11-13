# Romania Utilities for Home Assistant

Bills, consumption and meter-reading data from **E·ON România** (gas and
electricity, Myline) and **Hidroelectrica** (electricity, iHidro), as Home
Assistant sensors.

Unofficial and not affiliated with either provider. It uses the same private
APIs as the providers' own web and mobile apps, so a provider change can break
it until it is updated.

## Installation (HACS)

1. HACS → ⋮ → **Custom repositories** → add
   `https://github.com/randomdad156/ha-romania-utilities`, category **Integration**.
2. Download **Romania Utilities**, then restart Home Assistant.
3. **Settings → Devices & services → Add integration → Romania Utilities**.
4. Choose the provider, log in, and pick the contracts to add. Repeat for the
   other provider.

E·ON accounts with two-factor login get an extra step for the code that E·ON
sends by e-mail/SMS. The session is then kept alive with refresh tokens; if it
ever expires, Home Assistant asks you to log in again (no background logins
that would send you unwanted codes).

## Entities (per contract)

| Entity | Notes |
|---|---|
| Balance due | RON |
| Last invoice amount | RON; attributes: number, paid, issue/due date |
| Last invoice issue date / due date | date |
| Next due date / Days until due | earliest unpaid invoice; negative when overdue |
| Unpaid invoices | count (Hidroelectrica: 1 when a balance is due) |
| Consumption last month / this year | kWh or m³. Hidroelectrica: difference between index readings |
| Meter reading window start / end | date |
| Meter reading window open | binary sensor |
| Invoice overdue | binary sensor |
| Last meter index | total-increasing, usable in the Energy dashboard |
| Injected energy index, prosumer balance, compensations | only for prosumers |

Updates every 6 hours.

## Events

`romania_utilities_auth_failed` is fired when a provider rejects the saved
login, just before Home Assistant starts the re-authentication flow. Data:
`entry_id`, `provider`, `title`, `reason`. Useful for a push notification.

## How it talks to the providers

* **E·ON** – `api2.eon.ro`, the JSON API of the Myline web app (login,
  2FA, token refresh, contracts, invoices, consumption chart, meter index).
  The Myline web pages are behind a Cloudflare challenge; the API is not and
  needs no captcha.
* **Hidroelectrica** – the iHidro mobile app API (two-step login, then
  bill, billing history, reading window and index history). The iHidro web
  login requires a captcha, so it is not used.

Thanks to the MIT-licensed community projects
[HAForgeLabs/utilitati_romania](https://github.com/HAForgeLabs/utilitati_romania) and
[sfDuhNinja/hidro-ha](https://github.com/sfDuhNinja/hidro-ha), whose
documentation of these APIs made this possible. The code here is an
independent implementation.

## Diagnostics

**Settings → Devices & services → Romania Utilities → ⋮ → Download
diagnostics** gives the raw provider responses with credentials, tokens, names
and addresses redacted. Attach it to bug reports.

## Development

```bash
pip install pytest
pytest -q
```

## Licence

MIT
