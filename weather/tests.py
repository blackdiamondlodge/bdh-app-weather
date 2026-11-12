from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.test import TestCase, override_settings, tag
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from weather.forecast import location, place_name
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
    @override_settings(HUB_LOCATION={"latitude": 47.5712345, "longitude": -122.2221}, HUB_BRAND={"subtitle": "Mercer Island"})
    def test_site_defaults(self):
        self.assertEqual(location(), (47.571, -122.222))
        self.assertEqual(place_name(), "Mercer Island")

    @override_settings(HUB_LOCATION={"latitude": 47.57, "longitude": -122.22})
    def test_app_settings_win(self):
        with patch.dict("os.environ", {"BDH_WEATHER_LATITUDE": "50.88", "BDH_WEATHER_LONGITUDE": "-119.89",
                                       "BDH_WEATHER_PLACE": "Sun Peaks"}):
            self.assertEqual(location(), (50.88, -119.89))
            self.assertEqual(place_name(), "Sun Peaks")

    @override_settings(HUB_LOCATION={})
    def test_no_location(self):
        self.assertIsNone(location())


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
    def status(self, data, env=None):
        with patch("weather.homepage.get_weather", return_value=data), patch.dict("os.environ", env or {}):
            from weather.homepage import status
            return status()

    def test_status(self):
        self.assertEqual(self.status(payload()).text, "7° · Overcast")
        self.assertEqual(self.status(payload(), {"BDH_HUB_UNITS": "imperial"}).text, "45° · Overcast")

    def test_unavailable(self):
        self.assertIsNone(self.status(None))


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
