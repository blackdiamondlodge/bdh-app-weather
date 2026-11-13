"""Active weather alerts (watches, warnings, advisories, statements) for the hub's location.

Canada comes from Environment Canada's GeoMet API, the US from the National
Weather Service. Both are free and need no key. Alerts are normalised to
dicts (see :func:`_alert`) and cached for a few minutes.
"""
import logging
import re
from datetime import datetime

import requests
from django.core.cache import cache

from bdh_sdk.location import location as hub_location

log = logging.getLogger(__name__)

ECCC_URL = "https://api.weather.gc.ca/collections/weather-alerts/items"
NWS_URL = "https://api.weather.gov/alerts/active"
USER_AGENT = "bdh-app-weather (https://github.com/blackdiamondlodge/bdh-app-weather)"
CACHE_TTL = 5 * 60
NEAR = 0.01  # degrees around the hub for Environment Canada's bbox query

SEVERITIES = ("yellow", "orange", "red")  # least to most severe
NWS_SEVERITY = {"Extreme": "red", "Severe": "orange"}  # Moderate, Minor, Unknown → yellow
CANADA = {"ca", "can", "canada"}
US = {"us", "usa", "united states", "united states of america"}

ICONS = (
    ("chill", "fas fa-temperature-low"), ("cold", "fas fa-temperature-low"), ("frost", "fas fa-temperature-low"),
    ("heat", "fas fa-temperature-high"), ("fog", "fas fa-smog"), ("smoke", "fas fa-smog"),
    ("air quality", "fas fa-smog"), ("thunder", "fas fa-bolt"), ("tornado", "fas fa-tornado"),
    ("blizzard", "fas fa-wind"), ("snow", "fas fa-snowflake"), ("winter", "fas fa-snowflake"),
    ("freezing", "fas fa-icicles"), ("ice", "fas fa-icicles"), ("rain", "fas fa-cloud-showers-heavy"),
    ("flood", "fas fa-water"), ("wind", "fas fa-wind"),
)


def kind(name):
    """Which forecast number matters for this alert: ``"wind"``, ``"snow"``, ``"rain"`` or None."""
    n = name.lower()
    if "chill" in n:
        return None
    if "wind" in n or "blizzard" in n:
        return "wind"
    if "snow" in n or "winter storm" in n:
        return "snow"
    if "rain" in n or ("flood" in n and "coastal" not in n):
        return "rain"
    return None


def icon(name):
    n = name.lower()
    return next((cls for word, cls in ICONS if word in n), "fas fa-triangle-exclamation")


def _when(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# "What:\n..." (Environment Canada) or "* WHAT...text" (NWS).
_SECTION = re.compile(r"^(?:\*\s*([A-Z][A-Z ]+)\.\.\.|([A-Z][A-Za-z ]+):\s*$)", re.M)


def sections(text):
    """Split an alert's text into ``[(label, text)]``; unlabelled text becomes one section."""
    text = (text or "").strip()
    found = list(_SECTION.finditer(text))
    if not found:
        return [("", " ".join(text.split()))] if text else []
    out = []
    lead = text[:found[0].start()].strip()
    if lead:
        out.append(("", " ".join(lead.split())))
    for i, m in enumerate(found):
        body = text[m.end():found[i + 1].start() if i + 1 < len(found) else len(text)]
        label = (m.group(1) or m.group(2)).strip().capitalize()
        if body.strip():
            out.append((label, " ".join(body.split())))
    return out


def _alert(key, title, severity, text, source, issued, start, end):
    parts = sections(text)
    summary = next((body for label, body in parts if label.lower() == "what"), parts[0][1] if parts else "")
    return {
        "key": key, "title": title, "severity": severity, "rank": SEVERITIES.index(severity),
        "kind": kind(title), "icon": icon(title), "summary": summary,
        "sections": [(label, body) for label, body in parts if label.lower() != "what"],
        "source": source, "issued": issued, "start": start, "end": end,
    }


def parse_eccc(payload, now):
    alerts = {}
    for feature in (payload or {}).get("features", []):
        p = feature.get("properties") or {}
        name = (p.get("alert_name_en") or "").strip()
        if not name or (p.get("status_en") or "").lower() in ("ended", "cancelled"):
            continue
        end = _when(p.get("event_end_datetime")) or _when(p.get("expiration_datetime"))
        if end and end <= now:
            continue
        colour = (p.get("risk_colour_en") or "").lower()
        alert = _alert(
            key=p.get("alert_code") or name, title=name[:1].upper() + name[1:],
            severity=colour if colour in SEVERITIES else "yellow",  # statements have no colour
            text=p.get("alert_text_en"), source="Environment Canada",
            issued=_when(p.get("publication_datetime")), start=None, end=end,
        )
        # One alert is repeated for every forecast region it covers.
        if alert["key"] not in alerts or alert["rank"] > alerts[alert["key"]]["rank"]:
            alerts[alert["key"]] = alert
    return list(alerts.values())


def parse_nws(payload, now):
    alerts = {}
    for feature in (payload or {}).get("features", []):
        p = feature.get("properties") or {}
        name = (p.get("event") or "").strip()
        if not name or p.get("status") != "Actual" or p.get("messageType") == "Cancel":
            continue
        end = _when(p.get("ends")) or _when(p.get("expires"))
        if end and end <= now:
            continue
        text = "\n\n".join(t for t in (p.get("description"), p.get("instruction")) if t)
        alert = _alert(
            key=name, title=name, severity=NWS_SEVERITY.get(p.get("severity"), "yellow"), text=text,
            source=p.get("senderName") or "National Weather Service", issued=_when(p.get("sent")),
            start=_when(p.get("onset")) or _when(p.get("effective")), end=end,
        )
        if alert["key"] not in alerts or alert["rank"] > alerts[alert["key"]]["rank"]:
            alerts[alert["key"]] = alert
    return list(alerts.values())


def _fetch_eccc(lat, lon, now):
    response = requests.get(ECCC_URL, params={
        "f": "json", "lang": "en", "limit": 100,
        "bbox": f"{lon - NEAR},{lat - NEAR},{lon + NEAR},{lat + NEAR}",
    }, timeout=15)
    response.raise_for_status()
    return parse_eccc(response.json(), now)


def _fetch_nws(lat, lon, now):
    response = requests.get(NWS_URL, params={"point": f"{lat},{lon}", "status": "actual"},
                            headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"}, timeout=15)
    if response.status_code in (400, 404):  # outside the US
        return []
    response.raise_for_status()
    return parse_nws(response.json(), now)


def sources(country):
    c = (country or "").strip().lower()
    if c in CANADA:
        return (_fetch_eccc,)
    if c in US:
        return (_fetch_nws,)
    return (_fetch_eccc, _fetch_nws)


def get_alerts(now):
    """Active alerts for the hub's location, most severe first (cached); [] when there are none or on errors."""
    where = hub_location()
    if not where or not where.has_coordinates:
        return []
    lat, lon = round(where.latitude, 3), round(where.longitude, 3)
    key = f"weather:alerts:{lat},{lon}"
    alerts = cache.get(key)
    if alerts is None:
        alerts = []
        for fetch in sources(where.country):
            try:
                alerts += fetch(lat, lon, now)
            except Exception:
                log.warning("weather alerts unavailable from %s", fetch.__name__, exc_info=True)
        cache.set(key, alerts, CACHE_TTL)
    alerts = [a for a in alerts if not a["end"] or a["end"] > now]
    return sorted(alerts, key=lambda a: -a["rank"])
