from bdh_sdk import PluginSpec, Setting, Tile
from bdh_sdk.plugins import CHOICE
from bdh_sdk.provides import STATS

STAT_KEYS = (
    ("temp", "Temperature"),
    ("feels_like", "Feels like"),
    ("sunrise", "Sunrise"),
    ("sunset", "Sunset"),
)

spec = PluginSpec(
    id="weather",
    app="weather",
    urls="weather.urls",
    status="weather.homepage.status",
    provides={STATS: "weather.provides.stats"},
    stat_keys=STAT_KEYS,
    tiles=(
        Tile(
            title="Weather",
            url_name="weather",
            icon="weather/hub/icon.svg",
            section="more",
            subtitle="Now, 24 hours & 7 days",
            order=10,
        ),
    ),
    settings=(
        Setting("place", "Place name", default="",
                help="Shown on the Now card. Defaults to the site's subtitle."),
        Setting("units", "Default units", type=CHOICE, default="metric",
                choices=(("metric", "Metric (°C, km/h)"), ("imperial", "Imperial (°F, mph)"))),
        Setting("latitude", "Latitude", default="",
                help="Leave blank to use the site's location."),
        Setting("longitude", "Longitude", default="",
                help="Leave blank to use the site's location."),
    ),
)
