"""duo-lock: the lock screen client (ext-session-lock-v1, pywayland + cairo).

It asks duo-wc to lock, puts a lock surface on every panel (the clock, the
date, the PIN dots and a keypad on each; unlock on either), and unlocks
when the PIN is right (lockpad.py: PBKDF2, back-off after wrong tries).
Without a PIN it is a swipe lock: the clock, the date and "Swipe up to
unlock"; a swipe up on either panel (or Enter) unlocks.
While locked duo-wc shows and feeds nothing but these surfaces.

No toolkit: GTK4 has no session-lock support in Debian's gtk4-layer-shell
1.0.4, so this draws with cairo into wl_shm buffers, sized for the panel's
physical pixels through wp_viewporter (exact at the fractional scale).
The Wayland bindings for ext-session-lock and viewporter are generated at
install time (lock/gen-protocols.py) into /usr/local/lib/duo-shell/protocols.

An "Emergency call" button (on a swipe lock and a PIN lock alike) opens
an emergency keypad on the lock screen: only emergency numbers are dialled
from it (emergencymodel.py; the list comes from the shell, which checks the
number again and places the call), the call then shows on the call card.

`duo-lock --set-pin` sets a PIN; `duoctl unlock` ends any lock from ssh.
"""

import math
import mmap
import os
import select
import sys
import time

import cairo
from protocols.ext_session_lock_v1 import ExtSessionLockManagerV1
from protocols.viewporter import WpViewporter
from protocols.wayland import WlCompositor, WlOutput, WlSeat, WlShm
from pywayland.client import Display

from . import callsmodel, emergencymodel, lockpad, lockstore, svgicon, timefmt, wallpaper, wc

FONT = "Cantarell"
KEY_CODES = {
    2: "1",
    3: "2",
    4: "3",
    5: "4",
    6: "5",
    7: "6",
    8: "7",
    9: "8",
    10: "9",
    11: "0",
    14: "back",
    28: "ok",
    96: "ok",
}


def log(*args):
    print("duo-lock:", *args, file=sys.stderr, flush=True)


class Panel:
    def __init__(self, output):
        self.output = output
        self.name = None
        self.mode = None  # physical (w, h)
        self.surface = None
        self.lock_surface = None
        self.viewport = None
        self.size = None  # logical (w, h)
        self.keys = []
        self.buffers = []
        self.wall = None  # (cairo surface, w, h, bytes) of the wallpaper, or False for none
        self.wall_key = None
        self.wall_now = None


