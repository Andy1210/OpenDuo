"""The ringtone (Duo Settings > Sound), through feedbackd the proper way
(pure parts tested in tests/test_ringtone.py).

GNOME Calls rings with feedbackd's `phone-incoming-call` event (its app id
org.gnome.Calls, timeout 0: feedbackd loops it until the call stops
ringing). What the event plays is the feedback theme's: the phone's theme
(/usr/share/feedbackd/themes/microsoft,surface-duo2.json: the vibrations)
over feedbackd's default theme, whose `full` profile plays the sound
theme's phone-incoming-call (sound-theme-freedesktop's
/usr/share/sounds/freedesktop/stereo/phone-incoming-call.oga).

Another ringtone is a user theme (feedbackd 0.8's "custom theme" way): a
file in ~/.config/feedbackd/themes/ whose parent is the device theme
(`$device`: the phone's theme, then the default), with one entry: the
`full` profile's phone-incoming-call as a Sound feedback with `file-name`
(feedbackd plays it through GSound / libcanberra, which reads Ogg Vorbis
and WAV). GSettings org.sigxcpu.feedbackd `theme` names it; feedbackd loads
a theme when that key changes, so every change writes the theme under the
other of two names (duo2-ring-a / duo2-ring-b) and points the key at it.
The default ringtone is the key reset and our theme files gone. The sound
mode stays as it was: `quiet` and `silent` keep the device theme's entries,
so "Vibration only" still only vibrates.

The choices: every sound theme installed under XDG data dirs/sounds that
has a phone-incoming-call (Debian's sound-theme-freedesktop is the one on
the phone; another theme package adds its own), a few other sounds of
those themes that work as a ring, and the user's own file: copied
(converted by ffmpeg when it is not Ogg Vorbis or WAV, at most
OWN_MAX_S seconds) to ~/.local/share/duo2/ringtones/.
"""

import json
import os

SCHEMA = "org.sigxcpu.feedbackd"
EVENT = "phone-incoming-call"
THEME_NAMES = ("duo2-ring-a", "duo2-ring-b")
EXTS = (".oga", ".ogg", ".wav")
OWN_MAX_S = 60
OWN_NAME = "own-ringtone.oga"
# Other sounds of a sound theme that work as a ringtone (they are short:
# feedbackd plays them again and again while the phone rings).
OTHERS = (
    ("alarm-clock-elapsed", "Alarm clock"),
    ("phone-outgoing-calling", "Ringback"),
    ("bell", "Bell"),
    ("complete", "Chime"),
    ("message-new-instant", "Message"),
    ("window-attention", "Attention"),
)
DEFAULT_THEME = "freedesktop"


def config_home():
    return os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")


def data_home():
    return os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )


def data_dirs():
    dirs = [data_home()]
    dirs += [
        d
        for d in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
        if d
    ]
    return dirs


def themes_dir(base=None):
    return os.path.join(base or config_home(), "feedbackd", "themes")


def own_dir(base=None):
    return os.path.join(base or data_home(), "duo2", "ringtones")


def theme_label(index_text, fallback):
    """A sound theme's Name from its index.theme (the [Sound Theme] group)."""
    group = None
    for line in (index_text or "").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            group = line[1:-1]
        elif group == "Sound Theme" and line.startswith("Name=") and line[5:].strip():
            return line[5:].strip()
    return fallback


def theme_dirs(index_text):
    """A sound theme's sound directories (Directories=, else stereo)."""
    group = None
    for line in (index_text or "").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            group = line[1:-1]
        elif group == "Sound Theme" and line.startswith("Directories="):
            dirs = [d.strip() for d in line[len("Directories=") :].split(",") if d.strip()]
            if dirs:
                return dirs
    return ["stereo"]


def find_sound(theme_root, subdirs, event, listdir=os.listdir):
    for sub in subdirs:
        d = os.path.join(theme_root, sub)
        try:
            names = set(listdir(d))
        except OSError:
            continue
        for ext in EXTS:
            if event + ext in names:
                return os.path.join(d, event + ext)
    return None


