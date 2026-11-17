"""The Battery page: the level and the charger, "Stay awake while charging"
(duo2-autosleep's, duosettings/sleep.py), and Android-style battery usage
since the last full charge (the collector in battery/, duo2-battery-stats;
duosettings/battery.py reads its report)."""

import gi

gi.require_version("PangoCairo", "1.0")
from gi.repository import Adw, Gio, GLib, Gtk, PangoCairo  # noqa: E402

from . import battery, sleep  # noqa: E402
from .main import Page, _, ask, set_quietly, switch_row  # noqa: E402
from .system import HELPER  # noqa: E402

GRAPH_H = 132  # the plot
STRIP_H = 7  # a strip under it (screen on, asleep)
LABEL_H = 18


class BatteryGraph(Gtk.DrawingArea):
    """The battery level since the window's start: charging shaded, the
    screen's lit periods (and suspended ones) as strips under the plot."""

    def __init__(self):
        super().__init__()
        self.report = None
        self.set_content_height(GRAPH_H + 2 * STRIP_H + LABEL_H + 18)
        self.set_hexpand(True)
        self.set_draw_func(self.draw)
        self.set_accessible_role(Gtk.AccessibleRole.IMG)

    def set_report(self, rep):
        self.report = rep
        self.queue_draw()

    def draw(self, _area, cr, width, height):
        rep = self.report or {}
        fg = self.get_color()
        try:
            accent = Adw.StyleManager.get_default().get_accent_color_rgba()
            acc = (accent.red, accent.green, accent.blue)
        except AttributeError:
            acc = (0.21, 0.52, 0.89)
        green = (0.18, 0.76, 0.49)
        yellow = (0.96, 0.83, 0.18)
        left, right = 4, 44  # room for the 100 / 50 / 0 labels at the right
        w = max(10, width - left - right)
        g = battery.graph_geometry(rep, w, GRAPH_H)
        top = 4
        # charging periods
        cr.set_source_rgba(*green, 0.18)
        for x0, x1 in g["charging"]:
            cr.rectangle(left + x0, top, max(1.0, x1 - x0), GRAPH_H)
        cr.fill()
        # grid
        cr.set_line_width(1)
        for lvl in (0, 50, 100):
            y = top + GRAPH_H - lvl / 100.0 * GRAPH_H
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.15)
            cr.move_to(left, round(y) + 0.5)
            cr.line_to(left + w, round(y) + 0.5)
            cr.stroke()
            self.text(cr, "%d %%" % lvl, left + w + 4, y - 7, fg, 0.6, size=0.75)
        # the level
        pts = g["line"]
        if pts:
            cr.set_source_rgba(*acc, 0.18)
            cr.move_to(left + pts[0][0], top + GRAPH_H)
            for x, y in pts:
                cr.line_to(left + x, top + y)
            cr.line_to(left + pts[-1][0], top + GRAPH_H)
            cr.close_path()
            cr.fill()
            cr.set_source_rgba(*acc, 1.0)
            cr.set_line_width(2)
            cr.move_to(left + pts[0][0], top + pts[0][1])
            for x, y in pts[1:]:
                cr.line_to(left + x, top + y)
            cr.stroke()
        # strips: screen on, then asleep
        sy = top + GRAPH_H + 6
        for spans, col in ((g["screen"], yellow), (g["suspended"], (0.6, 0.6, 0.7))):
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.08)
            cr.rectangle(left, sy, w, STRIP_H - 2)
            cr.fill()
            cr.set_source_rgba(*col, 0.9)
            for x0, x1 in spans:
                cr.rectangle(left + x0, sy, max(1.5, x1 - x0), STRIP_H - 2)
            cr.fill()
            sy += STRIP_H
        # the time axis and a legend
        ly = sy + 2
        now = rep.get("generated")
        self.text(cr, battery.clock(g["t0"], now), left, ly, fg, 0.6, size=0.75)
        self.text(cr, _("Now"), left + w, ly, fg, 0.6, size=0.75, align=1.0)
        lx = left + w * 0.30
        for label, col in ((_("Charging"), green), (_("Screen on"), yellow)):
            cr.set_source_rgba(*col, 0.9)
            cr.rectangle(lx, ly + 4, 9, 9)
            cr.fill()
            lx += 13
            lx += self.text(cr, label, lx, ly, fg, 0.7, size=0.75) + 14

    def text(self, cr, s, x, y, fg, alpha, size=1.0, align=0.0):
        layout = PangoCairo.create_layout(cr)
        desc = self.get_pango_context().get_font_description().copy()
        desc.set_size(int(desc.get_size() * size))
        layout.set_font_description(desc)
        layout.set_text(s, -1)
        tw, _th = layout.get_pixel_size()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, alpha)
        cr.move_to(x - tw * align, y)
        PangoCairo.show_layout(cr, layout)
        return tw