def verify_pin(pin):
    """The PIN checked by root (duo-lock-helper: the hash is root's, so is
    the back-off): ("ok" | "wrong" | "wait", seconds)."""
    import subprocess

    try:
        done = subprocess.run(
            ["sudo", "-n", lockstore.HELPER, "verify"],
            input=pin + "\n",
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log("cannot check the PIN: %s" % e)
        return "wrong", 0
    if done.returncode == lockstore.OK:
        return "ok", 0
    if done.returncode == lockstore.NO_PIN:
        # The PIN was taken away (by root): no PIN = the swipe lock's rule.
        log("no PIN is set any more: unlocking")
        return "ok", 0
    if done.returncode == lockstore.WAIT:
        try:
            return "wait", int(done.stdout.split()[1])
        except (IndexError, ValueError):
            return "wait", lockstore.FIRST_WAIT_S
    if done.returncode != lockstore.WRONG:
        log("the PIN check failed: %s" % (done.stderr.strip() or done.returncode))
    return "wrong", 0


class LockClient:
    def __init__(self, has_pin):
        # No PIN: a swipe up unlocks.
        self.swipe = not has_pin
        self.entry = lockpad.Entry(None, verify=verify_pin)
        if lockpad.home_encrypted():
            # the recovery key is typed here too when the PIN is forgotten
            self.entry.max_len = lockpad.RECOVERY_LEN
        self.display = Display()
        self.display.connect()
        self.compositor = self.shm = self.seat = self.manager = self.viewporter = None
        self.panels = []
        self.lock = None
        self.locked = False
        self.done = None
        self.pressed = {}  # touch id -> (panel, key)
        self.want = None  # the wallpaper choice, read when the first panel is drawn
        # The decoded wallpaper, kept while locked: a rotation cuts it again
        # for the turned panel without decoding the file again (~0.3 s), so
        # the lock screen at the new size is there within duo-wc's turn.
        self.wall_cache = {}
        # A call (callsmodel.py): the shell sends the card over the
        # socketpair it started us with, the buttons go back the same way.
        self.call = None
        self.speaker = False
        self.card_hidden = False  # a PIN lock's call that is up, keypad out
        # The emergency keypad (an emergencymodel.Entry while it is out) and
        # what the shell offers for it (the numbers it may dial).
        self.emergency = None
        self.emergency_calling = None  # the number asked for, until its call card comes
        self.offer = dict(emergencymodel.DEFAULT_OFFER)
        self.call_sock = call_socket()
        self.call_buf = b""
        registry = self.display.get_registry()
        registry.dispatcher["global"] = self._global
        self.display.roundtrip()
        self.display.roundtrip()  # the outputs' modes and names

    def _global(self, registry, name, interface, version):
        if interface == "wl_compositor":
            self.compositor = registry.bind(name, WlCompositor, min(version, 4))
        elif interface == "wl_shm":
            self.shm = registry.bind(name, WlShm, 1)
        elif interface == "wl_seat":
            self.seat = registry.bind(name, WlSeat, min(version, 5))
            self.seat.dispatcher["capabilities"] = self._capabilities
        elif interface == "wl_output":
            output = registry.bind(name, WlOutput, min(version, 4))
            panel = Panel(output)
            output.dispatcher["mode"] = lambda _o, _f, w, h, _r, p=panel: setattr(p, "mode", (w, h))
            output.dispatcher["name"] = lambda _o, n, p=panel: setattr(p, "name", n)
            self.panels.append(panel)
        elif interface == "ext_session_lock_manager_v1":
            self.manager = registry.bind(name, ExtSessionLockManagerV1, 1)
        elif interface == "wp_viewporter":
            self.viewporter = registry.bind(name, WpViewporter, 1)

    def _capabilities(self, seat, caps):
        if caps & WlSeat.capability.touch and not hasattr(self, "touch"):
            self.touch = seat.get_touch()
            self.touch.dispatcher["down"] = self._down
            self.touch.dispatcher["motion"] = self._motion
            self.touch.dispatcher["up"] = self._up
            self.touch.dispatcher["cancel"] = lambda *_a: self.pressed.clear()
        if caps & WlSeat.capability.keyboard and not hasattr(self, "keyboard"):
            self.keyboard = seat.get_keyboard()
            self.keyboard.dispatcher["key"] = self._key

    # -- locking --------------------------------------------------------------------

    def run(self):
        if self.manager is None:
            log(
                "the compositor offers no ext-session-lock (duo-wc: session_lock = off, or this is "
                "not a trusted client: the shell starts the lock, docs/hardware/security.md)"
            )
            return 2
        if self.compositor is None or self.shm is None:
            log("no wl_compositor / wl_shm")
            return 2
        self.lock = self.manager.lock()
        self.lock.dispatcher["locked"] = self._locked
        self.lock.dispatcher["finished"] = self._finished
        for panel in self.panels:
            panel.surface = self.compositor.create_surface()
            if self.viewporter is not None:
                panel.viewport = self.viewporter.get_viewport(panel.surface)
            panel.lock_surface = self.lock.get_lock_surface(panel.surface, panel.output)
            panel.lock_surface.dispatcher["configure"] = lambda ls, serial, w, h, p=panel: (
                self._configure(p, ls, serial, w, h)
            )
        self.display.flush()
        fd = self.display.get_fd()
        while self.done is None:
            self.display.dispatch(block=False)
            self.display.flush()
            ticking = self.entry.waiting(time.time()) or (self.call or {}).get("kind") == "active"
            timeout = 1.0 if ticking else 60 - time.localtime().tm_sec + 0.05
            fds = [fd] + ([self.call_sock] if self.call_sock is not None else [])
            readable, _, _ = select.select(fds, [], [], timeout)
            if self.call_sock is not None and self.call_sock in readable:
                self.read_call()
            if fd in readable:
                if self.display.dispatch(block=True) < 0:
                    log("lost the compositor")
                    return 1
            elif not readable:
                self.redraw()
        return self.done

    # -- the call card ---------------------------------------------------------------

    def read_call(self):
        try:
            data = self.call_sock.recv(4096)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""
        if not data:
            # The shell went: nothing more comes (a card would go stale).
            self.call_sock.close()
            self.call_sock = None
            self.set_call(None, False)
            return
        self.call_buf += data
        changed = False
        while b"\n" in self.call_buf:
            line, self.call_buf = self.call_buf.split(b"\n", 1)
            text = line.decode("utf-8", "replace")
            offer = emergencymodel.parse_offer(text)
            if offer is not None and offer != self.offer:
                self.offer = offer
                if self.emergency is not None:
                    self.emergency.allowed = set(offer["numbers"])
                changed = True
            parsed = callsmodel.parse_card(text)
            if parsed is not None:
                self.set_call(*parsed, redraw=False)
                changed = True
        if len(self.call_buf) > 8192:
            self.call_buf = b""
        if changed:
            self.redraw()

    def set_call(self, card, speaker, redraw=True):
        old = self.call
        if (
            card is None
            or old is None
            or card["path"] != old["path"]
            or card["kind"] != old["kind"]
        ):
            self.card_hidden = False
        if card is not None and (old is None or old.get("path") != card["path"]):
            log("call card: %s" % card["kind"])
            # The call card takes over from the emergency keypad (a PIN
            # lock's "Enter PIN" then shows the PIN keypad, not this one).
            self.emergency = None
            self.emergency_calling = None
        elif card is None and old is not None:
            log("call card: gone")
        self.call = card
        self.speaker = bool(speaker)
        if redraw:
            self.redraw()

    def card_shown(self):
        return self.call is not None and not self.card_hidden

    def call_do(self, action, on=False):
        if self.call is None or self.call_sock is None:
            return
        try:
            self.call_sock.send(
                callsmodel.encode({"do": action, "path": self.call["path"], "on": bool(on)})
            )
        except OSError as e:
            log("cannot reach the shell: %s" % e)

    def card_press(self, button):
        if button == "accept":
            self.call_do("accept")
            if self.swipe:
                # No PIN: the lock keeps nothing from anyone, and Calls'
                # own window is the better call screen.
                log("answered: unlocking (no PIN)")
                self.unlock()
                return
        elif button in ("decline", "hangup"):
            self.call_do("hangup")
        elif button == "speaker":
            self.call_do("speaker", on=not self.speaker)
        elif button == "pin":
            self.card_hidden = True
        self.redraw()

    # -- the emergency keypad -------------------------------------------------------

    def emergency_offered(self):
        """Whether the "Emergency call" button shows: the shell can dial (the
        socketpair is there), no call is up or ringing, the keypad is not out."""
        return self.call_sock is not None and self.call is None and self.emergency is None

    def open_emergency(self):
        log("emergency keypad")
        self.emergency = emergencymodel.Entry(self.offer["numbers"])
        self.emergency_calling = None
        self.redraw()

    def close_emergency(self):
        self.emergency = None
        self.emergency_calling = None
        self.redraw()

    def emergency_press(self, key):
        result = self.emergency.press(key)
        if result is not None:
            number = result[1]
            log("emergency call to %s" % number)
            try:
                self.call_sock.send(emergencymodel.encode_request(number))
                self.emergency_calling = number
            except (OSError, AttributeError) as e:
                log("cannot reach the shell: %s" % e)
                self.emergency.message = "no-shell"
        self.redraw()

    def emergency_target(self, panel, x, y):
        """What a finger at (x, y) is on, in the emergency keypad."""
        if panel.size is None:
            return None
        w, h = panel.size
        lay = emergencymodel.layout(w, h, len(self.offer["suggestions"]))
        if emergencymodel.in_box(lay["cancel"], x, y):
            return ("em", "cancel")
        for i, cx, cy, pw, ph in lay["pills"]:
            if emergencymodel.in_box((cx, cy, pw, ph), x, y, pad=2):
                return ("em", "pill", i)
        key = emergencymodel.key_at(lay["keys"], x, y)
        return ("em", "key", key) if key is not None else None

    def emergency_act(self, target):
        if target[1] == "cancel":
            self.close_emergency()
        elif target[1] == "pill":
            sugg = self.offer["suggestions"]
            if 0 <= target[2] < len(sugg):
                self.emergency.choose(sugg[target[2]][0])
            self.redraw()
        elif target[1] == "key":
            self.emergency_press(target[2])
        elif target[1] == "open":
            self.open_emergency()

    def _locked(self, _lock):
        self.locked = True
        log("locked")

    def _finished(self, lock):
        if not self.locked:
            log("the compositor refused the lock")
            self.done = 2
            return
        # Locked, and the compositor ended it (duoctl unlock): the protocol
        # wants unlock_and_destroy now; then this lock is over and the
        # shell may lock again (it starts none while this process runs).
        log("the compositor ended the lock")
        try:
            lock.unlock_and_destroy()
            self.display.flush()
        except Exception as e:  # the connection may be going already
            log("unlock_and_destroy: %s" % e)
        self.done = 0

    def _configure(self, panel, lock_surface, serial, width, height):
        lock_surface.ack_configure(serial)
        panel.size = (width, height)
        panel.keys = [] if self.swipe else lockpad.keypad(width, height, top=height * 0.36)
        self.draw(panel)

    def unlock(self):
        log("unlocked")
        self.lock.unlock_and_destroy()
        self.display.roundtrip()
        self.done = 0

    # -- input -----------------------------------------------------------------------

    def _panel_of(self, surface):
        for panel in self.panels:
            if panel.surface is not None and panel.surface == surface:
                return panel
        return None

    def _down(self, _touch, _serial, _time, surface, tid, x, y):
        panel = self._panel_of(surface)
        if panel is None:
            return
        if self.card_shown():
            key = callsmodel.button_at(getattr(panel, "call_buttons", []), x, y)
            key = ("call", key) if key is not None else None
        elif self.emergency is not None:
            key = self.emergency_target(panel, x, y)
        elif self.call is not None and self.in_call_line(panel, x, y):
            key = ("call", "card")
        else:
            key = None if self.swipe else lockpad.key_at(panel.keys, x, y)
            if (
                key is None
                and self.emergency_offered()
                and panel.size
                and emergencymodel.in_box(
                    emergencymodel.button(*panel.size, pin_lock=not self.swipe), x, y
                )
            ):
                key = ("em", "open")
        self.pressed[tid] = (panel, key, x, y)
        self.started = getattr(self, "started", {})
        self.started[tid] = (x, y)
        if key is not None:
            self.draw(panel, key)

    def _motion(self, _touch, _time, tid, x, y):
        if tid in self.pressed:
            panel, key, _x, _y = self.pressed[tid]
            self.pressed[tid] = (panel, key, x, y)

    def _up(self, _touch, _serial, _time, tid):
        panel, key, x, y = self.pressed.pop(tid, (None, None, 0, 0))
        x0, y0 = getattr(self, "started", {}).pop(tid, (x, y))
        if panel is None:
            return
        if isinstance(key, tuple) and key[0] == "em":
            # The emergency keypad and its button: where the finger went
            # down and lifted (a swipe up still unlocks a swipe lock).
            if self.swipe and panel.size and lockpad.swipe_unlocks(x - x0, y - y0, panel.size[1]):
                self.unlock()
            elif key[1] == "open":
                if self.emergency_offered() and emergencymodel.in_box(
                    emergencymodel.button(*panel.size, pin_lock=not self.swipe), x, y
                ):
                    self.open_emergency()
                else:
                    self.redraw()
            elif self.emergency is not None and self.emergency_target(panel, x, y) == key:
                self.emergency_act(key)
            else:
                self.redraw()
            return
        if isinstance(key, tuple):
            # A call button: pressed where it went down (a swipe from it
            # still unlocks a swipe lock).
            if self.swipe and panel.size and lockpad.swipe_unlocks(x - x0, y - y0, panel.size[1]):
                self.unlock()
            elif key[1] == "card":
                if self.in_call_line(panel, x, y):
                    self.card_hidden = False
                self.redraw()
            elif callsmodel.button_at(getattr(panel, "call_buttons", []), x, y) == key[1]:
                self.card_press(key[1])
            else:
                self.redraw()
            return
        if self.swipe:
            if panel.size and lockpad.swipe_unlocks(x - x0, y - y0, panel.size[1]):
                self.unlock()
            return
        if key is not None and lockpad.key_at(panel.keys, x, y) == key:
            self.press(key)
        else:
            self.redraw()

    def in_call_line(self, panel, x, y):
        if panel.size is None or self.swipe:
            return False
        bx, by, bw, bh = callsmodel.call_line_box(*panel.size)
        return bx <= x <= bx + bw and by - 8 <= y <= by + bh + 8

    def _key(self, _kbd, _serial, _time, code, state):
        if self.card_shown():
            return
        if self.emergency is not None:
            if state == 1 and code == 1:  # Esc
                self.close_emergency()
            elif state == 1 and code in KEY_CODES:
                key = KEY_CODES[code]
                self.emergency_press("call" if key == "ok" else key)
            return
        if state == 1 and self.swipe and KEY_CODES.get(code) == "ok":
            self.unlock()
            return
        if state == 1 and code in KEY_CODES:
            self.press(KEY_CODES[code])

    def press(self, key):
        if self.entry.press(key, time.time()) == "unlock":
            self.unlock()
            return
        self.redraw()

    # -- drawing ---------------------------------------------------------------------

    def redraw(self):
        for panel in self.panels:
            self.draw(panel)

    def draw(self, panel, held=None):
        if panel.size is None:
            return
        w, h = panel.size
        if panel.mode and panel.viewport is not None:
            scale = max(panel.mode) / max(w, h)
        else:
            scale = 3.0
        pw, ph = int(round(w * scale)), int(round(h * scale))
        stride = pw * 4
        size = stride * ph
        fd = os.memfd_create("duo-lock", os.MFD_CLOEXEC)
        os.ftruncate(fd, size)
        data = mmap.mmap(fd, size)
        pool = self.shm.create_pool(fd, size)
        buffer = pool.create_buffer(0, pw, ph, stride, WlShm.format.argb8888.value)
        pool.destroy()
        os.close(fd)
        buffer.dispatcher["release"] = lambda b, d=data: release(b, d)

        image = cairo.ImageSurface.create_for_data(data, cairo.FORMAT_ARGB32, pw, ph, stride)
        ctx = cairo.Context(image)
        ctx.scale(pw / w, ph / h)
        panel.wall_now = self.wall_for(panel, w, h, pw, ph)
        self.paint(ctx, w, h, panel, held)
        image.flush()
        del ctx, image

        if panel.viewport is not None:
            panel.viewport.set_destination(w, h)
        else:
            panel.surface.set_buffer_scale(int(scale))
        panel.surface.attach(buffer, 0, 0)
        panel.surface.damage_buffer(0, 0, pw, ph)
        panel.surface.commit()
        self.display.flush()

    def wall_for(self, panel, w, h, pw, ph):
        """The panel's part of the wallpaper as a cairo surface (rendered once
        per choice and size), or None for the default look."""
        if self.want is None:
            self.want = wallpaper.resolve(wallpaper.read_conf())
            self.layout = None
        if not self.want["path"]:
            return None
        key = (pw, ph)
        if panel.wall_key == key:
            return panel.wall or None
        panel.wall_key = key
        panel.wall = False
        # The panels' places as they are now: a new size is a rotation
        # (duo-wc configures the lock surfaces again), and the old layout
        # would cut the picture for the upright panel and stretch it.
        if self.layout is None or not wallpaper.panel_fits(self.layout, panel.name, w, h):
            self.layout = {}
            try:
                self.layout = wallpaper.panels_from_outputs(
                    wc.request("get_outputs").get("outputs")
                )
            except (OSError, RuntimeError, ValueError, AttributeError):
                pass
        panels = dict(self.layout)
        name = panel.name
        if name not in panels:
            panels = {name or "": (0.0, 0.0, float(w), float(h))}
            name = name or ""
        pb = wallpaper.render_panel(
            self.want["path"], name, panels, self.want["mode"], pw / float(w), cache=self.wall_cache
        )
        if pb is not None:
            sw, sh, data = wallpaper.pixbuf_to_bgrx(pb)
            buf = bytearray(data)
            panel.wall = (
                cairo.ImageSurface.create_for_data(buf, cairo.FORMAT_RGB24, sw, sh, sw * 4),
                sw,
                sh,
                buf,
            )
        return panel.wall or None

    def paint(self, ctx, w, h, panel, held):
        # The shell's look: black (AMOLED), a soft glow of the accent at the
        # top, a large light clock, the date dimmed (style.css's home screen);
        # or the wallpaper, dimmed a little more than the home screen's.
        ctx.set_source_rgb(0, 0, 0)
        ctx.paint()
        wall = getattr(panel, "wall_now", None)
        if wall is not None:
            surface, sw, sh, _buf = wall
            ctx.save()
            ctx.scale(w / sw, h / sh)
            ctx.set_source_surface(surface, 0, 0)
            ctx.paint()
            ctx.restore()
            ctx.set_source_rgba(0, 0, 0, min(0.8, self.want["dim"] / 100.0 + 0.08))
            ctx.paint()
        else:
            glow = cairo.RadialGradient(w / 2, -h * 0.1, 0, w / 2, -h * 0.1, h * 0.75)
            glow.add_color_stop_rgba(0.0, 0.208, 0.518, 0.894, 0.30)
            glow.add_color_stop_rgba(0.45, 0.208, 0.518, 0.894, 0.07)
            glow.add_color_stop_rgba(1.0, 0.208, 0.518, 0.894, 0.0)
            ctx.set_source(glow)
            ctx.paint()
        if self.emergency is not None and not self.card_shown():
            # The emergency keypad has the whole panel (no clock).
            panel.call_buttons = []
            self.paint_emergency(ctx, w, h, held)
            return
        now = time.localtime()
        ctx.set_source_rgb(0.95, 0.953, 0.96)
        ctx.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        centred(ctx, time.strftime(timefmt.hm(), now), w / 2, h * 0.17, 84)
        ctx.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        ctx.set_source_rgba(0.95, 0.953, 0.96, 0.62)
        centred(ctx, time.strftime(timefmt.date_long("%d"), now), w / 2, h * 0.225, 17)

        if self.card_shown():
            self.paint_card(ctx, w, h, panel, held)
        else:
            panel.call_buttons = []
            if self.emergency_offered():
                cx, cy, bw, bh = emergencymodel.button(w, h, pin_lock=not self.swipe)
                self.paint_pill(
                    ctx,
                    "Emergency call",
                    cx,
                    cy,
                    bh / 1.3,
                    held == ("em", "open"),
                    width=bw,
                    rgb=(0.88, 0.2, 0.2),
                )

        if self.swipe:
            # The hint and a handle bar at the bottom (swipe it up).
            if self.card_shown() and w > h:
                return  # on its side the card's buttons take that room
            ctx.set_source_rgba(0.95, 0.953, 0.96, 0.7)
            ctx.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            centred(ctx, "Swipe up to unlock", w / 2, h * 0.88, 16)
            bw, bh = 120, 5
            x, y = w / 2 - bw / 2, h * 0.92
            ctx.new_sub_path()
            ctx.arc(x + bh / 2, y + bh / 2, bh / 2, math.pi / 2, 3 * math.pi / 2)
            ctx.arc(x + bw - bh / 2, y + bh / 2, bh / 2, -math.pi / 2, math.pi / 2)
            ctx.close_path()
            ctx.set_source_rgba(0.95, 0.953, 0.96, 0.85)
            ctx.fill()
            return
        if self.card_shown():
            return
        if self.call is not None:
            self.paint_call_line(ctx, w, h)

        # The typed digits as dots, or the message.
        y = h * 0.27
        n = len(self.entry.digits)
        if self.entry.waiting(time.time()):
            left = int(math.ceil(self.entry.wait_until - time.time()))
            ctx.set_source_rgb(1, 0.45, 0.45)
            centred(ctx, "Too many tries: wait %d s" % left, w / 2, y, 17)
        elif n > lockpad.MAX_LEN:
            # longer than a PIN: the recovery key (homecrypt/)
            ctx.set_source_rgb(1, 1, 1)
            centred(ctx, "Recovery key: %d of %d digits" % (n, lockpad.RECOVERY_LEN), w / 2, y, 17)
        elif n:
            gap = 22
            x0 = w / 2 - gap * (n - 1) / 2
            ctx.set_source_rgb(1, 1, 1)
            for i in range(n):
                ctx.arc(x0 + i * gap, y - 6, 6, 0, 2 * math.pi)
                ctx.fill()
        elif self.entry.message == "wrong":
            ctx.set_source_rgb(1, 0.45, 0.45)
            centred(ctx, "Wrong PIN", w / 2, y, 17)
        elif os.path.exists(lockpad.HOME_LOCKED):
            ctx.set_source_rgba(1, 1, 1, 0.6)
            centred(ctx, "Enter PIN to unlock your data", w / 2, y, 17)
        else:
            ctx.set_source_rgba(1, 1, 1, 0.6)
            centred(ctx, "Enter PIN", w / 2, y, 17)

        for key, cx, cy, r in panel.keys:
            ctx.arc(cx, cy, r, 0, 2 * math.pi)
            if key == "ok":
                ctx.set_source_rgba(0.21, 0.52, 0.89, 1.0 if key != held else 0.7)
            else:
                ctx.set_source_rgba(1, 1, 1, 0.30 if key == held else 0.12)
            ctx.fill()
            ctx.set_source_rgb(1, 1, 1)
            if key == "back" and theme_icon(ctx, "edit-clear-symbolic", cx, cy, r * 0.8):
                continue
            label = {"back": "\u232b", "ok": "OK"}.get(key, key)
            ctx.select_font_face(
                FONT,
                cairo.FONT_SLANT_NORMAL,
                cairo.FONT_WEIGHT_BOLD if key == "ok" else cairo.FONT_WEIGHT_NORMAL,
            )
            size = r * (0.55 if key != "ok" else 0.45)
            # Digits share one baseline (that of "0"), so they sit alike in their circles.
            middle(ctx, label, cx, cy, size, ref="0" if key.isdigit() else None)

    def call_status(self):
        call = self.call or {}
        if call.get("kind") == "incoming":
            return "Incoming call"
        if call.get("state") == "held":
            return "On hold"
        if call.get("state") == "dialing" or call.get("since") is None:
            return "Calling..."
        return callsmodel.duration(time.time() - call["since"])

    def paint_card(self, ctx, w, h, panel, held):
        """The call: who, its state, and the buttons (Decline / Answer, or
        Speaker / Hang up and, on a PIN lock, Enter PIN)."""
        call = self.call
        lay = callsmodel.card_layout(call["kind"], w, h, pin_lock=not self.swipe)
        panel.call_buttons = lay["buttons"]
        ctx.set_source_rgb(0.95, 0.953, 0.96)
        ctx.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        fit(ctx, call["caller"], w / 2, lay["caller_y"], 30, w * 0.86)
        ctx.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_source_rgba(0.95, 0.953, 0.96, 0.7)
        centred(ctx, self.call_status(), w / 2, lay["status_y"], 18)
        pressed = held[1] if isinstance(held, tuple) else None
        labels = {
            "accept": "Answer",
            "decline": "Decline",
            "speaker": "Speaker",
            "hangup": "Hang up",
        }
        for name, cx, cy, r in lay["buttons"]:
            if name == "pin":
                self.paint_pill(ctx, "Enter PIN", cx, cy, r, pressed == name)
                continue
            if name == "accept":
                rgb = (0.18, 0.66, 0.34)
            elif name in ("decline", "hangup"):
                rgb = (0.88, 0.2, 0.2)
            elif self.speaker:
                rgb = (0.21, 0.52, 0.89)
            else:
                rgb = None
            ctx.arc(cx, cy, r, 0, 2 * math.pi)
            if rgb is None:
                ctx.set_source_rgba(1, 1, 1, 0.30 if pressed == name else 0.16)
            else:
                ctx.set_source_rgba(rgb[0], rgb[1], rgb[2], 0.7 if pressed == name else 1.0)
            ctx.fill()
            ctx.set_source_rgb(1, 1, 1)
            icon = {"speaker": "audio-speakers-symbolic", "accept": "call-start-symbolic"}.get(
                name, "call-stop-symbolic"
            )
            if theme_icon(ctx, icon, cx, cy, r * 0.9):
                pass
            elif name == "speaker":
                draw_speaker(ctx, cx, cy, r * 0.5)
            else:
                draw_handset(ctx, cx, cy, r * 0.56, down=name != "accept")
            ctx.set_source_rgba(0.95, 0.953, 0.96, 0.85)
            ctx.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            centred(ctx, labels[name], cx, cy + r + 24, 15)

    def paint_pill(self, ctx, text, cx, cy, r, pressed, width=None, rgb=None):
        ctx.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(16)
        ext = ctx.text_extents(text)
        pw, ph = (width or ext.width + 40), r * 1.3
        rounded(ctx, cx - pw / 2, cy - ph / 2, pw, ph, ph / 2)
        if rgb is None:
            ctx.set_source_rgba(1, 1, 1, 0.28 if pressed else 0.14)
        else:
            ctx.set_source_rgba(rgb[0], rgb[1], rgb[2], 0.55 if pressed else 0.85)
        ctx.fill()
        ctx.set_source_rgb(1, 1, 1)
        fit(ctx, text, cx, cy + 6, 16, pw - 16)

    def paint_emergency(self, ctx, w, h, held):
        """The emergency keypad: the title, what is typed (or why it cannot
        be dialled), the one-tap numbers, the keys with a green call key,
        Cancel."""
        e = self.emergency
        lay = emergencymodel.layout(w, h, len(self.offer["suggestions"]))
        ctx.set_source_rgb(1, 0.55, 0.55)
        ctx.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        title = (
            "Calling %s..." % self.emergency_calling if self.emergency_calling else "Emergency call"
        )
        centred(ctx, title, w / 2, lay["title_y"], 22)
        ctx.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        if e.message == "not-emergency":
            ctx.set_source_rgb(1, 0.45, 0.45)
            fit(
                ctx,
                "Only emergency numbers from the lock screen",
                w / 2,
                lay["number_y"],
                17,
                w * 0.9,
            )
        elif e.message == "no-shell":
            ctx.set_source_rgb(1, 0.45, 0.45)
            fit(ctx, "Cannot call now: unlock and use Calls", w / 2, lay["number_y"], 17, w * 0.9)
        elif e.digits:
            ctx.set_source_rgb(1, 1, 1)
            centred(ctx, e.digits, w / 2, lay["number_y"], 34)
        else:
            ctx.set_source_rgba(1, 1, 1, 0.55)
            centred(ctx, "Type or choose a number", w / 2, lay["number_y"], 17)
        for i, cx, cy, pw, ph in lay["pills"]:
            number, what = self.offer["suggestions"][i]
            self.paint_pill(
                ctx,
                "%s %s" % (number, what) if lay["labelled"] else number,
                cx,
                cy,
                ph / 1.3,
                held == ("em", "pill", i),
                width=pw,
                rgb=(0.88, 0.2, 0.2) if number == "112" else None,
            )
        cx, cy, bw, bh = lay["cancel"]
        self.paint_pill(ctx, "Cancel", cx, cy, bh / 1.3, held == ("em", "cancel"), width=bw)
        for key, kx, ky, r in lay["keys"]:
            ctx.arc(kx, ky, r, 0, 2 * math.pi)
            pressed = held == ("em", "key", key)
            if key == "call":
                ctx.set_source_rgba(0.18, 0.66, 0.34, 0.7 if pressed else 1.0)
            else:
                ctx.set_source_rgba(1, 1, 1, 0.30 if pressed else 0.12)
            ctx.fill()
            ctx.set_source_rgb(1, 1, 1)
            if key == "call":
                if not theme_icon(ctx, "call-start-symbolic", kx, ky, r * 0.9):
                    draw_handset(ctx, kx, ky, r * 0.56)
                continue
            if key == "back" and theme_icon(ctx, "edit-clear-symbolic", kx, ky, r * 0.8):
                continue
            ctx.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            centred(ctx, "\u232b" if key == "back" else key, kx, ky + r * 0.28, r * 0.55)

    def paint_call_line(self, ctx, w, h):
        """A PIN lock's call that is up, above the keypad: a tap on it brings
        the card back."""
        x, y, bw, bh = callsmodel.call_line_box(w, h)
        rounded(ctx, x, y, bw, bh, bh / 2)
        ctx.set_source_rgba(0.18, 0.66, 0.34, 0.9)
        ctx.fill()
        ctx.set_source_rgb(1, 1, 1)
        ctx.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        text = "%s  %s" % (self.call["caller"], self.call_status())
        fit(ctx, text, w / 2, y + bh / 2 + 5, 15, bw - 24)


def call_socket(environ=None):
    """The call card's socket from the shell (DUO_LOCK_CALL_FD), or None."""
    import socket

    environ = os.environ if environ is None else environ
    value = (environ.pop("DUO_LOCK_CALL_FD", "") or "").strip()
    if not value.isdigit() or int(value) < 3:
        return None
    try:
        sock = socket.socket(fileno=int(value))
        os.set_inheritable(sock.fileno(), False)
        sock.setblocking(False)
        return sock
    except OSError as e:
        log("no call card: %s" % e)
        return None


def fit(ctx, text, x, y, size, width):
    """`centred`, smaller until it fits `width` (then cut short)."""
    while size > 12:
        ctx.set_font_size(size)
        if ctx.text_extents(text).x_advance <= width:
            break
        size -= 1
    ctx.set_font_size(size)
    while len(text) > 4 and ctx.text_extents(text).x_advance > width:
        text = text[:-2].rstrip() + "\u2026"
    centred(ctx, text, x, y, size)


def rounded(ctx, x, y, w, h, r):
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    ctx.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    ctx.close_path()


def theme_icon(ctx, name, cx, cy, size):
    """The icon theme's symbolic icon `name` (Adwaita: call-start, call-stop,
    edit-clear for backspace, audio-speakers), `size` px, centred, filled in
    the current source; False when the theme does not have it drawable (the
    caller then draws its own). Real icons, not font glyphs: the lock's font
    may not have a glyph, and a drawn handset looked home-made."""
    icon = svgicon.load(name)
    if icon is None:
        return False
    svgicon.draw(ctx, icon, cx, cy, size)
    return True


def draw_handset(ctx, cx, cy, s, down=False):
    """A telephone handset (drawn: no icon theme in the lock), lifted for
    Answer, turned down for Decline / Hang up."""
    ctx.save()
    ctx.translate(cx, cy)
    ctx.rotate(math.radians(135 if down else 0))
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    # the grip: an arc from the earpiece to the mouthpiece
    ctx.set_line_width(s * 0.4)
    ctx.arc(s * 0.35, -s * 0.35, s * 0.95, math.radians(100), math.radians(170))
    ctx.stroke()
    # the two ends
    ctx.set_line_width(s * 0.5)
    for ang in (95, 175):
        a = math.radians(ang)
        ex, ey = s * 0.35 + s * 0.95 * math.cos(a), -s * 0.35 + s * 0.95 * math.sin(a)
        tx, ty = -math.sin(a), math.cos(a)  # along the arc
        nx, ny = math.cos(a), math.sin(a)  # out of the arc
        ctx.move_to(ex - tx * s * 0.08, ey - ty * s * 0.08)
        ctx.line_to(ex - nx * s * 0.18, ey - ny * s * 0.18)
        ctx.stroke()
    ctx.restore()


def draw_speaker(ctx, cx, cy, s):
    """A loudspeaker with two sound arcs."""
    ctx.save()
    ctx.translate(cx - s * 0.25, cy)
    ctx.move_to(-s * 0.75, -s * 0.3)
    ctx.line_to(-s * 0.35, -s * 0.3)
    ctx.line_to(s * 0.1, -s * 0.72)
    ctx.line_to(s * 0.1, s * 0.72)
    ctx.line_to(-s * 0.35, s * 0.3)
    ctx.line_to(-s * 0.75, s * 0.3)
    ctx.close_path()
    ctx.fill()
    ctx.set_line_width(s * 0.14)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    for rad in (0.45, 0.8):
        ctx.arc(s * 0.2, 0, s * rad, -math.radians(50), math.radians(50))
        ctx.stroke()
    ctx.restore()


def release(buffer, data):
    """The compositor is done with a buffer (a handler must return None:
    pywayland hands its result back to C)."""
    buffer.destroy()
    data.close()


def centred(ctx, text, x, y, size):
    ctx.set_font_size(size)
    ext = ctx.text_extents(text)
    ctx.move_to(x - ext.width / 2 - ext.x_bearing, y)
    ctx.show_text(text)


def middle(ctx, text, x, cy, size, ref=None):
    """`text` centred on (x, cy) both ways: its ink's middle (or that of
    `ref`) on cy, not its baseline."""
    ctx.set_font_size(size)
    ext = ctx.text_extents(ref or text)
    centred(ctx, text, x, cy - (ext.y_bearing + ext.height / 2), size)


def set_pin():
    """Set the PIN through duo-lock-helper (root keeps it; the current one
    first, when one is set)."""
    import getpass
    import subprocess

    current = ""
    if lockstore.has_pin(lockstore.read_policy()):
        current = getpass.getpass("Current PIN: ")
    pin = getpass.getpass("New PIN (%d-%d digits): " % (lockpad.MIN_LEN, lockpad.MAX_LEN))
    if not lockpad.valid_pin(pin):
        print("A PIN is %d to %d digits." % (lockpad.MIN_LEN, lockpad.MAX_LEN))
        return 1
    if getpass.getpass("Again: ") != pin:
        print("They differ; nothing changed.")
        return 1
    done = subprocess.run(
        ["sudo", "-n", lockstore.HELPER, "set"],
        input="%s\n%s\n" % (current, pin),
        capture_output=True,
        text=True,
    )
    if done.returncode == lockstore.OK:
        print("PIN saved (root keeps it, %s)." % lockstore.STORE)
        return 0
    if done.returncode == lockstore.WAIT:
        print("Too many wrong PINs: try again in %s s." % (done.stdout.split() + ["?", "?"])[1])
    elif done.returncode == lockstore.WRONG:
        print("The current PIN is wrong; nothing changed.")
    else:
        print("Not saved: %s" % (done.stdout.strip() or done.stderr.strip() or done.returncode))
    return 1


def main(argv):
    if argv[:1] == ["--set-pin"]:
        return set_pin()
    has_pin = lockstore.has_pin(lockstore.read_policy())
    if not has_pin:
        log("no PIN set: a swipe up unlocks")
    code = LockClient(has_pin).run()
    # pywayland's finalizers can crash on the way out (proxies outliving the
    # connection): leave without them.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
