"""The weather for the home screen's widget: GNOME Weather's first place,
fetched with libgweather (the same library the Weather app uses), at most
every 30 minutes, only while the screen is lit.

No place set in Weather: the widget says so, and a tap opens Weather to
add one. `describe` is pure (tests/test_weather.py).
"""

from gi.repository import Gio, GLib

EVERY_S = 30 * 60


def describe(temp, sky, place):
    """The widget's two lines from libgweather's strings (any may be None)."""
    first = temp or "--"
    second = ", ".join(part for part in (sky, place) if part)
    return first, second


class Weather:
    """Calls `changed()` when there is something new to show; `.lines`,
    `.icon` and `.place_set` say what."""

    def __init__(self, changed):
        self.changed = changed
        self.lines = ("--", "")
        self.icon = "weather-none-available-symbolic"
        self.place_set = False
        self.info = None
        self.last = 0
        self.gweather = None
        try:
            import gi

            gi.require_version("GWeather", "4.0")
            from gi.repository import GWeather

            self.gweather = GWeather
        except (ImportError, ValueError):
            self.gweather = None
        self.refresh()

    def _location(self):
        """GNOME Weather's first saved place, or None."""
        if self.gweather is None:
            return None
        source = Gio.SettingsSchemaSource.get_default()
        if source is None or source.lookup("org.gnome.Weather", True) is None:
            return None
        settings = Gio.Settings.new("org.gnome.Weather")
        places = settings.get_value("locations")
        if places.n_children() == 0:
            return None
        world = self.gweather.Location.get_world()
        if world is None:
            return None
        try:
            return world.deserialize(places.get_child_value(0).get_variant())
        except (GLib.Error, TypeError):
            return None

    def refresh(self, force=False):
        """Fetch again if the last fetch is old enough (or `force`)."""
        now = GLib.get_monotonic_time() / 1e6
        if not force and self.last and now - self.last < EVERY_S:
            return
        self.last = now
        location = self._location()
        self.place_set = location is not None
        if location is None:
            self.lines = ("", "")
            self.icon = "weather-few-clouds-symbolic"
            self.changed()
            return
        try:
            info = self.gweather.Info.new(location)
            info.set_application_id("org.duo2os.Shell")
            info.set_contact_info("duo2-os shell")
            info.set_enabled_providers(self.gweather.Provider.METAR | self.gweather.Provider.MET_NO)
            info.connect("updated", self._updated)
            self.info = info
            info.update()
        except (GLib.Error, AttributeError, TypeError) as e:
            print("duo-shell: weather: %s" % e, flush=True)

    def _updated(self, info):
        try:
            if not info.is_valid():
                return
            location = info.get_location()
            place = (
                location.get_city_name() or location.get_name() if location is not None else None
            )
            self.lines = describe(info.get_temp_summary(), info.get_sky(), place)
            self.icon = info.get_symbolic_icon_name() or "weather-few-clouds-symbolic"
        except (GLib.Error, AttributeError, TypeError) as e:
            print("duo-shell: weather: %s" % e, flush=True)
            return
        self.changed()
