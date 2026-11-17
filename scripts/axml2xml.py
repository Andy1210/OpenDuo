#!/usr/bin/env python3
"""Decode an Android compiled XML resource (binary AXML, as found inside an
APK's res/xml/) back into plain XML text.

    python3 -I scripts/axml2xml.py FILE.apk res/xml/power_profile.xml > out.xml
    python3 -I scripts/axml2xml.py compiled.xml > out.xml

Only what resource XML uses: the string pool (UTF-8 and UTF-16), namespaces,
elements, attributes (strings and typed values) and text. Our own code; it
reads the phone's own stock images (scripts/build-power-profile.sh).
"""

import struct
import sys
import zipfile
from xml.sax.saxutils import escape, quoteattr

RES_STRING_POOL = 0x0001
RES_XML = 0x0003
RES_XML_START_NS = 0x0100
RES_XML_END_NS = 0x0101
RES_XML_START_ELEMENT = 0x0102
RES_XML_END_ELEMENT = 0x0103
RES_XML_CDATA = 0x0104
NO_INDEX = 0xFFFFFFFF


def _string_pool(buf, off):
    _t, hsize, _size, count, _styles, flags, start, _sstart = struct.unpack_from(
        "<HHIIIIII", buf, off
    )
    utf8 = bool(flags & 0x100)
    offsets = struct.unpack_from("<%dI" % count, buf, off + hsize)
    base = off + start
    out = []
    for o in offsets:
        p = base + o
        if utf8:
            n = buf[p]
            p += 2 if n & 0x80 else 1  # UTF-16 length, skipped
            n = buf[p]
            if n & 0x80:
                n = ((n & 0x7F) << 8) | buf[p + 1]
                p += 2
            else:
                p += 1
            out.append(buf[p : p + n].decode("utf-8", "replace"))
        else:
            n = struct.unpack_from("<H", buf, p)[0]
            p += 2
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", buf, p)[0]
                p += 2
            out.append(buf[p : p + 2 * n].decode("utf-16-le", "replace"))
    return out


def _typed(dtype, data, strings):
    if dtype == 0x03:  # string
        return strings[data] if data < len(strings) else ""
    if dtype == 0x04:  # float
        return repr(struct.unpack("<f", struct.pack("<I", data))[0])
    if dtype == 0x10:  # int (decimal)
        return str(struct.unpack("<i", struct.pack("<I", data))[0])
    if dtype == 0x11:  # int (hex)
        return "0x%08x" % data
    if dtype == 0x12:  # boolean
        return "true" if data else "false"
    if dtype == 0x01:  # reference
        return "@0x%08x" % data
    return "0x%x" % data


def decode(buf):
    """The XML text of a compiled XML resource."""
    t, hsize, size = struct.unpack_from("<HHI", buf, 0)
    if t != RES_XML:
        raise ValueError("not a compiled XML resource")
    strings = []
    ns_pending = []
    prefixes = {}
    lines = ['<?xml version="1.0" encoding="utf-8"?>']
    depth = 0
    text_open = False
    off = hsize
    while off + 8 <= min(size, len(buf)):
        ct, chs, csize = struct.unpack_from("<HHI", buf, off)
        if csize < 8:
            break
        if ct == RES_STRING_POOL:
            strings = _string_pool(buf, off)
        elif ct == RES_XML_START_NS:
            prefix, uri = struct.unpack_from("<II", buf, off + chs)
            prefixes[uri] = strings[prefix] if prefix != NO_INDEX else ""
            ns_pending.append((prefixes[uri], strings[uri]))
        elif ct == RES_XML_START_ELEMENT:
            ns, name, astart, asize, acount = struct.unpack_from("<IIHHH", buf, off + chs)
            attrs = []
            for prefix, uri in ns_pending:
                attrs.append(
                    " xmlns:%s=%s" % (prefix, quoteattr(uri))
                    if prefix
                    else " xmlns=%s" % quoteattr(uri)
                )
            ns_pending = []
            a = off + chs + astart
            for _i in range(acount):
                ans, aname, raw, _vs, _r0, dtype, data = struct.unpack_from("<IIIHBBI", buf, a)
                value = strings[raw] if raw != NO_INDEX else _typed(dtype, data, strings)
                qual = strings[aname]
                if ans != NO_INDEX and prefixes.get(ans):
                    qual = prefixes[ans] + ":" + qual
                attrs.append(" %s=%s" % (qual, quoteattr(value)))
                a += asize
            tag = strings[name]
            lines.append("    " * depth + "<%s%s>" % (tag, "".join(attrs)))
            depth += 1
            text_open = True
        elif ct == RES_XML_CDATA:
            data = struct.unpack_from("<I", buf, off + chs)[0]
            _vs, _r0, dtype, tdata = struct.unpack_from("<HBBI", buf, off + chs + 4)
            text = strings[data] if data != NO_INDEX else _typed(dtype, tdata, strings)
            if text_open and lines:
                lines[-1] += escape(text.strip())
                text_open = "inline"
        elif ct == RES_XML_END_ELEMENT:
            name = struct.unpack_from("<II", buf, off + chs)[1]
            depth -= 1
            if text_open == "inline" or (
                text_open
                and lines[-1].endswith(">")
                and lines[-1].lstrip().startswith("<" + strings[name])
            ):
                lines[-1] += "</%s>" % strings[name]
            else:
                lines.append("    " * depth + "</%s>" % strings[name])
            text_open = False
        off += csize
    return "\n".join(lines) + "\n"


def main(argv):
    if len(argv) not in (2, 3):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    if len(argv) == 3:
        with zipfile.ZipFile(argv[1]) as z:
            buf = z.read(argv[2])
    else:
        with open(argv[1], "rb") as f:
            buf = f.read()
    sys.stdout.write(decode(buf))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
