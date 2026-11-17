"""Settings > Appearance: dark or light apps, or by the time of day
(appearance.py has what follows the choice; duo-appearance applies it)."""

from . import appearance as ap
from .main import Page, _, combo_row, run_async, select_quietly

STYLES = ["dark", "light", "auto"]
LIGHT_FROM = [h * 60 for h in range(5, 12)]  # 05:00 .. 11:00
DARK_FROM = [h * 60 for h in range(16, 24)]  # 16:00 .. 23:00
TOOL = "duo-appearance"


def nearest(options, value):
    return min(range(len(options)), key=lambda i: abs(options[i] - value))


class AppearancePage(Page):
    icon = "preferences-desktop-appearance-symbolic"
    title = _("Appearance")

    def build(self):
        g = self.group(
            _("Style"),
            _(
                "The apps follow it: GNOME apps and Flatpaks at once, KDE apps such as Angelfish at once "
                "or when they start again, Android apps while Android runs (or at its next start). "
                "The bars, the home screen and the lock screen stay dark: black pixels are off on "
                "these panels."
            ),
        )
        self.style = combo_row(
            _("Style"),
            [_("Dark"), _("Light"), _("Automatic")],
            on_select=lambda i: self.changed(),
            subtitle=_("Automatic: light by day, dark in the evening"),
        )
        g.add(self.style)
        self.light = combo_row(
            _("Light from"), [ap.hhmm(m) for m in LIGHT_FROM], on_select=lambda i: self.changed()
        )
        self.dark = combo_row(
            _("Dark from"), [ap.hhmm(m) for m in DARK_FROM], on_select=lambda i: self.changed()
        )
        g.add(self.light)
        g.add(self.dark)

    def shown(self):
        prefs = ap.load()
        select_quietly(self.style, STYLES.index(prefs["mode"]))
        select_quietly(self.light, nearest(LIGHT_FROM, prefs["light_from"]))
        select_quietly(self.dark, nearest(DARK_FROM, prefs["dark_from"]))
        self.show_times(prefs["mode"])

    def show_times(self, mode):
        self.light.set_visible(mode == "auto")
        self.dark.set_visible(mode == "auto")

    def choice(self):
        return (
            STYLES[self.style.get_selected()],
            LIGHT_FROM[self.light.get_selected()],
            DARK_FROM[self.dark.get_selected()],
        )

    def changed(self):
        mode, light_from, dark_from = self.choice()
        self.show_times(mode)

        def done(ok, out):
            if not ok:
                self.toast(_("Could not change the style"))

        run_async(
            [
                TOOL,
                "set",
                mode,
                "--light-from",
                ap.hhmm(light_from),
                "--dark-from",
                ap.hhmm(dark_from),
            ],
            done,
        )
