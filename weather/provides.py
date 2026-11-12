"""Weather stats for other parts of the hub (e.g. the screensaver info strip)."""
from datetime import datetime, timedelta, timezone

from bdh_sdk.provides import Stat

from .forecast import get_weather
from .plugin import STAT_KEYS
from .views import Units, default_units, utc_now

LABELS = dict(STAT_KEYS)


def _next(times, tz, now):
    """First of the (local ISO) times that's still ahead of now, as an aware datetime."""
    for iso in times or ():
        t = datetime.fromisoformat(iso).replace(tzinfo=tz)
        if t > now:
            return t
    return None


def build_stats(data, units, now=None):
    forecast = (data or {}).get("forecast") or {}
    current = forecast.get("current") or {}
    daily = forecast.get("daily") or {}
    u = Units(units)
    out = []
    for order, key, field in ((10, "temp", "temperature_2m"), (20, "feels_like", "apparent_temperature")):
        value = u.t(current.get(field))
        if value is not None:
            out.append(Stat(key, LABELS[key], value=f"{value}{u.temp}", order=order))
    tz = timezone(timedelta(seconds=forecast.get("utc_offset_seconds", 0)))
    now = now or utc_now()
    sun = []
    for order, key in ((30, "sunrise"), (40, "sunset")):
        t = _next(daily.get(key), tz, now)
        if t is not None:
            out.append(Stat(key, LABELS[key], time=t, order=order))
            sun.append((t, LABELS[key]))
    if sun:
        t, label = min(sun)
        out.append(Stat("sun", label, time=t, order=35))
    return out


def stats():
    return build_stats(get_weather(), default_units())