def choices(dirs=None, own=None, read=None, listdir=os.listdir):
    """[(key, label, path)]: "default" first (path: the file it plays, or
    None), then each installed sound theme's ringtone, the other ring-like
    sounds, and the user's own file (`own`: its path, when there is one)."""
    read = read or _read
    dirs = dirs or data_dirs()
    out = []
    seen_themes = set()
    default_path = None
    themes = []
    for base in dirs:
        root = os.path.join(base, "sounds")
        try:
            names = sorted(listdir(root))
        except OSError:
            continue
        for name in names:
            if name in seen_themes:
                continue
            index = read(os.path.join(root, name, "index.theme"))
            if index is None:
                continue
            seen_themes.add(name)
            themes.append(
                (name, os.path.join(root, name), theme_label(index, name), theme_dirs(index))
            )
    themes.sort(key=lambda t: (t[0] != DEFAULT_THEME, t[2].lower()))
    rings, others = [], []
    for name, root, label, subdirs in themes:
        ring = find_sound(root, subdirs, EVENT, listdir)
        if ring:
            if name == DEFAULT_THEME and default_path is None:
                default_path = ring
            else:
                rings.append(("theme:%s" % name, "Phone ringing (%s)" % label, ring))
        for event, what in OTHERS:
            path = find_sound(root, subdirs, event, listdir)
            if path:
                others.append(
                    (
                        "sound:%s:%s" % (name, event),
                        what if name == DEFAULT_THEME else "%s (%s)" % (what, label),
                        path,
                    )
                )
    out.append(("default", "Phone ringing (default)", default_path))
    out += rings + others
    if own:
        out.append(("own", "Your sound: %s" % os.path.basename(own), own))
    return out


def theme_json(name, sound_path):
    """Our user theme: the device theme with the ringtone replaced."""
    return {
        "name": name,
        "parent-name": "$device",
        "profiles": [
            {
                "name": "full",
                "feedbacks": [{"event-name": EVENT, "type": "Sound", "file-name": sound_path}],
            }
        ],
    }


def next_name(current):
    """The theme name to write next (feedbackd reloads when the key changes)."""
    return THEME_NAMES[1] if current == THEME_NAMES[0] else THEME_NAMES[0]


def current_path(theme_key, base=None, read=None):
    """The file our theme rings with, or None (the default ringtone)."""
    if theme_key not in THEME_NAMES:
        return None
    text = (read or _read)(os.path.join(themes_dir(base), theme_key + ".json"))
    try:
        data = json.loads(text or "")
        for profile in data.get("profiles") or []:
            for fb in profile.get("feedbacks") or []:
                if fb.get("event-name") == EVENT and isinstance(fb.get("file-name"), str):
                    return fb["file-name"]
    except (ValueError, AttributeError):
        pass
    return None


def selected(options, path):
    """The index of the choice that rings now (0: the default)."""
    if path:
        for i, (_k, _l, p) in enumerate(options):
            if p == path:
                return i
    return 0


def write_theme(sound_path, current_key, base=None):
    """Write our theme for `sound_path`; returns the name for the theme key.
    The other one goes."""
    name = next_name(current_key)
    d = themes_dir(base)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name + ".json")
    with open(path + ".tmp", "w") as f:
        json.dump(theme_json(name, sound_path), f, indent=2)
        f.write("\n")
    os.replace(path + ".tmp", path)
    return name


def remove_themes(keep=None, base=None):
    for name in THEME_NAMES:
        if name == keep:
            continue
        try:
            os.unlink(os.path.join(themes_dir(base), name + ".json"))
        except OSError:
            pass


def needs_conversion(path):
    """libcanberra plays Ogg Vorbis and WAV; anything else goes through ffmpeg."""
    head = b""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        return True
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return False
    return not (head[:4] == b"OggS" and b"\x01vorbis" in head)


def convert_argv(src, dst, max_s=OWN_MAX_S):
    """ffmpeg: the first `max_s` s of any audio (the first audio stream, no
    picture), as Ogg Vorbis, 44.1 kHz stereo."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-t",
        str(int(max_s)),
        "-i",
        src,
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-c:a",
        "libvorbis",
        "-q:a",
        "5",
        "-f",
        "ogg",
        dst,
    ]


def own_path(base=None):
    path = os.path.join(own_dir(base), OWN_NAME)
    return path if os.path.exists(path) else None


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None
