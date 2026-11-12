# bdh-app-weather

A [Black Diamond Hub](https://github.com/blackdiamondlodge/BlackDiamondHub) app: weather for
any location. Current conditions with an animated background, wind, humidity, air quality, UV,
sunrise/sunset, a swipeable 24-hour strip and a 7-day forecast. Data is from
[Open-Meteo](https://open-meteo.com/) (no API key), cached for 15 minutes; the page refreshes
itself every 5 minutes.

## Settings

Set them on the hub's Settings page, in the site's `[apps.weather]` table, or as
`BDH_WEATHER_<KEY>` environment variables.

| Key | Default | |
|---|---|---|
| `place` | site brand subtitle | Name shown on the Now card |
| `units` | `metric` | `metric` or `imperial`; the page's toggle overrides it per visit |
| `latitude`, `longitude` | site `[location]` | Forecast location |

Preview a background with `?wx=` (`sun`, `stars`, `partly`, `clouds`, `fog`, `drizzle`, `rain`,
`snow`, `storm`).
