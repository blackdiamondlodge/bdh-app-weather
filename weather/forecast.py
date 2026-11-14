"""Current conditions, 24-hour and 7-day forecast and air quality from Open-Meteo (no API key)."""
import logging

import requests
from django.conf import settings
from django.core.cache import cache

from bdh_sdk.location import location as hub_location
from bdh_sdk.settings import app_setting

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
CACHE_TTL = 15 * 60
CURRENT = ("temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,is_day,"
           "wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation")
HOURLY = ("temperature_2m,weather_code,precipitation_probability,is_day,"
          "wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation,snowfall")
DAILY = ("weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
         "precipitation_sum,uv_index_max,sunrise,sunset")

# WMO weather interpretation codes → (Font Awesome icon, label)
WMO = {
    0: ("fas fa-sun", "Clear"),
    1: ("fas fa-sun", "Mainly clear"),
    2: ("fas fa-cloud-sun", "Partly cloudy"),
    3: ("fas fa-cloud", "Overcast"),
    45: ("fas fa-smog", "Fog"),
    48: ("fas fa-smog", "Freezing fog"),
    51: ("fas fa-cloud-rain", "Light drizzle"),
    53: ("fas fa-cloud-rain", "Drizzle"),
    55: ("fas fa-cloud-rain", "Heavy drizzle"),
    56: ("fas fa-cloud-rain", "Freezing drizzle"),
    57: ("fas fa-cloud-rain", "Freezing drizzle"),
    61: ("fas fa-cloud-rain", "Light rain"),
    63: ("fas fa-cloud-showers-heavy", "Rain"),
    65: ("fas fa-cloud-showers-heavy", "Heavy rain"),
    66: ("fas fa-cloud-rain", "Freezing rain"),
    67: ("fas fa-cloud-showers-heavy", "Freezing rain"),
    71: ("fas fa-snowflake", "Light snow"),
    73: ("fas fa-snowflake", "Snow"),
    75: ("fas fa-snowflake", "Heavy snow"),
    77: ("fas fa-snowflake", "Snow grains"),
    80: ("fas fa-cloud-sun-rain", "Showers"),
    81: ("fas fa-cloud-showers-heavy", "Showers"),
    82: ("fas fa-cloud-showers-heavy", "Heavy showers"),
    85: ("fas fa-snowflake", "Snow showers"),
    86: ("fas fa-snowflake", "Heavy snow showers"),
    95: ("fas fa-bolt", "Thunderstorm"),
    96: ("fas fa-bolt", "Thunderstorm with hail"),
    99: ("fas fa-bolt", "Thunderstorm with hail"),
}

NIGHT_ICONS = {"fas fa-sun": "fas fa-moon", "fas fa-cloud-sun": "fas fa-cloud-moon",
               "fas fa-cloud-sun-rain": "fas fa-cloud-moon-rain"}

# Background animations for the Now card.
EFFECTS = ("sun", "stars", "partly", "clouds", "fog", "drizzle", "rain", "snow", "storm")


def weather_effect(code, is_day=True):
    """WMO weather code → Now-card background animation (one of EFFECTS)."""
    if code in (0, 1):
        return "sun" if is_day else "stars"
    if code == 2:
        return "partly" if is_day else "clouds"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 57:
        return "drizzle"
    if 61 <= code <= 67 or 80 <= code <= 82:
        return "rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if 95 <= code <= 99:
        return "storm"
    return "clouds"


def describe(code, is_day=True):
    icon, label = WMO.get(code, ("fas fa-cloud", "—"))
    return (icon if is_day else NIGHT_ICONS.get(icon, icon)), label


def location():
    """(latitude, longitude) of the hub's location (Settings > General), or None."""
    where = hub_location()
    if not where or not where.has_coordinates:
        return None
    return round(where.latitude, 3), round(where.longitude, 3)


def place_name():
    """Heading for the Now card: the place setting, else the hub's city, else the brand subtitle or name."""
    where = hub_location()
    brand = getattr(settings, "HUB_BRAND", None) or {}
    return (app_setting("weather", "place", "") or (where.name if where else "")
            or brand.get("subtitle") or brand.get("name") or "Weather")


def _air_quality(lat, lon):
    try:
        response = requests.get(AIR_QUALITY_URL, params={
            "latitude": lat, "longitude": lon, "current": "us_aqi", "timezone": "auto",
        }, timeout=15)
        response.raise_for_status()
        return (response.json().get("current") or {}).get("us_aqi")
    except Exception:
        log.warning("Open-Meteo air quality unavailable", exc_info=True)
        return None


def get_weather():
    """``{"forecast": <Open-Meteo payload>, "aqi": int | None}`` (cached), or None if unavailable."""
    where = location()
    if where is None:
        return None
    lat, lon = where
    key = f"weather:{lat},{lon}"
    data = cache.get(key)
    if data is None:
        try:
            response = requests.get(FORECAST_URL, params={
                "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 7,
                "current": CURRENT, "hourly": HOURLY, "daily": DAILY,
            }, timeout=15)
            response.raise_for_status()
            forecast = response.json()
        except Exception:
            log.exception("Open-Meteo forecast unavailable")
            return None
        data = {"forecast": forecast, "aqi": _air_quality(lat, lon)}
        cache.set(key, data, CACHE_TTL)
    return data
