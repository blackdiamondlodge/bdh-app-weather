from datetime import datetime, timedelta, timezone

from django.shortcuts import render

from bdh_sdk.units import system

from .alerts import get_alerts
from .forecast import EFFECTS, describe, get_weather, place_name, weather_effect

COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
AQI_LEVELS = ((50, "good", "Good"), (100, "moderate", "Moderate"), (150, "usg", "Unhealthy for sensitive groups"))
SNOW_STRIP_CM = 2  # forecast snow over the next 24 hours that switches the strip to snow amounts


def utc_now():
    return datetime.now(timezone.utc)


def default_units():
    """The hub's units (its °C/°F switch); the page's own toggle only changes the view."""
    return system()


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

    @property
    def snow_unit(self):
        return "in" if self.imperial else "cm"

    @property
    def rain_unit(self):
        return "in" if self.imperial else "mm"

    def snow(self, cm):
        return round(cm / 2.54, 1) if self.imperial else round(cm, 1)

    def rain(self, mm):
        return round(mm / 25.4, 2) if self.imperial else round(mm, 1)


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


def _num(value):
    """2.0 → "2", 2.5 → "2.5"."""
    return f"{value:g}"


def _compass(direction):
    return COMPASS[round(direction / 45) % 8] if direction is not None else None


def _alert_view(alert, offset, local_now):
    """An alert with its times as local clock strings for the banner."""
    def local(t):
        return (t.astimezone(timezone.utc) + offset).replace(tzinfo=None) if t else None

    view = dict(alert)
    issued, start, end = local(alert["issued"]), local(alert["start"]), local(alert["end"])
    view["issued_at"] = _clock(issued) if issued else None
    when = []
    if start and start > local_now:
        when.append(f"from {start:%a} {_clock(start)}" if start.date() != local_now.date() else f"from {_clock(start)}")
    if end:
        when.append(f"until {end:%a} {_clock(end)}" if end.date() != local_now.date() else f"until {_clock(end)}")
    view["when"] = " ".join(when)
    return view


def _alert_strip(alert, hours, hourly, offset, now, u):
    """Swap the 24-hour strip's rain chance for the number the alert is about (wind, snow or rain).

    Marks the hours inside the alert's window and returns the header chip, or None to keep the
    normal strip (e.g. a forecast cached before the extra hourly fields existed).
    """
    kind = alert["kind"]
    fields = {"wind": ("wind_speed_10m", "wind_gusts_10m", "wind_direction_10m"),
              "snow": ("snowfall",), "rain": ("precipitation",)}[kind]
    if not hours or not all(hourly.get(f) for f in fields):
        return None
    start = alert["start"] or now
    for h in hours:
        i = h["i"]
        local_start = datetime.fromisoformat(hourly["time"][i])
        begins = (local_start - offset).replace(tzinfo=timezone.utc)
        h["until"] = _hour(local_start + timedelta(hours=1))
        h["inwin"] = begins + timedelta(hours=1) > start and (alert["end"] is None or begins < alert["end"])
        if kind == "wind":
            direction = hourly["wind_direction_10m"][i]
            h.update(value=u.s(hourly["wind_speed_10m"][i]), gust=u.s(hourly["wind_gusts_10m"][i]),
                     wind_from=_compass(direction),
                     rotation=(direction + 180) % 360 if direction is not None else None)
        else:
            h["raw"] = (hourly["snowfall"] if kind == "snow" else hourly["precipitation"])[i] or 0
            h["value"] = u.snow(h["raw"]) if kind == "snow" else u.rain(h["raw"])
    window = [h for h in hours if h["inwin"]]
    if not window:  # the alert starts after the strip ends
        return None
    if kind == "wind":
        peaks = [h["value"] for h in window if h["value"] is not None]
        gusts = [h["gust"] for h in window if h["gust"] is not None]
        if not peaks:
            return None
        return f"Peak {max(peaks)} {u.speed}" + (f" · gusts {max(gusts)}" if gusts else "")
    top = max(h["value"] for h in hours) or 1
    for h in hours:
        h["bar"] = round(h["value"] / top * 100)
        h["value"] = _num(h["value"])
    raw = sum(h["raw"] for h in window)
    total = u.snow(raw) if kind == "snow" else u.rain(raw)
    unit = u.snow_unit if kind == "snow" else u.rain_unit
    return f"Total {_num(total)} {unit} by {window[-1]['until']}"


def _forecast_snow(hours, hourly, offset):
    """A stand-in snow "alert" spanning the snowy hours when the next 24 hours bring real snow, so
    places that care about snowfall (ski hills) see amounts without waiting for a warning."""
    snowfall = hourly.get("snowfall")
    if not hours or not snowfall:
        return None
    snowy = [h["i"] for h in hours if (snowfall[h["i"]] or 0) > 0]
    if sum(snowfall[i] for i in snowy) < SNOW_STRIP_CM:
        return None

    def utc(i):
        return (datetime.fromisoformat(hourly["time"][i]) - offset).replace(tzinfo=timezone.utc)

    return {"kind": "snow", "start": utc(snowy[0]), "end": utc(snowy[-1]) + timedelta(hours=1)}


def build_context(data, units, now=None, alerts=()):
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
            "i": i,
            "name": "Now" if not hours else _hour(datetime.fromisoformat(iso)),
            "icon": h_icon, "temp": u.t(hourly["temperature_2m"][i]),
            "pop": hourly["precipitation_probability"][i] or 0,
        })
        if len(hours) == 24:
            break

    offset = timedelta(seconds=forecast.get("utc_offset_seconds", 0))
    alerts = [_alert_view(a, offset, local) for a in alerts]
    strip = None
    lead = next((a for a in alerts if a["kind"]), None)
    if lead:
        chip = _alert_strip(lead, hours, hourly, offset, now, u)
        if chip:
            strip = {"kind": lead["kind"], "severity": lead["severity"], "chip": chip,
                     "unit": {"wind": u.speed, "snow": u.snow_unit, "rain": u.rain_unit}[lead["kind"]]}
    if not strip:
        snowfall = _forecast_snow(hours, hourly, offset)
        chip = snowfall and _alert_strip(snowfall, hours, hourly, offset, now, u)
        if chip:
            strip = {"kind": "snow", "severity": "forecast", "chip": chip, "unit": u.snow_unit}

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
        "alerts": alerts,
        "alert_sources": sorted({a["source"] for a in alerts}),
        "strip": strip,
    })
    return context


def weather(request):
    units = request.GET.get("units") or default_units()
    now = utc_now()
    context = build_context(get_weather(), units, now, alerts=get_alerts(now))
    if request.GET.get("wx") in EFFECTS:  # preview a background
        context["wx"] = request.GET["wx"]
    return render(request, "weather/weather.html", context)
