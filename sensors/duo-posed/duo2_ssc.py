# SPDX-License-Identifier: GPL-3.0-or-later
"""A small client for Qualcomm's Sensor Core (SSC) on the SLPI, stdlib only.

The SSC is a QMI service (400) on the SLPI's QRTR node. Every request is one
QMI "Control" message (0x0020) carrying a protobuf `sns_client_request_msg`;
every sensor event comes back as a QMI indication (0x0021 small / 0x0022
large) carrying a protobuf `sns_client_event_msg`. The field numbers below
are the public Snapdragon Sensor Core message layout (the same that libssc,
GPL-3+, describes in its data/*.proto); Microsoft's own sensors (hinge angle,
hall, posture) use the standard sensor event or tiny messages of their own,
documented where they are decoded.

Only what duo-posed needs: find sensors by data type, read their attributes,
enable (streaming or on-change), disable, and receive events.
"""

import os
import select
import socket
import struct
import time

AF_QIPCRTR = getattr(socket, "AF_QIPCRTR", 42)
QRTR_PORT_CTRL = 0xFFFFFFFE
QRTR_TYPE_NEW_SERVER = 4
QRTR_TYPE_DEL_SERVER = 5
QRTR_TYPE_NEW_LOOKUP = 10

SSC_SERVICE = 400
QMI_REQUEST, QMI_RESPONSE, QMI_INDICATION = 0x00, 0x02, 0x04
QMI_SSC_CONTROL, QMI_SSC_REPORT_SMALL, QMI_SSC_REPORT_LARGE = 0x20, 0x21, 0x22

SUID_LOOKUP = (0xABABABABABABABAB, 0xABABABABABABABAB)  # (low, high)

# message ids
MSG_ATTR_REQ = 1
MSG_ATTR_EVENT = 128
MSG_FLUSH_EVENT = 129
MSG_ERROR_EVENT = 130
MSG_SUID_REQ = 512
MSG_SUID_EVENT = 768
MSG_STD_SENSOR_CONFIG = 513  # streaming: sample rate
MSG_STD_ON_CHANGE_CONFIG = 514  # on-change sensors: empty payload
MSG_PHYSICAL_CONFIG_EVENT = 768  # (for a sensor, not the SUID lookup)
MSG_STD_SENSOR_EVENT = 1025  # repeated float data, status
MSG_CLIENT_DISABLE = 10
MSG_HALL_EVENT = 770  # sns_hall_event {1: near(1)/far(0), 2: status}
MSG_DEVICE_ORIENT_EVENT = 776  # sns_device_orient_event {1: state}
MSG_POSTURE_EVENT = 7788  # Microsoft posture {1: state}

# attribute ids (sns_std_sensor_attr_id)
ATTR_NAME, ATTR_VENDOR, ATTR_TYPE, ATTR_AVAILABLE = 0, 1, 2, 3
ATTR_RATES, ATTR_STREAM_TYPE, ATTR_HW_ID, ATTR_RIGID_BODY = 6, 16, 18, 19
ATTR_PLACEMENT, ATTR_PHYSICAL = 20, 21
STREAM_TYPES = {0: "streaming", 1: "on-change", 2: "single"}
RIGID_BODIES = {0: "display", 1: "keyboard", 2: "external"}

PROC_APSS = 1
DELIVERY_WAKEUP, DELIVERY_NO_WAKEUP = 0, 1


# ---- protobuf (proto2 wire format), just enough -------------------------


def _varint(n):
    if n < 0:
        n += 1 << 64
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def pb_varint(num, v):
    return _varint(num << 3) + _varint(int(v))


def pb_fixed32(num, v):
    return _varint(num << 3 | 5) + struct.pack("<I", v)


def pb_fixed64(num, v):
    return _varint(num << 3 | 1) + struct.pack("<Q", v)


def pb_float(num, v):
    return _varint(num << 3 | 5) + struct.pack("<f", v)


