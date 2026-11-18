"""Reading a QR code, for the eSIM page's activation code: live from a
camera (GStreamer: the camera through PipeWire, the zbar element reading
the frames, gtk4paintablesink showing them) or from a picture (a
screenshot, a photo, an e-mail's attachment), so nobody has to type the
code.

Needs GStreamer's Python bindings (gir1.2-gstreamer-1.0), the zbar element
(gstreamer1.0-plugins-bad), the PipeWire source (gstreamer1.0-pipewire)
and, for the live view, gstreamer1.0-gtk4; what is missing turns the
matching way off (`can_scan()`, `can_read_pictures()`). The pure parts
(the cameras' order and names, a picture's grey rows) are in qr.py."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, GdkPixbuf, GLib, Gtk  # noqa: E402

try:
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # noqa: E402

    Gst.init(None)
except (ValueError, ImportError):
    Gst = None

from .main import _  # noqa: E402
from .qr import camera_name, camera_rank, gray_rows  # noqa: E402

# A picture is read at most this big (its longer side, pixels): enough for
# a QR code that fills a fifth of a phone photo, quick on the phone.
PICTURE_MAX = 1600
# The camera's frames: 1280 x 720 if the camera has it (a code at arm's
# length still has a few pixels a module), else the biggest up to 1280; the
# cameras list 640 x 480 first, which is what they would otherwise give.
CAMERA_CAPS = (
    'capsfilter caps="video/x-raw,width=1280,height=720; video/x-raw,width=1280,height=960; '
    'video/x-raw,width=(int)[320,1280],height=(int)[240,1280]"'
)


# -- GStreamer -------------------------------------------------------------------------


def _have(*names):
    return Gst is not None and all(Gst.ElementFactory.find(n) for n in names)


def can_read_pictures():
    return _have("appsrc", "videoconvert", "zbar", "fakesink")


def can_scan():
    return _have("pipewiresrc", "videoconvert", "zbar", "gtk4paintablesink", "tee", "queue")


def _prop(props, key):
    if props is None or not props.has_field(key):
        return None
    value = props.get_value(key)
    return value if value is None or isinstance(value, (int, str)) else str(value)


def cameras():
    """The cameras PipeWire offers: [{"rank", "name", "device"}], in the
    order to try them."""
    if Gst is None:
        return []
    # PipeWire's provider only: a DeviceMonitor would also start libcamera's
    # (a camera manager in this process) and v4l2's (the camera
    # subsystem's raw nodes) just to list what PipeWire already has.
    provider = Gst.DeviceProviderFactory.get_by_name("pipewiredeviceprovider")
    if provider is None or not provider.start():
        return []
    found = []
    try:
        for dev in provider.get_devices():
            if not dev.has_classes("Video/Source"):
                continue
            element = dev.create_element(None)
            if element is None or element.get_factory().get_name() != "pipewiresrc":
                continue
            props = dev.get_properties()
            path = _prop(props, "api.libcamera.path") or ""
            location = _prop(props, "api.libcamera.location") or ""
            found.append(
                {
                    "rank": camera_rank(path, location),
                    "name": camera_name(path, location, dev.get_display_name()),
                    "device": dev,
                }
            )
    finally:
        provider.stop()
    return sorted(found, key=lambda c: c["rank"])


def read_picture(path):
    """The QR codes' texts in a picture file ([] when none or unreadable)."""
    if not can_read_pictures():
        return []
    for size in (PICTURE_MAX, PICTURE_MAX // 2):
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
        except GLib.Error:
            return []
        if pb.get_has_alpha():
            # A code drawn on a transparent background: put it on white.
            pb = pb.composite_color_simple(
                pb.get_width(),
                pb.get_height(),
                GdkPixbuf.InterpType.BILINEAR,
                255,
                32,
                0xFFFFFFFF,
                0xFFFFFFFF,
            )
        texts = _zbar_gray(
            gray_rows(
                pb.get_pixels(),
                pb.get_width(),
                pb.get_height(),
                pb.get_rowstride(),
                pb.get_n_channels(),
            ),
            pb.get_width(),
            pb.get_height(),
        )
        if texts:
            return texts
    return []


def _zbar_gray(data, width, height):
    pipe = Gst.parse_launch(
        "appsrc name=src format=time ! videoconvert ! zbar ! fakesink sync=false"
    )
    src = pipe.get_by_name("src")
    src.set_property(
        "caps",
        Gst.Caps.from_string(
            "video/x-raw,format=GRAY8,width=%d,height=%d,framerate=0/1" % (width, height)
        ),
    )
    texts = []
    pipe.set_state(Gst.State.PLAYING)
    try:
        src.emit("push-buffer", Gst.Buffer.new_wrapped(data))
        src.emit("end-of-stream")
        bus = pipe.get_bus()
        while True:
            msg = bus.timed_pop_filtered(
                10 * Gst.SECOND,
                Gst.MessageType.ELEMENT | Gst.MessageType.EOS | Gst.MessageType.ERROR,
            )
            if msg is None or msg.type != Gst.MessageType.ELEMENT:
                break
            s = msg.get_structure()
            if s is not None and s.get_name() == "barcode" and s.has_field("symbol"):
                texts.append(s.get_string("symbol"))
    finally:
        pipe.set_state(Gst.State.NULL)
    return texts


class ScanDialog(Adw.Dialog):
    """The live view of a camera; on_code(text) with the first QR code's
    text for which accept(text) is true (the dialog then closes). A code
    it does not accept is named under the picture and the scan goes on."""

    def __init__(self, on_code, accept=lambda _t: True, wrong=""):
        super().__init__(title=_("Scan the QR code"))
        self.set_content_width(480)
        self.set_content_height(640)
        self.on_code, self.accept, self.wrong = on_code, accept, wrong
        self.pipe = None
        self.cams = cameras()
        self.index = 0
        self.done = False

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.switch = Gtk.Button(icon_name="camera-switch-symbolic")
        self.switch.set_tooltip_text(_("Another camera"))
        self.switch.connect("clicked", lambda _b: self.next_camera())
        self.switch.set_sensitive(len(self.cams) > 1)
        header.pack_end(self.switch)
        view.add_top_bar(header)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_bottom(12)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, vexpand=True)
        self.picture.add_css_class("card")
        box.append(self.picture)
        self.status = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        box.append(self.status)
        view.set_content(box)
        self.set_child(view)
        self.connect("closed", lambda _d: self.stop())
        if self.cams:
            self.start()
        else:
            self.say(_("No camera was found."))

    def say(self, text):
        self.status.set_label(text)

    def next_camera(self):
        self.index = (self.index + 1) % len(self.cams)
        self.start()

    def start(self):
        self.stop()
        cam = self.cams[self.index]
        self.say(
            _("%s: hold the QR code in the middle, about a hand's width away.") % _(cam["name"])
        )
        src = cam["device"].create_element(None)
        # No turn of our own: PipeWire's libcamera plugin puts the sensor's
        # mounting on every buffer (SPA_META_VideoTransform, which
        # pipewiresrc turns into an image-orientation tag) and
        # gtk4paintablesink shows the picture turned by it, as Snapshot
        # does (docs/hardware/camera.md). A videoflip here turned it twice.
        rest = Gst.parse_bin_from_description(
            CAMERA_CAPS + " ! videoconvert ! tee name=t "
            "t. ! queue leaky=downstream max-size-buffers=1 ! videoconvert "
            "! gtk4paintablesink name=view sync=false "
            "t. ! queue leaky=downstream max-size-buffers=1 ! videoconvert ! zbar "
            "! fakesink sync=false async=false",
            True,
        )
        pipe = Gst.Pipeline.new("qrscan")
        pipe.add(src)
        pipe.add(rest)
        src.link(rest)
        sink = rest.get_by_name("view")
        self.picture.set_paintable(sink.get_property("paintable"))
        bus = pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.message)
        self.pipe = pipe
        if pipe.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.say(_("The camera could not be started."))

    def stop(self):
        if self.pipe is not None:
            bus = self.pipe.get_bus()
            bus.remove_signal_watch()
            self.pipe.set_state(Gst.State.NULL)
            self.pipe = None

    def message(self, _bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            err, _debug = msg.parse_error()
            self.say(_("The camera stopped: %s") % err.message)
            return
        if msg.type != Gst.MessageType.ELEMENT or self.done:
            return
        s = msg.get_structure()
        if s is None or s.get_name() != "barcode" or not s.has_field("symbol"):
            return
        text = s.get_string("symbol") or ""
        if not self.accept(text):
            self.say(self.wrong or _("That QR code is not the one needed."))
            return
        self.done = True
        self.stop()
        self.close()
        self.on_code(text)