class BatteryPage(Page):
    icon = "battery-good-symbolic"
    title = _("Battery")

    def build(self):
        self.report = None
        self.include_charger = False
        self.lookup = None
        self.rows = []
        self.busy = False

        g = self.group()
        self.status_row = Adw.ActionRow(use_markup=False)
        self.status_icon = Gtk.Image.new_from_icon_name("battery-symbolic")
        self.status_icon.set_pixel_size(32)
        self.status_row.add_prefix(self.status_icon)
        self.level_label = Gtk.Label(xalign=1)
        self.level_label.add_css_class("big-status")
        self.status_row.add_suffix(self.level_label)
        g.add(self.status_row)

        g = self.group(_("Sleep"))
        self.awake_switch = switch_row(
            _("Stay awake while charging"),
            _(
                "The screens still turn off. While plugged in, the phone does not go to sleep, "
                "so it stays reachable over the network"
            ),
            on_toggle=self.set_stay_awake,
        )
        g.add(self.awake_switch)
        self.sleep_note = Adw.ActionRow(use_markup=False, title=_("Automatic sleep"))
        self.sleep_note.set_subtitle_lines(3)
        self.sleep_note.set_visible(False)
        g.add(self.sleep_note)

        self.usage_group = self.group(_("Battery usage"))
        graph_row = Adw.PreferencesRow(activatable=False)
        self.graph = BatteryGraph()
        self.graph.set_margin_start(10)
        self.graph.set_margin_end(10)
        self.graph.set_margin_top(10)
        self.graph.set_margin_bottom(4)
        graph_row.set_child(self.graph)
        self.usage_group.add(graph_row)
        self.summary_row = Adw.ActionRow(use_markup=False, title=_("Since the last full charge"))
        self.summary_row.set_subtitle_lines(3)
        self.usage_group.add(self.summary_row)
        self.charger_switch = switch_row(
            _("Include time on the charger"),
            _("Estimates for the time the phone was plugged in"),
            on_toggle=self.set_include_charger,
        )
        self.usage_group.add(self.charger_switch)

        self.apps_group = self.group(_("Apps"), _("Estimated share of the battery used"))
        self.system_group = self.group(_("System"))

        about = self.group(_("About these numbers"))
        self.quality_row = Adw.ActionRow(use_markup=False, title=_("Estimates"))
        self.quality_row.set_subtitle_lines(4)
        about.add(self.quality_row)
        self.profile_row = Adw.ActionRow(use_markup=False, title=_("Power figures"))
        self.profile_row.set_subtitle_lines(4)
        about.add(self.profile_row)
        privacy = Adw.ActionRow(use_markup=False, title=_("Privacy"))
        privacy.set_subtitle(
            _(
                "Which apps ran when stays on this phone, kept for 7 days, "
                "readable only by you and the system. Nothing is sent anywhere."
            )
        )
        privacy.set_subtitle_lines(4)
        about.add(privacy)
        clear = Adw.ButtonRow(title=_("Clear usage history"))
        clear.add_css_class("destructive-action")
        clear.connect("activated", lambda _r: self.ask_clear())
        about.add(clear)

    # -- data ---------------------------------------------------------------------

    def shown(self):
        self.show_sleep()
        if self.lookup is None:
            self.lookup = battery.desktop_lookup_factory(Gio)
        rep = battery.load_report()
        if rep:
            self.show_report(rep)
        elif self.report is None:
            self.show_missing(_("Loading..."))
        self.every(60, self.refresh)

    def refresh(self):
        self.show_sleep()
        if self.busy:
            return
        if self.report is not None and not battery.panels_lit():
            return  # left open with the screen off: no refresh
        self.busy = True
        from .main import run_async

        def done(ok, out):
            self.busy = False
            rep = battery.load_report(text=out) if ok and out.strip() else battery.load_report()
            if rep:
                self.show_report(rep)
            elif self.report is None:
                self.show_missing(
                    _(
                        "Battery usage is not being collected on this phone "
                        "(duo2-battery-stats is not running)."
                    )
                )

        run_async(["sudo", "-n", HELPER, "battery", "refresh"], done)

    def show_sleep(self):
        set_quietly(self.awake_switch, sleep.stay_awake_charging())
        what, end = sleep.held()
        if what == "off":
            self.sleep_note.set_subtitle(_("Off: the phone does not go to sleep by itself"))
        elif what == "until":
            self.sleep_note.set_subtitle(
                _("Kept awake for remote work until %s") % battery.clock(end)
            )
        self.sleep_note.set_visible(what is not None)

    def set_stay_awake(self, on):
        from .main import run_async

        def done(ok, _out):
            if not ok:
                self.toast(_("Could not change the setting"))
            self.show_sleep()

        run_async(sleep.switch_argv(on), done)

    def set_include_charger(self, on):
        self.include_charger = on
        if self.report:
            self.show_report(self.report)

    def ask_clear(self):
        def go():
            from .main import run_async

            def done(ok, out):
                rep = battery.load_report(text=out) if ok and out.strip() else None
                if rep:
                    self.show_report(rep)
                    self.toast(_("Usage history cleared"))
                else:
                    self.toast(_("Could not clear the usage history"))

            run_async(["sudo", "-n", HELPER, "battery", "clear"], done)

        ask(
            self.window,
            _("Clear usage history?"),
            _(
                "The battery graph and the use of every app since the last full charge are deleted. "
                "Collecting starts again from now."
            ),
            _("Clear"),
            go,
            destructive=True,
        )

    # -- showing ---------------------------------------------------------------------

    def clear_rows(self):
        for group, row in self.rows:
            group.remove(row)
        self.rows = []

    def add_row(self, group, row):
        group.add(row)
        self.rows.append((group, row))

    def show_missing(self, text):
        self.clear_rows()
        self.status_row.set_title(_("Battery"))
        self.status_row.set_subtitle(text)
        self.level_label.set_label("")
        self.summary_row.set_subtitle("")
        self.graph.set_report(None)

    def show_report(self, rep):
        self.report = rep
        level, state, detail = battery.status_line(rep)
        self.level_label.set_label(level)
        self.status_row.set_title(state)
        self.status_row.set_subtitle(detail)
        b = rep.get("battery") or {}
        lvl = b.get("level")
        if lvl is not None:
            n = min(100, int(round(lvl / 10.0)) * 10)
            suffix = ("-charged" if n == 100 else "-charging") if b.get("charger") else ""
            self.status_icon.set_from_icon_name("battery-level-%d%s-symbolic" % (n, suffix))
        self.graph.set_report(rep)
        title, sub = battery.summary_line(rep, self.include_charger)
        self.summary_row.set_title(title)
        self.summary_row.set_subtitle(sub)
        self.quality_row.set_subtitle(battery.quality_line(rep))
        self.profile_row.set_subtitle(battery.profile_line(rep))

        self.clear_rows()
        merged = battery.merge_entries(rep.get("entries") or {}, self.include_charger)
        rows, total = battery.ranked(battery.resolve(merged, self.lookup or (lambda *_a: None)))
        apps = [r for r in rows if r["section"] == "apps"]
        system = [r for r in rows if r["section"] == "system"]
        if not rows:
            empty = Adw.ActionRow(use_markup=False, title=_("Nothing to show yet"))
            empty.set_subtitle(
                _(
                    "The phone has not been on battery since the statistics started. "
                    'Switch on "Include time on the charger" to see the estimates '
                    "for the time plugged in."
                )
                if not self.include_charger
                else _("The first numbers come after a few minutes.")
            )
            empty.set_subtitle_lines(4)
            self.add_row(self.apps_group, empty)
            return
        for group, lst in ((self.apps_group, apps), (self.system_group, system)):
            if not lst:
                r = Adw.ActionRow(use_markup=False, title=_("None"))
                self.add_row(group, r)
            for r in lst:
                self.add_row(group, self.entry_row(r))

    def entry_row(self, r):
        row = Adw.ActionRow(use_markup=False, title=r["name"])
        sub = battery.row_subtitle(r)
        if sub:
            row.set_subtitle(sub)
        try:
            gicon = Gio.Icon.new_for_string(r["icon"])
            img = Gtk.Image.new_from_gicon(gicon)
        except GLib.Error:
            img = Gtk.Image.new_from_icon_name(battery.APP_ICON)
        img.set_pixel_size(
            32 if r["section"] == "apps" and not r["icon"].endswith("-symbolic") else 20
        )
        row.add_prefix(img)
        pct = Gtk.Label(label=battery.pct_label(r["share"]), xalign=1)
        pct.add_css_class("numeric")
        row.add_suffix(pct)
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        row.set_activatable(True)
        row.connect("activated", lambda _w, r=r: self.open_details(r))
        return row

    def open_details(self, r):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup()
        # the group's title and description are markup ("<1 %")
        g.set_title(
            GLib.markup_escape_text(_("%s of the battery used") % battery.pct_label(r["share"]))
        )
        g.set_description(GLib.markup_escape_text(battery.explain(r)))
        page.add(g)
        for title, value in battery.details(r):
            row = Adw.ActionRow(use_markup=False, title=title.strip(), subtitle=value)
            row.add_css_class("property")
            if title.startswith("  "):
                row.set_margin_start(18)
            g.add(row)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        tv.set_content(page)
        d = Adw.Dialog(title=r["name"], child=tv)
        d.set_content_width(480)
        d.set_content_height(560)
        d.present(self.window)
        return d
