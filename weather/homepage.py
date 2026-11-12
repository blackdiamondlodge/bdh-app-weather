from bdh_sdk import Status

from .forecast import describe, get_weather
from .views import Units, default_units


def status():
    data = get_weather()
    current = ((data or {}).get("forecast") or {}).get("current")
    if not current or current.get("temperature_2m") is None:
        return None
    temp = Units(default_units()).t(current["temperature_2m"])
    _, label = describe(current.get("weather_code"), bool(current.get("is_day", 1)))
    return Status(f"{temp}° · {label}")
