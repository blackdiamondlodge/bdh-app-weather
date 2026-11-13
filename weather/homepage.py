from bdh_sdk import WARN, Status

from .alerts import get_alerts
from .forecast import describe, get_weather
from .views import Units, default_units, utc_now


def status():
    data = get_weather()
    current = ((data or {}).get("forecast") or {}).get("current")
    if not current or current.get("temperature_2m") is None:
        return None
    temp = Units(default_units()).t(current["temperature_2m"])
    _, label = describe(current.get("weather_code"), bool(current.get("is_day", 1)))
    alerts = get_alerts(utc_now())
    if alerts:
        return Status(f"⚠ {alerts[0]['title']} · {temp}° {label}", level=WARN)
    return Status(f"{temp}° · {label}")