def pb_bytes(num, b):
    if isinstance(b, str):
        b = b.encode()
    return _varint(num << 3 | 2) + _varint(len(b)) + b


def _read_varint(buf, i):
    n = shift = 0
    while True:
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n, i
        shift += 7


def pb_decode(buf):
    """bytes -> {field: [(wire_type, value)]}; values are int or bytes."""
    out = {}
    i = 0
    n = len(buf)
    while i < n:
        key, i = _read_varint(buf, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _read_varint(buf, i)
        elif wt == 1:
            v = buf[i : i + 8]
            i += 8
        elif wt == 5:
            v = buf[i : i + 4]
            i += 4
        elif wt == 2:
            ln, i = _read_varint(buf, i)
            v = buf[i : i + ln]
            i += ln
        else:
            raise ValueError("protobuf wire type %d" % wt)
        out.setdefault(num, []).append((wt, v))
    return out


def pb_int(d, num, default=None, signed32=False):
    for wt, v in d.get(num, ()):
        if wt == 0:
            if signed32 and v >= 1 << 63:
                v -= 1 << 64
            return v
        if wt == 1:
            return struct.unpack("<Q", v)[0]
        if wt == 5:
            return struct.unpack("<I", v)[0]
    return default


def pb_u64(d, num, default=None):
    for wt, v in d.get(num, ()):
        if wt == 1:
            return struct.unpack("<Q", v)[0]
    return default


def pb_floats(d, num):
    vals = []
    for wt, v in d.get(num, ()):
        if wt == 5:
            vals.append(struct.unpack("<f", v)[0])
        elif wt == 2:  # packed
            vals.extend(struct.unpack("<%df" % (len(v) // 4), v[: len(v) // 4 * 4]))
    return vals


def pb_msgs(d, num):
    return [pb_decode(v) for wt, v in d.get(num, ()) if wt == 2]


def pb_str(d, num, default=None):
    for wt, v in d.get(num, ()):
        if wt == 2:
            return v.decode(errors="replace").rstrip("\0")
    return default


# ---- SSC messages -------------------------------------------------------


def suid_msg(suid):
    low, high = suid
    return pb_fixed64(1, low) + pb_fixed64(2, high)


def client_request(
    suid, msg_id, payload=b"", batch_period_us=None, passive=False, delivery=DELIVERY_NO_WAKEUP
):
    susp = pb_varint(1, PROC_APSS) + pb_varint(2, delivery)
    req = b""
    if batch_period_us is not None:
        req += pb_bytes(1, pb_varint(1, batch_period_us))
    if payload:
        req += pb_bytes(2, payload)
    if passive:
        req += pb_varint(3, 1)
    return (
        pb_bytes(1, suid_msg(suid)) + pb_fixed32(2, msg_id) + pb_bytes(3, susp) + pb_bytes(4, req)
    )


def decode_attr_value(d):
    """sns_std_attr_value -> list of python values."""
    vals = []
    for data in pb_msgs(d, 1):
        if 1 in data:
            vals.append(decode_attr_value(pb_decode(data[1][0][1])))
        elif 2 in data:
            vals.append(pb_str(data, 2))
        elif 3 in data:
            vals.append(pb_floats(data, 3)[0])
        elif 4 in data:
            v = struct.unpack("<q", data[4][0][1])[0] if data[4][0][0] == 1 else pb_int(data, 4)
            vals.append(v)
        elif 5 in data:
            vals.append(bool(pb_int(data, 5)))
    return vals


def decode_attributes(payload):
    attrs = {}
    for a in pb_msgs(pb_decode(payload), 1):
        aid = pb_int(a, 1, signed32=True)
        val = pb_decode(a[2][0][1]) if 2 in a else {}
        attrs[aid] = decode_attr_value(val)
    return attrs


class SscError(Exception):
    pass


class Sensor:
    """One SSC sensor: its SUID and attributes."""

    def __init__(self, suid, attrs):
        self.suid = suid
        self.attrs = attrs

    def attr(self, aid, default=None):
        v = self.attrs.get(aid)
        return v[0] if v else default

    @property
    def name(self):
        return self.attr(ATTR_NAME, "?")

    @property
    def data_type(self):
        return self.attr(ATTR_TYPE, "?")

    @property
    def stream_type(self):
        return STREAM_TYPES.get(self.attr(ATTR_STREAM_TYPE), "?")

    @property
    def hw_id(self):
        return self.attr(ATTR_HW_ID)

    @property
    def rigid_body(self):
        return self.attr(ATTR_RIGID_BODY)

    def describe(self):
        rb = self.rigid_body
        return "%s (%s, %s, vendor %s, hw_id %s, rigid body %s, available %s, rates %s)" % (
            self.name,
            self.data_type,
            self.stream_type,
            self.attr(ATTR_VENDOR),
            self.hw_id,
            RIGID_BODIES.get(rb, rb),
            self.attr(ATTR_AVAILABLE),
            self.attrs.get(ATTR_RATES),
        )

    def __repr__(self):
        return "<Sensor %s %016x%016x>" % (self.name, self.suid[1], self.suid[0])


class SscClient:
    """One QRTR socket = one SSC client."""

    def __init__(self, timeout=10.0):
        self.timeout = timeout
        self.sock = socket.socket(AF_QIPCRTR, socket.SOCK_DGRAM)
        self.local_node = self.sock.getsockname()[0]
        self.txn = 0
        self.pending = []  # events that arrived while waiting for a response
        self.addr = self._lookup_service()

    def close(self):
        self.sock.close()

    def fileno(self):
        return self.sock.fileno()

    def _lookup_service(self):
        pkt = struct.pack("<5I", QRTR_TYPE_NEW_LOOKUP, SSC_SERVICE, 0, 0, 0)
        self.sock.sendto(pkt, (self.local_node, QRTR_PORT_CTRL))
        deadline = time.monotonic() + self.timeout
        found = None
        while time.monotonic() < deadline:
            r, _, _ = select.select([self.sock], [], [], max(0.0, deadline - time.monotonic()))
            if not r:
                break
            data, addr = self.sock.recvfrom(65536)
            if addr[1] != QRTR_PORT_CTRL or len(data) < 20:
                continue
            cmd, svc, inst, node, port = struct.unpack("<5I", data[:20])
            if cmd == QRTR_TYPE_NEW_SERVER:
                if svc == 0 and node == 0 and port == 0:
                    break  # end of the lookup list
                if svc == SSC_SERVICE and found is None:
                    found = (node, port)
        if found is None:
            raise SscError("no SSC service (QMI 400) on QRTR: is the SLPI up?")
        return found

    # -- QMI ----------------------------------------------------------------

    def _send_control(self, proto):
        self.txn = (self.txn + 1) & 0xFFFF or 1
        tlvs = (
            struct.pack("<BHH", 0x01, len(proto) + 2, len(proto))
            + proto
            + struct.pack("<BHB", 0x10, 1, 1)
        )  # report type: large
        msg = struct.pack("<BHHH", QMI_REQUEST, self.txn, QMI_SSC_CONTROL, len(tlvs)) + tlvs
        self.sock.sendto(msg, self.addr)
        return self.txn

    @staticmethod
    def _tlvs(body):
        out = {}
        i = 0
        while i + 3 <= len(body):
            t, ln = struct.unpack_from("<BH", body, i)
            out[t] = body[i + 3 : i + 3 + ln]
            i += 3 + ln
        return out

    def _recv(self, timeout):
        """One QMI message from the SSC: ('resp', txn, tlvs) or ('ind', None, tlvs)."""
        r, _, _ = select.select([self.sock], [], [], timeout)
        if not r:
            return None
        data, addr = self.sock.recvfrom(65536)
        if addr[1] == QRTR_PORT_CTRL:
            if len(data) >= 20:
                cmd, svc, inst, node, port = struct.unpack("<5I", data[:20])
                if cmd == QRTR_TYPE_DEL_SERVER and (node, port) == self.addr:
                    raise SscError("SSC service went away (SLPI restart?)")
            return ("ctrl", None, None)
        if len(data) < 7:
            return ("junk", None, None)
        typ, txn, mid, ln = struct.unpack_from("<BHHH", data, 0)
        tlvs = self._tlvs(data[7 : 7 + ln])
        if typ == QMI_RESPONSE and mid == QMI_SSC_CONTROL:
            return ("resp", txn, tlvs)
        if typ == QMI_INDICATION and mid in (QMI_SSC_REPORT_SMALL, QMI_SSC_REPORT_LARGE):
            return ("ind", None, tlvs)
        return ("other", None, None)

    @staticmethod
    def _events_of(tlvs):
        raw = tlvs.get(0x02, b"")
        if len(raw) < 2:
            return []
        n = struct.unpack_from("<H", raw)[0]
        msg = pb_decode(raw[2 : 2 + n])
        sm = pb_msgs(msg, 1)
        suid = (pb_u64(sm[0], 1, 0), pb_u64(sm[0], 2, 0)) if sm else (0, 0)
        out = []
        for ev in pb_msgs(msg, 2):
            out.append((suid, pb_int(ev, 1), pb_u64(ev, 2, 0), ev[3][0][1] if 3 in ev else b""))
        return out

    def request(self, suid, msg_id, payload=b"", **kw):
        txn = self._send_control(client_request(suid, msg_id, payload, **kw))
        deadline = time.monotonic() + self.timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise SscError("no QMI response to msg %d" % msg_id)
            m = self._recv(left)
            if m is None:
                continue
            kind, rtxn, tlvs = m
            if kind == "ind":
                self.pending.extend(self._events_of(tlvs))
            elif kind == "resp" and rtxn == txn:
                res = tlvs.get(0x02, b"\0\0\0\0")
                result, error = struct.unpack("<HH", res[:4])
                if result != 0:
                    raise SscError("QMI error %d for msg %d" % (error, msg_id))
                rv = tlvs.get(0x11)
                return struct.unpack("<I", rv)[0] if rv and len(rv) == 4 else 0

    def events(self, timeout):
        """Events received so far plus those arriving within `timeout` s
        (returns as soon as at least one is there)."""
        if self.pending:
            out, self.pending = self.pending, []
            return out
        m = self._recv(timeout)
        if m is None:
            return []
        kind, _, tlvs = m
        if kind == "ind":
            return self._events_of(tlvs)
        return []

    def wait_event(self, suid, msg_ids, timeout):
        deadline = time.monotonic() + timeout
        keep = []
        try:
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                for ev in self.events(left):
                    if ev[0] == suid and ev[1] in msg_ids:
                        rest = self.pending
                        self.pending = keep
                        keep = []
                        self.pending.extend(rest)
                        return ev
                    keep.append(ev)
        finally:
            self.pending = keep + self.pending

    # -- sensors ------------------------------------------------------------

    def lookup(self, data_type, timeout=None):
        """SUIDs of every sensor of a data type ("accel", "hinge_angle"...)."""
        payload = pb_bytes(1, data_type) + pb_varint(2, 0) + pb_varint(3, 0)
        self.request(SUID_LOOKUP, MSG_SUID_REQ, payload)
        deadline = time.monotonic() + (timeout or self.timeout)
        while time.monotonic() < deadline:
            ev = self.wait_event(SUID_LOOKUP, (MSG_SUID_EVENT,), deadline - time.monotonic())
            if ev is None:
                break
            d = pb_decode(ev[3])
            if pb_str(d, 1) != data_type:
                continue
            return [(pb_u64(s, 1, 0), pb_u64(s, 2, 0)) for s in pb_msgs(d, 2)]
        return []

    def attributes(self, suid, timeout=None):
        self.request(suid, MSG_ATTR_REQ, b"")
        ev = self.wait_event(suid, (MSG_ATTR_EVENT,), timeout or self.timeout)
        if ev is None:
            raise SscError("no attributes for sensor %r" % (suid,))
        return decode_attributes(ev[3])

    def sensors(self, data_type):
        return [Sensor(s, self.attributes(s)) for s in self.lookup(data_type)]

    def enable(self, sensor, rate_hz=None, batch_period_us=None, wakeup=False):
        """Streaming sensors take a sample rate, on-change sensors none.
        wakeup: the SSC sends its events while the application processor
        is suspended (they may wake it); otherwise it holds them back."""
        delivery = DELIVERY_WAKEUP if wakeup else DELIVERY_NO_WAKEUP
        if sensor.stream_type == "streaming":
            payload = pb_float(1, float(rate_hz or 10.0))
            return self.request(
                sensor.suid,
                MSG_STD_SENSOR_CONFIG,
                payload,
                batch_period_us=batch_period_us,
                delivery=delivery,
            )
        return self.request(
            sensor.suid,
            MSG_STD_ON_CHANGE_CONFIG,
            b"",
            batch_period_us=batch_period_us,
            delivery=delivery,
        )

    def disable(self, sensor):
        return self.request(sensor.suid, MSG_CLIENT_DISABLE, b"")


# ---- event payloads -------------------------------------------------------


def std_event(payload):
    """sns_std_sensor_event -> (data floats, status)."""
    d = pb_decode(payload)
    return pb_floats(d, 1), pb_int(d, 2, 0)


def state_event(payload):
    """A Microsoft/Qualcomm one-enum event ({1: state}: posture, orientation)."""
    return pb_int(pb_decode(payload), 1, -1, signed32=True)


def hall_event(payload):
    d = pb_decode(payload)
    return pb_int(d, 1, 0), pb_int(d, 2, 0)


POSTURES = {
    -1: "unknown",
    0: "closed",
    1: "peek_right",
    2: "peek_left",
    3: "read",
    4: "compose",
    5: "open_dpm",
    6: "open_dlm",
    7: "brochure_right",
    8: "tent_right",
    9: "flip_p_right",
    10: "flip_l_right",
    11: "flip_p_left",
    12: "flip_l_left",
    13: "brochure_left",
    14: "tent_left",
    15: "ramp_right",
    16: "ramp_left",
    99: "undetermined",
}


def describe_event(sensor, msg_id, payload):
    if msg_id == MSG_STD_SENSOR_EVENT:
        data, status = std_event(payload)
        text = "data %s status %d" % (" ".join("%.3f" % v for v in data), status)
        if sensor is not None and sensor.data_type == "posture" and data and any(data):
            # Microsoft's posture: data[0] the posture, data[5] its hinge angle
            st = int(round(data[0]))
            text += " = posture %d (%s)" % (st, POSTURES.get(st, "?"))
        return text
    if msg_id == MSG_POSTURE_EVENT:
        s = state_event(payload)
        return "posture %d (%s)" % (s, POSTURES.get(s, "?"))
    if msg_id == MSG_HALL_EVENT:
        near, status = hall_event(payload)
        return "hall %s status %d" % ("near" if near else "far", status)
    if msg_id == MSG_DEVICE_ORIENT_EVENT:
        return "device orientation %d" % state_event(payload)
    if msg_id == MSG_PHYSICAL_CONFIG_EVENT:
        d = pb_decode(payload)
        return "physical config: rate %s" % (pb_floats(d, 1) or "-")
    if msg_id == MSG_ERROR_EVENT:
        return "error %s" % pb_int(pb_decode(payload), 1)
    if msg_id == MSG_ATTR_EVENT:
        return "attributes"
    return "msg %d: %s" % (msg_id, payload.hex())


def ssc_available():
    return os.path.exists("/dev/fastrpc-sdsp")
