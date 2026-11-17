"""Dark / Light / Automatic (Settings > Appearance; pure parts, tested in
tests/test_settings.py). The choice is ~/.config/duo2/appearance.conf;
`duo-appearance` (next to duo-settings) applies it:

- GTK 4 / libadwaita apps and, through the portal, Flatpaks: GSettings
  org.gnome.desktop.interface color-scheme, 'prefer-dark' or 'prefer-light'
  ('default' would leave apps that ask for dark, like this one, dark);
- GTK 3 apps: gtk-theme Adwaita-dark / Adwaita;
- Qt / KDE apps (Angelfish; QT_QPA_PLATFORMTHEME=kde): the colours of
  ~/.config/kdeglobals over the system's dark /etc/xdg/kdeglobals: light
  writes Breeze Light's colours there (a file this tool marks as its own),
  dark removes that file again; running ones are told (KGlobalSettings
  notifyChange, as Plasma does);
- Android (Waydroid): its night mode (`cmd uimode night`), through the root
  helper duo2-android-task, while Android runs (and at its next start).
The shell's bars, home screen and lock screen stay dark (AMOLED panels); the
on-screen keyboard (phosh-osk-stub, libadwaita) follows the apps.
"""

import os

MODES = ("dark", "light", "auto")
DEFAULT = {"mode": "dark", "light_from": 7 * 60, "dark_from": 19 * 60}
MARK = "# duo2-appearance:"
KDE_LIGHT = "/usr/share/color-schemes/BreezeLight.colors"


def config_path():
    return os.path.join(os.path.expanduser("~"), ".config", "duo2", "appearance.conf")


def parse_hhmm(text):
    """'07:30' -> 450; ValueError for anything else."""
    h, sep, m = str(text).strip().partition(":")
    if not sep or not h.isdigit() or not m.isdigit() or len(m) != 2:
        raise ValueError("not HH:MM: %r" % text)
    h, m = int(h), int(m)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError("not a time of day: %r" % text)
    return h * 60 + m


def hhmm(minutes):
    return "%02d:%02d" % (minutes // 60 % 24, minutes % 60)


def parse(text):
    prefs = dict(DEFAULT)
    for line in (text or "").splitlines():
        key, sep, value = line.split("#", 1)[0].strip().partition("=")
        key, value = key.strip(), value.strip()
        if not sep:
            continue
        if key == "mode" and value in MODES:
            prefs["mode"] = value
        elif key in ("light_from", "dark_from"):
            try:
                prefs[key] = parse_hhmm(value)
            except ValueError:
                pass
    return prefs


def text_of(prefs):
    return (
        "# duo2-os: Settings > Appearance (duo-appearance applies it)\n"
        "mode = %s\nlight_from = %s\ndark_from = %s\n"
        % (prefs["mode"], hhmm(prefs["light_from"]), hhmm(prefs["dark_from"]))
    )


def load(path=None):
    try:
        with open(path or config_path()) as f:
            return parse(f.read())
    except OSError:
        return dict(DEFAULT)


def save(prefs, path=None):
    path = path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        f.write(text_of(prefs))
    os.replace(path + ".tmp", path)


def scheme_for(prefs, minutes_now):
    """'dark' or 'light' for the setting at this time of day (minutes since
    midnight). Automatic: light from light_from until dark_from (either may
    be the earlier one: a span over midnight)."""
    mode = prefs.get("mode", "dark")
    if mode in ("dark", "light"):
        return mode
    lo, hi = prefs["light_from"], prefs["dark_from"]
    if lo == hi:
        return "dark"
    if lo < hi:
        return "light" if lo <= minutes_now < hi else "dark"
    return "dark" if hi <= minutes_now < lo else "light"


def gsettings_for(scheme):
    """org.gnome.desktop.interface keys and values."""
    if scheme == "light":
        return {"color-scheme": "prefer-light", "gtk-theme": "Adwaita"}
    return {"color-scheme": "prefer-dark", "gtk-theme": "Adwaita-dark"}


def kdeglobals_light(colors_text):
    """~/.config/kdeglobals for light Qt apps, from Breeze Light's colour
    scheme file (its [Colors:*] groups, as shell/duo-apps writes the dark
    system default from Breeze Dark's)."""
    out = [
        MARK + " light Breeze for Qt apps (Settings > Appearance); dark removes this file",
        "[General]",
        "ColorScheme=BreezeLight",
        "",
        "[KDE]",
        "widgetStyle=Breeze",
        "",
        "[Icons]",
        "Theme=Adwaita",
        "",
    ]
    keep = False
    for line in (colors_text or "").splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            keep = s.startswith("[Colors:")
        if keep:
            out.append(line.rstrip())
    return "\n".join(out).rstrip("\n") + "\n"


def is_ours(text):
    return (text or "").startswith(MARK)


def kde_action(scheme, current_text, colors_text):
    """What to do with ~/.config/kdeglobals: ("write", text), ("remove",
    None) or ("keep", reason). A file the user made (no mark) is never
    touched."""
    if current_text is not None and not is_ours(current_text):
        return ("keep", "your own ~/.config/kdeglobals")
    if scheme == "light":
        if not colors_text:
            return ("keep", "no Breeze Light colour scheme installed")
        want = kdeglobals_light(colors_text)
        return ("keep", "already light") if current_text == want else ("write", want)
    return ("remove", None) if current_text is not None else ("keep", "already dark")
