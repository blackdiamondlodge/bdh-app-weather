from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from bdh_sdk.location import Location
from django.test import TestCase, override_settings, tag
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from weather.forecast import location, place_name
from weather import alerts as wa
from weather.views import build_context

# 2026-01-10 15:20 local in a UTC-8 place.
NOW = datetime(2026, 1, 10, 23, 20, tzinfo=timezone.utc)
OFFSET = -8 * 3600


def payload(aqi=42):
    days = [date(2026, 1, 9) + timedelta(days=i) for i in range(8)]  # starts yesterday: stale cache
    hours = [datetime(2026, 1, 10, 0) + timedelta(hours=i) for i in range(72)]
    return {
        "forecast": {
            "utc_offset_seconds": OFFSET,
            "current": {
                "time": "2026-01-10T15:15", "temperature_2m": 7.2, "apparent_temperature": 4.6,
                "relative_humidity_2m": 81, "is_day": 1, "weather_code": 3, "wind_speed_10m": 16.0,
                "wind_gusts_10m": 32.0, "wind_direction_10m": 200,
            },
            "hourly": {
                "time": [h.strftime("%Y-%m-%dT%H:%M") for h in hours],
                "temperature_2m": [5.0 + (i % 6) for i in range(72)],
                "weather_code": [61] * 72,
                "precipitation_probability": [10 * (i % 10) for i in range(72)],
                "is_day": [1 if 8 <= h.hour < 17 else 0 for h in hours],
            },
            "daily": {
                "time": [d.isoformat() for d in days],
                "weather_code": [3, 3, 61, 0, 71, 2, 45, 95],
                "temperature_2m_max": [9.0, 9.0, 11.0, 13.0, 4.0, 8.0, 10.0, 12.0],
                "temperature_2m_min": [2.0, 3.0, 5.0, 1.0, -2.0, 0.0, 4.0, 6.0],
                "precipitation_probability_max": [0, 35, 80, 5, 60, None, 10, 90],
                "uv_index_max": [1.0, 1.6, 0.8, 3.0, 1.0, 2.0, 2.0, 1.0],
                "sunrise": [f"{d.isoformat()}T07:55" for d in days],
                "sunset": [f"{d.isoformat()}T16:40" for d in days],
            },
        },
        "aqi": aqi,
    }


