from datetime import datetime, timedelta, timezone

from django.shortcuts import render

from bdh_sdk.settings import app_setting

from .forecast import EFFECTS, describe, get_weather, place_name, weather_effect

COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
AQI_LEVELS = ((50, "good", "Good"), (100, "moderate", "Moderate"), (150, "usg", "Unhealthy for sensitive groups"))


def utc_now():
    return datetime.now(timezone.utc)


def default_units():
    return "imperial" if app_setting("weather", "units", "metric") == "imperial" else "metric"


class Units:
    def __init__(self, system):
        self.imperial = system == "imperial"
        self.system = "imperial" if self.imperial else "metric"
        self.temp = "°F" if self.imperial else "°C"
        self.speed = "mph" if self.imperial else "km/h"

    def t(self, c):
        if c is None:
            return None
        return round(c * 9 / 5 + 32) if self.imperial else round(c)

    def s(self, kph):
        if kph is None:
            return None
        return round(kph * 0.621371) if self.imperial else round(kph)


def _clock(t):
    return f"{t.hour % 12 or 12}:{t:%M} {'AM' if t.hour < 12 else 'PM'}"


def _hour(t):
    return f"{t.hour % 12 or 12} {'AM' if t.hour < 12 else 'PM'}"


def _aqi(value):
    if value is None:
        return None
    for limit, level, label in AQI_LEVELS:
        if value <= limit:
            return {"value": round(value), "level": level, "label": label}
    return {"value": round(value), "level": "bad", "label": "Unhealthy"}


def _next(times, now):
    """First of the (local ISO) times that's still ahead of now, as a clock string."""
    for iso in times:
        t = datetime.fromisoformat(iso)
        if t > now:
            return _clock(t)
    return None


def build_context(data, units, now=None):
    u = Units(units)
    context = {"units": u.system, "u": u, "place": place_name(), "ok": False}
    forecast = (data or {}).get("forecast")
    if not forecast or "current" not in forecast:
        return context

    now = now or utc_now()
    # Open-Meteo's times are local to the forecast location (timezone=auto).
    local = (now + timedelta(seconds=forecast.get("utc_offset_seconds", 0))).replace(tzinfo=None)
    c, hourly, daily = forecast["current"], forecast.get("hourly", {}), forecast.get("daily", {})
    is_day = bool(c.get("is_day", 1))
    icon, label = describe(c["weather_code"], is_day)

    days = []
    for i, day in enumerate(daily.get("time", [])):
        d = datetime.fromisoformat(day).date()
        if d < local.date():  # cached from before midnight
            continue
        day_icon, day_label = describe(daily["weather_code"][i])
        days.append({
            "name": "Today" if d == local.date() else f"{d:%a}",
            "date": f"{d:%b} {d.day}",
            "icon": day_icon, "label": day_label,
            "high_c": daily["temperature_2m_max"][i], "low_c": daily["temperature_2m_min"][i],
            "high": u.t(daily["temperature_2m_max"][i]), "low": u.t(daily["temperature_2m_min"][i]),
            "pop": daily["precipitation_probability_max"][i] or 0,
            "uv": round(daily["uv_index_max"][i]) if daily["uv_index_max"][i] is not None else None,
        })
    lows = [d["low_c"] for d in days if d["low_c"] is not None]
    highs = [d["high_c"] for d in days if d["high_c"] is not None]
    if lows and highs:
        week_lo, span = min(lows), (max(highs) - min(lows)) or 1
        for d in days:
            if d["low_c"] is not None and d["high_c"] is not None:
                d["left"] = f"{(d['low_c'] - week_lo) / span * 100:.1f}"
                d["width"] = f"{(d['high_c'] - d['low_c']) / span * 100:.1f}"

    this_hour = local.strftime("%Y-%m-%dT%H")
    hours = []
    for i, iso in enumerate(hourly.get("time", [])):
        if iso[:13] < this_hour:
            continue
        h_icon, _ = describe(hourly["weather_code"][i], bool(hourly["is_day"][i]))
        hours.append({
            "name": "Now" if not hours else _hour(datetime.fromisoformat(iso)),
            "icon": h_icon, "temp": u.t(hourly["temperature_2m"][i]),
            "pop": hourly["precipitation_probability"][i] or 0,
        })
        if len(hours) == 24:
            break

    direction = c.get("wind_direction_10m")
    today = days[0] if days and days[0]["name"] == "Today" else None
    context.update({
        "ok": True,
        "wx": weather_effect(c["weather_code"], is_day),
        "icon": icon,
        "label": label,
        "updated": _clock(datetime.fromisoformat(c["time"])) if c.get("time") else None,
        "temp": u.t(c.get("temperature_2m")),
        "feels_like": u.t(c.get("apparent_temperature")),
        "humidity": c.get("relative_humidity_2m"),
        "wind": {
            "speed": u.s(c.get("wind_speed_10m")), "gusts": u.s(c.get("wind_gusts_10m")),
            "from": COMPASS[round(direction / 45) % 8] if direction is not None else None,
            "rotation": (direction + 180) % 360 if direction is not None else None,
        },
        "aqi": _aqi((data or {}).get("aqi")),
        "today": today,
        "sunrise": _next(daily.get("sunrise", []), local),
        "sunset": _next(daily.get("sunset", []), local),
        "hours": hours,
        "days": days,
    })
    return context


def weather(request):
    units = request.GET.get("units") or default_units()
    context = build_context(get_weather(), units)
    if request.GET.get("wx") in EFFECTS:  # preview a background
        context["wx"] = request.GET["wx"]
    return render(request, "weather/weather.html", context)
