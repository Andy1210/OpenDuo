"""A bug report: the logs and state a developer needs, masked (redact.py),
packed into one .tar.gz.

What goes in (each a text file in the archive):
  versions.txt          the OS and build, the kernel and its command line,
                        every installed package's version
  journal-this-boot.txt the system journal since this start
  journal-last-boot.txt ... and of the start before (a crash, a freeze)
  kernel.txt            the kernel's messages since this start
  services.txt          failed units, running services, crashes (coredumps)
  network.txt           interfaces, addresses, routes, the firewall's tables
  system.txt            uptime, memory, storage
  duo-wc-state.json     the compositor's state, window titles left out
plus the device's own items (bugreport.d/*.conf: commands and files), and a
README.txt that says how it was masked. Every text is masked line by line
before it goes in; each file is cut at MAX_FILE.
"""

import io
import json
import os
import re
import shlex
import socket
import subprocess
import tarfile
import time

MAX_FILE = 24 << 20  # bytes of one text before it is masked and packed
JOURNAL_LINES = 150000  # lines of this start; half as many of the last one
RUN_TIMEOUT = 30
PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
WC_ADMIN = "/run/duo-wc/admin.sock"
DEVICE_DIR = "/usr/local/lib/duo-devmode/bugreport.d"

GENERIC = [
    (
        "versions.txt",
        [
            ["cat", "/etc/os-release"],
            ["uname", "-a"],
            ["cat", "/proc/cmdline"],
            ["dpkg-query", "-W", "-f", "${Package} ${Version}\\n"],
        ],
    ),
    (
        "journal-this-boot.txt",
        [
            [
                "journalctl",
                "-b",
                "0",
                "-o",
                "short-monotonic",
                "--no-pager",
                "-n",
                str(JOURNAL_LINES),
            ]
        ],
    ),
    (
        "journal-last-boot.txt",
        [
            [
                "journalctl",
                "-b",
                "-1",
                "-o",
                "short-monotonic",
                "--no-pager",
                "-n",
                str(JOURNAL_LINES),
            ]
        ],
    ),
    ("kernel.txt", [["journalctl", "-k", "-b", "0", "-o", "short-monotonic", "--no-pager"]]),
    (
        "services.txt",
        [
            ["systemctl", "--failed", "--no-pager"],
            ["systemctl", "list-units", "--type=service", "--state=running", "--no-pager"],
            ["coredumpctl", "list", "--no-pager"],
        ],
    ),
    (
        "network.txt",
        [
            ["ip", "-br", "link"],
            ["ip", "-br", "addr"],
            ["ip", "route"],
            ["ip", "-6", "route"],
            ["nft", "list", "ruleset"],
        ],
    ),
    (
        "system.txt",
        [["uptime"], ["free", "-m"], ["df", "-h"], ["cat", "/proc/pressure/memory"]],
    ),
]


# duo2-touchd's lines with a touch's or the pen's position (its contact log,
# Developer mode's "Log touch positions", touch/devgate.h): from them the
# lock screen's PIN could be read back, and the report lands in Downloads,
# where any app can read it. They are left out, whatever the switch says.
TOUCH_POSITION = re.compile(
    r"duo2-touchd.*(\b(raw|local|down)=-?\d|\bx -?\d+ y -?\d+|scripted (tap|pen))"
)


def drop_touch_positions(text):
    """(text without duo2-touchd's position lines, how many went)."""
    kept, dropped = [], 0
    for line in text.splitlines(True):
        if TOUCH_POSITION.search(line):
            dropped += 1
        else:
            kept.append(line)
    return "".join(kept), dropped


def run(argv, timeout=RUN_TIMEOUT):
    """A command's output (stdout and stderr), with a header line."""
    head = "$ %s\n" % " ".join(shlex.quote(a) for a in argv)
    try:
        p = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env={"PATH": PATH, "LANG": "C.UTF-8", "SYSTEMD_COLORS": "0"},
            stdin=subprocess.DEVNULL,
        )
        out = p.stdout[:MAX_FILE].decode("utf-8", "replace")
        tail = "" if p.returncode == 0 else "(exit %d)\n" % p.returncode
    except (OSError, subprocess.SubprocessError) as e:
        out, tail = "", "(not run: %s)\n" % e
    return head + out + ("" if out.endswith("\n") or not out else "\n") + tail + "\n"


