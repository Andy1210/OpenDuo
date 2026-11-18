"""The QR code reader's pure parts (qrscan.py is its GTK and GStreamer
side): which camera to offer first and what to call it, a picture's grey
rows for zbar. Tested in tests/test_qr.py."""

# The Surface Duo 2's cameras by their libcamera path (the device tree's
# node): what to call them, and the order to try them for a QR code. The
# ultra-wide has a fixed focus that is sharp from about 10 cm; the main and
# the tele lenses have no autofocus driver yet and rest at their far stop,
# so a code held close is blurred there; the front one is awkward to aim.
CAMERAS = {
    "/base/soc@0/cci@ac50000/i2c-bus@0/camera@10": (0, "Ultra-wide camera"),
    "/base/soc@0/cci@ac4f000/i2c-bus@1/camera@1a": (1, "Main camera"),
    "/base/soc@0/cci@ac4f000/i2c-bus@0/camera@1a": (2, "Telephoto camera"),
    "/base/soc@0/cci@ac50000/i2c-bus@1/camera@1a": (3, "Front camera"),
}


def camera_rank(path, location):
    """The order a camera is offered in (lower first): the known ones by
    CAMERAS, then any other back camera, then the rest."""
    if path in CAMERAS:
        return CAMERAS[path][0]
    return 10 if location == "back" else 20


def camera_name(path, location, fallback):
    if path in CAMERAS:
        return CAMERAS[path][1]
    if location == "back":
        return "Back camera"
    if location == "front":
        return "Front camera"
    return fallback or "Camera"


def gray_rows(pixels, width, height, rowstride, channels):
    """A picture's green channel (close enough to its brightness for a QR
    code) as GStreamer's GRAY8: each row padded to a multiple of 4 bytes."""
    stride = (width + 3) & ~3
    pad = bytes(stride - width)
    out = bytearray()
    for y in range(height):
        start = y * rowstride + (1 if channels >= 3 else 0)
        out += pixels[start : start + width * channels : channels]
        out += pad
    return bytes(out)