class BuildContextTests(TestCase):
    def test_metric(self):
        ctx = build_context(payload(), "metric", NOW)
        self.assertTrue(ctx["ok"])
        self.assertEqual((ctx["temp"], ctx["feels_like"], ctx["label"], ctx["wx"]), (7, 5, "Overcast", "clouds"))
        self.assertEqual(ctx["wind"], {"speed": 16, "gusts": 32, "from": "S", "rotation": 20})
        self.assertEqual(ctx["aqi"], {"value": 42, "level": "good", "label": "Good"})
        self.assertEqual((ctx["sunrise"], ctx["sunset"]), ("7:55 AM", "4:40 PM"))

    def test_hours_start_now_and_cover_a_day(self):
        hours = build_context(payload(), "metric", NOW)["hours"]
        self.assertEqual(len(hours), 24)
        self.assertEqual([h["name"] for h in hours[:3]], ["Now", "4 PM", "5 PM"])
        self.assertEqual(hours[9]["name"], "12 AM")
        self.assertEqual(hours[0]["pop"], 50)

    def test_days_drop_stale_yesterday_and_scale_bars_to_the_week(self):
        days = build_context(payload(), "metric", NOW)["days"]
        self.assertEqual([d["name"] for d in days], ["Today", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri"])
        self.assertEqual(days[0]["date"], "Jan 10")
        self.assertEqual(days[4]["pop"], 0)
        # Week runs -2..13 °C: Tue (-2..4) starts at the left edge, Mon tops out at 100%.
        self.assertEqual((days[3]["left"], days[3]["width"]), ("0.0", "40.0"))
        self.assertEqual(float(days[2]["left"]) + float(days[2]["width"]), 100.0)

    def test_imperial(self):
        ctx = build_context(payload(), "imperial", NOW)
        self.assertEqual((ctx["temp"], ctx["wind"]["speed"], ctx["u"].temp, ctx["u"].speed), (45, 10, "°F", "mph"))
        self.assertEqual((ctx["days"][0]["high"], ctx["days"][0]["low"]), (48, 37))

    def test_after_sunset_shows_tomorrows_sunrise(self):
        ctx = build_context(payload(), "metric", NOW + timedelta(hours=2))
        self.assertEqual(ctx["sunrise"], "7:55 AM")
        self.assertEqual(ctx["sunset"], "4:40 PM")  # tomorrow's

    def test_aqi_levels_and_missing(self):
        for value, level in ((80, "moderate"), (120, "usg"), (180, "bad")):
            self.assertEqual(build_context(payload(value), "metric", NOW)["aqi"]["level"], level)
        self.assertIsNone(build_context(payload(None), "metric", NOW)["aqi"])

    def test_unavailable(self):
        self.assertFalse(build_context(None, "metric", NOW)["ok"])
        self.assertFalse(build_context({"forecast": None, "aqi": 10}, "metric", NOW)["ok"])


class LocationTests(TestCase):
    def at(self, where):
        return patch("weather.forecast.hub_location", return_value=where)

    @override_settings(HUB_BRAND={"subtitle": "Lodge"})
    def test_hub_location(self):
        with self.at(Location(name="Sun Peaks", latitude=50.8812345, longitude=-119.9071, timezone="America/Vancouver")):
            self.assertEqual(location(), (50.881, -119.907))
            self.assertEqual(place_name(), "Sun Peaks")

    @override_settings(HUB_BRAND={"subtitle": "Mercer Island"})
    def test_place_setting_wins(self):
        with self.at(Location(name="Seattle", latitude=47.6, longitude=-122.3)), \
                patch.dict("os.environ", {"BDH_WEATHER_PLACE": "The Lodge"}):
            self.assertEqual(place_name(), "The Lodge")

    @override_settings(HUB_BRAND={"subtitle": "Mercer Island"})
    def test_no_location(self):
        with self.at(Location(timezone="America/Los_Angeles")):
            self.assertIsNone(location())
            self.assertEqual(place_name(), "Mercer Island")
        with self.at(None):
            self.assertIsNone(location())


@patch("weather.views.get_alerts", return_value=[])
@patch("weather.views.utc_now", return_value=NOW)
class ViewTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch("weather.views.get_weather", side_effect=lambda: payload())
    def test_page(self, *_):
        html = self.client.get("/weather/").content.decode()
        self.assertIn("Next 24 hours", html)
        self.assertIn("7-day forecast", html)
        self.assertIn('<div class="n">Now</div>', html)
        self.assertIn('class="mc-units" data-unit="metric"', html)
        self.assertIn('href="?units=imperial"', html)
        self.assertIn('data-wx="clouds"', html)

    @patch("weather.views.get_weather", side_effect=lambda: payload())
    def test_units_and_preview_override(self, *_):
        html = self.client.get("/weather/?units=imperial&wx=snow").content.decode()
        self.assertIn('class="mc-units" data-unit="imperial"', html)
        self.assertIn('href="?units=metric"', html)
        self.assertIn("45°", html)
        self.assertIn('data-wx="snow"', html)
        self.assertIn('data-wx="clouds"', self.client.get("/weather/?wx=<b>").content.decode())

    @patch.dict("os.environ", {"BDH_HUB_UNITS": "imperial"})
    @patch("weather.views.get_weather", side_effect=lambda: payload())
    def test_default_units_setting(self, *_):
        self.assertIn('data-unit="imperial"', self.client.get("/weather/").content.decode())

    @patch("weather.views.get_weather", return_value=None)
    def test_unavailable(self, *_):
        response = self.client.get("/weather/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("forecast is unavailable", response.content.decode())


class HomepageHookTests(TestCase):
    def status(self, data, env=None, alerts=None):
        with patch("weather.homepage.get_weather", return_value=data), patch.dict("os.environ", env or {}), \
                patch("weather.homepage.get_alerts", return_value=alerts or []), \
                patch("weather.homepage.utc_now", return_value=NOW):
            from weather.homepage import status
            return status()

    def test_status(self):
        self.assertEqual(self.status(payload()).text, "7° · Overcast")
        self.assertEqual(self.status(payload(), {"BDH_HUB_UNITS": "imperial"}).text, "45° · Overcast")

    def test_unavailable(self):
        self.assertIsNone(self.status(None))

    def test_alert_warns(self):
        from bdh_sdk import WARN
        s = self.status(payload(), alerts=[alert("Wind warning")])
        self.assertEqual((s.text, s.level), ("⚠ Wind warning · 7° Overcast", WARN))


class ProvidesTests(TestCase):
    def test_stats(self):
        from weather.plugin import STAT_KEYS
        from weather.provides import build_stats
        stats = {s.key: s for s in build_stats(payload(), "metric", NOW)}
        self.assertEqual(set(stats), {k for k, _ in STAT_KEYS})
        self.assertEqual((stats["temp"].value, stats["feels_like"].value), ("7°C", "5°C"))
        local = timezone(timedelta(seconds=OFFSET))
        # Today's sunrise has passed, so the next one is tomorrow's; sunset is still today.
        self.assertEqual(stats["sunrise"].time, datetime(2026, 1, 11, 7, 55, tzinfo=local))
        self.assertEqual(stats["sunset"].time, datetime(2026, 1, 10, 16, 40, tzinfo=local))
        self.assertEqual((stats["sun"].label, stats["sun"].time), ("Sunset", stats["sunset"].time))

    def test_next_sun_after_sunset_is_sunrise(self):
        from weather.provides import build_stats
        local = timezone(timedelta(seconds=OFFSET))
        evening = datetime(2026, 1, 10, 20, tzinfo=local)
        sun = {s.key: s for s in build_stats(payload(), "metric", evening)}["sun"]
        self.assertEqual((sun.label, sun.time), ("Sunrise", datetime(2026, 1, 11, 7, 55, tzinfo=local)))

    def test_imperial(self):
        from weather.provides import build_stats
        stats = {s.key: s for s in build_stats(payload(), "imperial", NOW)}
        self.assertEqual(stats["temp"].value, "45°F")

    def test_unavailable(self):
        from weather.provides import build_stats
        self.assertEqual(build_stats(None, "metric", NOW), [])

    def test_spec_declares_provider(self):
        from bdh_sdk.provides import STATS
        from weather.plugin import spec
        self.assertEqual(spec.provides, {STATS: "weather.provides.stats"})
        with patch("weather.provides.get_weather", return_value=payload()):
            from weather.provides import stats
            self.assertTrue(stats())


def alert(title, severity="orange", start=None, end=NOW + timedelta(hours=3), text="What:\nStrong winds.\n"):
    return wa._alert(key=title, title=title, severity=severity, text=text, source="Environment Canada",
                     issued=NOW - timedelta(hours=1), start=start, end=end)


def alert_payload(snow=None):
    data = payload()
    data["forecast"]["hourly"].update({
        "wind_speed_10m": [20.0 + i for i in range(72)],
        "wind_gusts_10m": [40.0 + i for i in range(72)],
        "wind_direction_10m": [270] * 72,
        "snowfall": snow or [0.5] * 72,
        "precipitation": [1.0] * 72,
    })
    return data


class AlertParseTests(TestCase):
    def test_kind(self):
        self.assertEqual([wa.kind(n) for n in ("Wind warning", "Blizzard warning", "Winter storm warning",
                                               "Snowfall warning", "Rainfall warning", "Extreme cold warning",
                                               "Wind chill advisory", "Fog advisory")],
                         ["wind", "wind", "snow", "snow", "rain", None, None, None])

    def test_sections(self):
        self.assertEqual(wa.sections("* WHAT...Southwest winds 40 mph.\n\n* WHERE...The mountains.\n"),
                         [("What", "Southwest winds 40 mph."), ("Where", "The mountains.")])
        self.assertEqual(wa.sections("What:\nFog.\n\nWhen:\nTonight.\n"), [("What", "Fog."), ("When", "Tonight.")])
        self.assertEqual(wa.sections("Just  some\ntext"), [("", "Just some text")])

    def test_eccc(self):
        def feature(code, name, colour, status="active", end="2026-01-11T12:00:00Z"):
            return {"properties": {"alert_code": code, "alert_name_en": name, "risk_colour_en": colour,
                                   "status_en": status, "event_end_datetime": end,
                                   "alert_text_en": "What:\nWinds gusting to 90 km/h.\n\nWhen:\nTonight.\n",
                                   "publication_datetime": "2026-01-10T20:00:00Z"}}
        found = wa.parse_eccc({"features": [
            feature("WW", "wind warning", "yellow"),
            feature("WW", "wind warning", "orange"),  # same alert, another region
            feature("SPS", "special weather statement", None),
            feature("FG", "fog advisory", "yellow", status="ended"),
            feature("RW", "rainfall warning", "red", end="2026-01-10T20:00:00Z"),  # over
        ]}, NOW)
        by_key = {a["key"]: a for a in found}
        self.assertEqual(set(by_key), {"WW", "SPS"})
        wind = by_key["WW"]
        self.assertEqual((wind["title"], wind["severity"], wind["kind"], wind["summary"]),
                         ("Wind warning", "orange", "wind", "Winds gusting to 90 km/h."))
        self.assertEqual(wind["sections"], [("When", "Tonight.")])
        self.assertEqual(by_key["SPS"]["severity"], "yellow")  # statements are never grey

    def test_nws(self):
        def feature(event, severity, ends="2026-01-11T12:00:00Z", **extra):
            props = {"event": event, "severity": severity, "status": "Actual", "messageType": "Alert",
                     "ends": ends, "description": "* WHAT...Dense fog.", "senderName": "NWS Seattle WA"}
            props.update(extra)
            return {"properties": props}
        found = wa.parse_nws({"features": [
            feature("High Wind Warning", "Severe"),
            feature("Dense Fog Advisory", "Moderate"),
            feature("Blizzard Warning", "Extreme", ends="2026-01-10T00:00:00Z"),
            feature("Winter Storm Watch", "Severe", messageType="Cancel"),
        ]}, NOW)
        self.assertEqual([(a["title"], a["severity"], a["source"]) for a in found],
                         [("High Wind Warning", "orange", "NWS Seattle WA"),
                          ("Dense Fog Advisory", "yellow", "NWS Seattle WA")])

    @patch("weather.alerts.requests.get", side_effect=OSError("down"))
    def test_get_alerts_survives_errors(self, _):
        cache.clear()
        with patch("weather.alerts.hub_location",
                   return_value=Location(name="Sun Peaks", latitude=50.88, longitude=-119.9, country="Canada")):
            self.assertEqual(wa.get_alerts(NOW), [])

    def test_get_alerts_without_location(self):
        with patch("weather.alerts.hub_location", return_value=None):
            self.assertEqual(wa.get_alerts(NOW), [])


class AlertStripTests(TestCase):
    def test_wind(self):
        ctx = build_context(alert_payload(), "metric", NOW, alerts=[alert("Wind warning")])
        self.assertEqual(ctx["strip"], {"kind": "wind", "severity": "orange", "chip": "Peak 38 km/h · gusts 58",
                                        "unit": "km/h"})
        hours = ctx["hours"]
        self.assertEqual([h["inwin"] for h in hours[:5]], [True, True, True, True, False])
        self.assertEqual((hours[0]["value"], hours[0]["gust"], hours[0]["wind_from"], hours[0]["rotation"]),
                         (35, 55, "W", 90))
        self.assertEqual(ctx["alerts"][0]["when"], "until 6:20 PM")

    def test_snow_total(self):
        strip = build_context(alert_payload(), "metric", NOW, alerts=[alert("Snowfall warning")])["strip"]
        self.assertEqual(strip["chip"], "Total 2 cm by 7 PM")

    def test_rain_imperial(self):
        ctx = build_context(alert_payload(), "imperial", NOW, alerts=[alert("Rainfall warning", "yellow")])
        self.assertEqual((ctx["strip"]["chip"], ctx["strip"]["unit"]), ("Total 0.16 in by 7 PM", "in"))
        self.assertEqual(ctx["hours"][0]["value"], "0.04")

    def test_alert_after_the_strip_keeps_rain_chance(self):
        later = alert("Wind warning", start=NOW + timedelta(hours=30), end=NOW + timedelta(hours=40))
        ctx = build_context(alert_payload([0] * 72), "metric", NOW, alerts=[later])
        self.assertIsNone(ctx["strip"])
        self.assertEqual(ctx["alerts"][0]["when"], "from Sun 9:20 PM until Mon 7:20 AM")

    def test_forecast_snow_without_alert(self):
        snow = [0] * 72
        snow[17:21] = [1.0] * 4  # 5 PM to 9 PM, the strip starts at 3 PM
        ctx = build_context(alert_payload(snow), "metric", NOW)
        self.assertEqual(ctx["strip"], {"kind": "snow", "severity": "forecast", "chip": "Total 4 cm by 9 PM",
                                        "unit": "cm"})
        self.assertEqual([h["inwin"] for h in ctx["hours"][:7]], [False, False, True, True, True, True, False])

    def test_light_forecast_snow_keeps_rain_chance(self):
        snow = [0] * 72
        snow[17:20] = [0.5] * 3
        self.assertIsNone(build_context(alert_payload(snow), "metric", NOW)["strip"])

    def test_old_cache_without_fields_keeps_rain_chance(self):
        ctx = build_context(payload(), "metric", NOW, alerts=[alert("Wind warning")])
        self.assertIsNone(ctx["strip"])
        self.assertEqual(len(ctx["alerts"]), 1)

    def test_fog_shows_banner_only(self):
        ctx = build_context(alert_payload([0] * 72), "metric", NOW, alerts=[alert("Fog advisory", "yellow")])
        self.assertIsNone(ctx["strip"])
        self.assertEqual((ctx["alerts"][0]["icon"], ctx["alert_sources"]), ("fas fa-smog", ["Environment Canada"]))

    @patch("weather.views.utc_now", return_value=NOW)
    @patch("weather.views.get_weather", side_effect=lambda: alert_payload())
    def test_page_shows_banner_and_strip(self, *_):
        with patch("weather.views.get_alerts", return_value=[alert("Wind warning")]):
            html = self.client.get("/weather/").content.decode()
        self.assertIn('class="wa-alert orange"', html)
        self.assertIn("Wind warning", html)
        self.assertIn("Peak 38 km/h · gusts 58", html)
        self.assertIn("wa-hour inwin", html)
        self.assertIn("alerts by Environment Canada", html)
        self.assertNotIn("fa-droplet", html.split('id="wa-hours"')[1].split("</section>")[0])


@tag("selenium")
class HourlySwipeTest(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from tests.selenium_helpers import get_chrome_options
        cls.browser = webdriver.Chrome(options=get_chrome_options())
        cls.browser.set_window_size(1280, 900)

    @classmethod
    def tearDownClass(cls):
        cls.browser.quit()
        super().tearDownClass()

    @patch("weather.views.utc_now", return_value=NOW)
    @patch("weather.views.get_weather", side_effect=lambda: payload())
    def test_edges_follow_the_scroll(self, *_):
        self.browser.get(f"{self.live_server_url}/weather/")
        edge = lambda side: self.browser.find_element(By.ID, f"wa-edge-{side}").get_attribute("class")
        WebDriverWait(self.browser, 10).until(lambda d: d.find_element(By.ID, "wa-hours"))
        self.assertIn("hidden", edge("l"))
        self.assertNotIn("hidden", edge("r"))
        self.browser.execute_script("var s = document.getElementById('wa-hours'); s.scrollLeft = s.scrollWidth;")
        WebDriverWait(self.browser, 5).until(lambda d: "hidden" in edge("r"), "right edge should hide at the end")
        self.assertNotIn("hidden", edge("l"))
