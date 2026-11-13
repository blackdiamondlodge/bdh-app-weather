# bdh-app-weather

A [Black Diamond Hub](https://github.com/blackdiamondlodge/BlackDiamondHub) app: weather for
any location. Current conditions with an animated background, wind, humidity, air quality, UV,
sunrise/sunset, a swipeable 24-hour strip and a 7-day forecast. Data is from
[Open-Meteo](https://open-meteo.com/) (no API key), cached for 15 minutes; the page refreshes
itself every 5 minutes.

Active weather alerts (warnings, watches, advisories and special statements) show as a banner
coloured by severity, from [Environment Canada](https://weather.gc.ca/) in Canada and the
[National Weather Service](https://www.weather.gov/) in the US (both keyless, cached for 5 minutes).
While a wind, snowfall or rainfall alert is on, the 24-hour strip shows that number instead of the
chance of rain (wind speed, direction and gusts; snow or rain per hour with a running total), and
the home tile turns amber with the alert's name. With no alert, the strip still switches to snow amounts
when 2 cm or more is forecast over the next 24 hours.

## Settings

Set them on the hub's Settings page, in the site's `[apps.weather]` table, or as
`BDH_WEATHER_<KEY>` environment variables.

| Key | Default | |
|---|---|---|
| `place` | city from Settings > Location, else site brand subtitle | Name shown on the Now card |

Temperatures and wind follow the hub's units (its °C/°F switch). The page's own toggle only
changes what that visit shows.

Preview a background with `?wx=` (`sun`, `stars`, `partly`, `clouds`, `fog`, `drizzle`, `rain`,
`snow`, `storm`).