def read_file(path):
    head = "# %s\n" % path
    try:
        with open(path, "rb") as f:
            return head + f.read(MAX_FILE).decode("utf-8", "replace") + "\n"
    except OSError as e:
        return head + "(not read: %s)\n" % e.strerror


def parse_device_conf(text):
    """bugreport.d/*.conf -> [(archive name, ("run", argv) | ("file", path))].

        NAME: command arg...     the command's output (no shell)
        NAME < /path             a file
    NAME: letters, digits, . _ - only. Bad lines are skipped."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep, kind in ((":", "run"), ("<", "file")):
            name, found, rest = line.partition(sep)
            name, rest = name.strip(), rest.strip()
            if not found or not name or not rest:
                continue
            if not all(c.isalnum() or c in "._-" for c in name) or name.startswith("."):
                continue
            if kind == "run":
                try:
                    out.append((name, ("run", shlex.split(rest))))
                except ValueError:
                    pass
            elif rest.startswith("/"):
                out.append((name, ("file", rest)))
            break
    return out


def device_items(directory=DEVICE_DIR):
    items = []
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(".conf"))
    except OSError:
        return items
    for n in names:
        try:
            with open(os.path.join(directory, n)) as f:
                items.extend(parse_device_conf(f.read()))
        except OSError:
            pass
    return items


def scrub_state(obj):
    """duo-wc's state without what a window shows about the user (titles)."""
    if isinstance(obj, dict):
        return {k: ("[title]" if k == "title" else scrub_state(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_state(v) for v in obj]
    return obj


def wc_state(path=WC_ADMIN):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(path)
            s.sendall(b'{"id":1,"request":"get_state"}\n')
            buf = b""
            while not buf.endswith(b"\n") and len(buf) < (8 << 20):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        reply = json.loads(buf.decode("utf-8", "replace") or "{}")
        return json.dumps(scrub_state(reply.get("ok", reply)), indent=1, sort_keys=True) + "\n"
    except (OSError, ValueError) as e:
        return "(duo-wc's state not read: %s)\n" % e


README = """OpenDuo bug report, made %(when)s by duo-devmode (Settings > Developer).

Identifiers were masked before anything was written: device numbers (IMEI,
IMSI, ICCID, EID), phone numbers, MAC and Bluetooth addresses, IP addresses
outside OpenDuo's own subnets, SSIDs, e-mail addresses, user, host and
Bluetooth device names, the serial number, keys and tokens, coordinates.
Each value became one placeholder ([mac-1], [ip-2], ...) for the whole
report, so the same value can be followed from line to line. Window titles
and the touch daemon's touch and pen positions are left out. Look through it
before you send it anywhere: masking finds the usual identifiers, not
everything a log line may say.

Masked: %(counts)s
Touch positions left out: %(touches)d lines
"""


def collect(redactor, generic=GENERIC, device=None, state=None, runner=run, reader=read_file):
    """{archive name: masked text}."""
    texts = {}
    for name, cmds in generic:
        texts[name] = "".join(runner(argv) for argv in cmds)
    for name, (kind, what) in device if device is not None else device_items():
        text = runner(what) if kind == "run" else reader(what)
        texts[name] = texts.get(name, "") + text
    texts["duo-wc-state.json"] = state if state is not None else wc_state()
    touches = 0
    masked = {}
    for name, text in texts.items():
        text, n = drop_touch_positions(text[:MAX_FILE])
        touches += n
        masked[name] = redactor.mask_text(text)
    counts = ", ".join("%d %s" % (n, c) for c, n in sorted(redactor.counts.items())) or "nothing"
    masked["README.txt"] = README % {
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "counts": counts,
        "touches": touches,
    }
    return masked


def archive_name(now=None):
    return time.strftime("openduo-bugreport-%Y%m%d-%H%M%S.tar.gz", time.localtime(now))


def pack(texts, top):
    """The .tar.gz's bytes: top/NAME for each text, files 0644, one mtime."""
    buf = io.BytesIO()
    now = int(time.time())
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in sorted(texts):
            data = texts[name].encode("utf-8")
            info = tarfile.TarInfo("%s/%s" % (top, name))
            info.size, info.mode, info.mtime = len(data), 0o644, now
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()
