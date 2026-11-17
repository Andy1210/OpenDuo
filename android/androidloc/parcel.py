"""Android Parcel data, written and read as bytes (pure).

duo2-android-location talks to Android's LocationManagerService over
binder (libgbinder writes the RPC header with the interface token; the
arguments after it are these bytes, appended as they are). The layout is
Android's own (frameworks/base, Parcel.java and the AIDL-generated
proxies): little-endian, every item padded to 4 bytes.

  int / boolean   4 bytes (a boolean is an int, 0 or 1)
  long / double   8 bytes, float 4
  String          writeString (UTF-16): int32 length in code units (-1 for
                  null), the units, a 0 unit, padding
  String8         writeString8 (UTF-8, Location's provider name): int32
                  length in bytes (-1 for null), the bytes, a 0, padding
  List<String>    int32 count (-1 for null), then each String
  in Parcelable   int32 1 (0 for null), then its writeToParcel
"""

import struct


def _pad(n):
    return (4 - n % 4) % 4


class Writer:
    def __init__(self):
        self.buf = bytearray()

    def int32(self, value):
        self.buf += struct.pack("<i", int(value))
        return self

    def int64(self, value):
        self.buf += struct.pack("<q", int(value))
        return self

    def boolean(self, value):
        return self.int32(1 if value else 0)

    def float32(self, value):
        self.buf += struct.pack("<f", float(value))
        return self

    def double(self, value):
        self.buf += struct.pack("<d", float(value))
        return self

    def string16(self, value):
        if value is None:
            return self.int32(-1)
        data = value.encode("utf-16-le")
        self.int32(len(data) // 2)
        data += b"\0\0"
        self.buf += data + b"\0" * _pad(len(data))
        return self

    def string8(self, value):
        if value is None:
            return self.int32(-1)
        data = value.encode("utf-8")
        self.int32(len(data))
        data += b"\0"
        self.buf += data + b"\0" * _pad(len(data))
        return self

    def string_list(self, items):
        if items is None:
            return self.int32(-1)
        self.int32(len(items))
        for item in items:
            self.string16(item)
        return self

    def raw(self, data):
        self.buf += data
        return self

    def bytes(self):
        return bytes(self.buf)


def words(payload):
    """The bytes as unsigned 32-bit little-endian words (what libgbinder's
    append_int32 writes on this little-endian phone)."""
    if len(payload) % 4:
        raise ValueError("a parcel is made of 4-byte items")
    return [w for (w,) in struct.iter_unpack("<I", payload)]


class Reader:
    """Reads what Writer writes (the tests), with the method names of
    libgbinder's Python reader, so the code that parses an incoming
    transaction runs on either."""

    def __init__(self, data):
        self.data = bytes(data)
        self.pos = 0

    def _take(self, n):
        if self.pos + n > len(self.data):
            raise ValueError("parcel too short")
        out = self.data[self.pos : self.pos + n]
        self.pos += n
        return out

    def read_int32(self):
        return struct.unpack("<i", self._take(4))[0]

    def read_int64(self):
        return struct.unpack("<q", self._take(8))[0]

    def read_float(self):
        return struct.unpack("<f", self._take(4))[0]

    def read_double(self):
        return struct.unpack("<d", self._take(8))[0]

    def read_string16(self):
        n = self.read_int32()
        if n < 0:
            return None
        size = (n + 1) * 2
        data = self._take(size + _pad(size))
        return data[: n * 2].decode("utf-16-le")

    def read_string8(self):
        n = self.read_int32()
        if n < 0:
            return None
        size = n + 1
        data = self._take(size + _pad(size))
        return data[:n].decode("utf-8")

    def at_end(self):
        return self.pos >= len(self.data)
