#!/usr/bin/python3 -I
"""qmi-nas-siginfo.py: the modem's NAS "Signal Info" reporting, from its own QMI client.

    qmi-nas-siginfo.py count SECONDS      register for Signal Info indications
                                          on this client, count them
    qmi-nas-siginfo.py ab SECONDS DB      one client: count SECONDS, set the
                                          deltas to DB dB, count SECONDS again
    qmi-nas-siginfo.py hold SECONDS       configure Signal Info on this client
                                          but do not register for it; hold the
                                          client SECONDS (whom does the modem
                                          send them to then?)
    qmi-nas-siginfo.py deltas DB [NR_DB]  Config Signal Info v2: report LTE and
                                          5G NR changes only beyond DB dB
                                          (RSSI, SNR, RSRQ, RSRP; 0 = the
                                          modem's default)
    qmi-nas-siginfo.py takeover SECONDS   register Signal Info on this client,
                                          then unregister it, hold the client
                                          SECONDS and count what still comes
                                          (does the modem then stop sending?)
    qmi-nas-siginfo.py rate SECONDS R A   register here, LTE and NR5G report
                                          rate R s and averaging A s (1-5;
                                          0 = default), count SECONDS
    qmi-nas-siginfo.py ssp                the system selection preference (read)
    qmi-nas-siginfo.py ssp-hold SECONDS MASK
                                          the RAT mode preference MASK (LTE 16,
                                          5G NR 64, ...) for SECONDS, "until the
                                          next power cycle" (never PERMANENT,
                                          which ModemManager uses: an NV write),
                                          then the original mask back, also on
                                          SIGTERM / SIGINT / SIGHUP

An experiment tool (stdlib only, QMI over QRTR like duo2_ssc.py): does a
second client's Config Signal Info change what ModemManager's client gets
(per-modem setting), or only its own (per-client)? ModemManager configures
only the RSSI deltas (5 dB), and the modem then reports about once a second
(SNR / RSRQ / RSRP moving), which wakes the phone from suspend.
"""

import select
import socket
import struct
import sys
import time

AF_QIPCRTR = 42
QRTR_PORT_CTRL = 0xFFFFFFFE
QRTR_TYPE_NEW_SERVER, QRTR_TYPE_NEW_LOOKUP = 4, 10
QMI_NAS = 3
QMI_REQUEST, QMI_RESPONSE, QMI_INDICATION = 0, 2, 4
MSG_REGISTER_INDICATIONS = 0x0003
MSG_CONFIG_SIGNAL_INFO_V2 = 0x006C
MSG_SIGNAL_INFO_IND = 0x0051
MSG_SET_SSP, MSG_GET_SSP = 0x0033, 0x0034
SSP_MODE_PREF, SSP_CHANGE_DURATION, DURATION_POWER_CYCLE = 0x11, 0x17, 0x00
RATS = {1: "cdma1x", 2: "evdo", 4: "gsm", 8: "umts", 16: "lte", 32: "tdscdma", 64: "5gnr"}

# Config Signal Info v2 delta TLVs (guint16, 0.1 dB), from libqmi's NAS data
DELTAS_LTE = {0x23: "LTE RSSI", 0x25: "LTE SNR", 0x27: "LTE RSRQ", 0x29: "LTE RSRP"}
DELTAS_NR = {0x34: "NR5G SNR", 0x36: "NR5G RSRP", 0x39: "NR5G RSRQ"}


class Qmi:
    def __init__(self, service, timeout=5.0):
        self.sock = socket.socket(AF_QIPCRTR, socket.SOCK_DGRAM)
        self.local = self.sock.getsockname()[0]
        self.txn = 0
        self.last = {}
        self.addr = self.lookup(service, timeout)

    def lookup(self, service, timeout):
        self.sock.sendto(
            struct.pack("<5I", QRTR_TYPE_NEW_LOOKUP, service, 0, 0, 0), (self.local, QRTR_PORT_CTRL)
        )
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            r, _, _ = select.select([self.sock], [], [], max(0.0, end - time.monotonic()))
            if not r:
                break
            data, addr = self.sock.recvfrom(65536)
            if addr[1] != QRTR_PORT_CTRL or len(data) < 20:
                continue
            cmd, svc, inst, node, port = struct.unpack("<5I", data[:20])
            if cmd == QRTR_TYPE_NEW_SERVER and svc == service:
                return (node, port)
        raise SystemExit("no QMI service %d on QRTR" % service)

    def request(self, msg_id, tlvs, timeout=5.0):
        self.txn = (self.txn + 1) & 0xFFFF or 1
        body = b"".join(struct.pack("<BH", t, len(v)) + v for t, v in tlvs)
        self.sock.sendto(
            struct.pack("<BHHH", QMI_REQUEST, self.txn, msg_id, len(body)) + body, self.addr
        )
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            m = self.recv(max(0.0, end - time.monotonic()))
            if m and m[0] == QMI_RESPONSE and m[1] == self.txn:
                res = m[3].get(0x02, b"\0\0\0\0")
                result, error = struct.unpack("<HH", res[:4])
                self.last = m[3]
                return result, error
        raise SystemExit("no response to 0x%04x" % msg_id)

    def recv(self, timeout):
        r, _, _ = select.select([self.sock], [], [], timeout)
        if not r:
            return None
        data, addr = self.sock.recvfrom(65536)
        if addr != self.addr or len(data) < 7:
            return None
        typ, txn, mid, ln = struct.unpack_from("<BHHH", data, 0)
        tlvs, i, body = {}, 0, data[7 : 7 + ln]
        while i + 3 <= len(body):
            t, n = struct.unpack_from("<BH", body, i)
            tlvs[t] = body[i + 3 : i + 3 + n]
            i += 3 + n
        return typ, txn, mid, tlvs


def count(q, secs):
    n, end = 0, time.monotonic() + secs
    while time.monotonic() < end:
        m = q.recv(max(0.0, end - time.monotonic()))
        if m and m[0] == QMI_INDICATION and m[2] == MSG_SIGNAL_INFO_IND:
            n += 1
    return n


def rats(mask):
    return "+".join(n for b, n in sorted(RATS.items()) if mask & b) or "none"


def get_mode_pref(q):
    res = q.request(MSG_GET_SSP, [])
    if res != (0, 0) or SSP_MODE_PREF not in q.last:
        raise SystemExit("get system selection preference: %s" % (res,))
    return struct.unpack("<H", q.last[SSP_MODE_PREF][:2])[0]


def set_mode_pref(q, mask):
    return q.request(
        MSG_SET_SSP,
        [
            (SSP_MODE_PREF, struct.pack("<H", mask)),
            (SSP_CHANGE_DURATION, bytes([DURATION_POWER_CYCLE])),
        ],
    )


def main():
    if (
        len(sys.argv) < 2
        or sys.argv[1]
        not in ("count", "deltas", "ab", "hold", "takeover", "rate", "ssp", "ssp-hold")
        or (sys.argv[1] != "ssp" and len(sys.argv) < 3)
    ):
        raise SystemExit(__doc__)
    q = Qmi(QMI_NAS)
    if sys.argv[1] == "ssp":
        mask = get_mode_pref(q)
        print("mode preference 0x%02x (%s)" % (mask, rats(mask)))
        for t, name in (
            (0x22, "disabled modes"),
            (0x1F, "usage preference"),
            (0x20, "voice domain preference"),
            (0x18, "service domain preference"),
        ):
            if t in q.last:
                v = q.last[t]
                print("%s: 0x%x" % (name, int.from_bytes(v, "little")))
        return
    if sys.argv[1] == "ssp-hold":
        import signal

        secs, mask = float(sys.argv[2]), int(sys.argv[3], 0)
        orig = get_mode_pref(q)
        stop = []
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, lambda *a: stop.append(1))
        print(
            time.strftime("%H:%M:%S"),
            "mode preference 0x%02x (%s) -> 0x%02x (%s), until power cycle:"
            % (orig, rats(orig), mask, rats(mask)),
            set_mode_pref(q, mask),
            flush=True,
        )
        try:
            end = time.monotonic() + secs
            while not stop and time.monotonic() < end:
                q.recv(min(1.0, max(0.0, end - time.monotonic())))
        finally:
            print(
                time.strftime("%H:%M:%S"),
                "back to 0x%02x:" % orig,
                set_mode_pref(q, orig),
                "now 0x%02x" % get_mode_pref(q),
                flush=True,
            )
        return
    if sys.argv[1] == "takeover":
        secs = float(sys.argv[2])
        print(
            "register signal_info=1:",
            q.request(MSG_REGISTER_INDICATIONS, [(0x19, b"\x01")]),
            "then 0:",
            q.request(MSG_REGISTER_INDICATIONS, [(0x19, b"\x00")]),
            flush=True,
        )
        print(
            time.strftime("%H:%M:%S"),
            "held: %d indications to this client in %.0f s" % (count(q, secs), secs),
        )
        return
    if sys.argv[1] == "rate":
        secs, rate, avg = float(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
        print(
            "register:",
            q.request(MSG_REGISTER_INDICATIONS, [(0x19, b"\x01")]),
            "report rate %d s, average %d s:" % (rate, avg),
            q.request(
                MSG_CONFIG_SIGNAL_INFO_V2, [(0x2A, bytes([rate, avg])), (0x37, bytes([rate, avg]))]
            ),
            flush=True,
        )
        print(time.strftime("%H:%M:%S"), "%d indications in %.0f s" % (count(q, secs), secs))
        return
    if sys.argv[1] == "ab":
        secs, db = float(sys.argv[2]), int(round(float(sys.argv[3]) * 10))
        print(
            "register Signal Info indications:",
            q.request(MSG_REGISTER_INDICATIONS, [(0x19, b"\x01")]),
        )
        print(
            time.strftime("%H:%M:%S"),
            "A: %d indications in %.0f s" % (count(q, secs), secs),
            flush=True,
        )
        tlvs = [(t, struct.pack("<H", db)) for t in list(DELTAS_LTE) + list(DELTAS_NR)]
        print(
            time.strftime("%H:%M:%S"),
            "deltas %.1f dB:" % (db / 10),
            q.request(MSG_CONFIG_SIGNAL_INFO_V2, tlvs),
            flush=True,
        )
        print(
            time.strftime("%H:%M:%S"),
            "B: %d indications in %.0f s" % (count(q, secs), secs),
            flush=True,
        )
        return
    if sys.argv[1] == "hold":
        secs = float(sys.argv[2])
        tlvs = [(t, struct.pack("<H", 50)) for t in list(DELTAS_LTE) + list(DELTAS_NR)]
        print(
            "config:",
            q.request(MSG_CONFIG_SIGNAL_INFO_V2, tlvs),
            "register signal_info=0:",
            q.request(MSG_REGISTER_INDICATIONS, [(0x19, b"\x00")]),
            flush=True,
        )
        print("held: %d indications to this client in %.0f s" % (count(q, secs), secs))
        return
    if sys.argv[1] == "count":
        secs = float(sys.argv[2])
        print(
            "register Signal Info indications:",
            q.request(MSG_REGISTER_INDICATIONS, [(0x19, b"\x01")]),
        )
        n, end = 0, time.monotonic() + secs
        while time.monotonic() < end:
            m = q.recv(max(0.0, end - time.monotonic()))
            if m and m[0] == QMI_INDICATION and m[2] == MSG_SIGNAL_INFO_IND:
                n += 1
        print("%d Signal Info indications in %.0f s" % (n, secs))
        return
    lte = int(round(float(sys.argv[2]) * 10))
    nr = int(round(float(sys.argv[3]) * 10)) if len(sys.argv) > 3 else lte
    tlvs = [(t, struct.pack("<H", lte)) for t in DELTAS_LTE] + [
        (t, struct.pack("<H", nr)) for t in DELTAS_NR
    ]
    print(
        "Config Signal Info v2 (LTE %.1f dB, NR %.1f dB):" % (lte / 10, nr / 10),
        q.request(MSG_CONFIG_SIGNAL_INFO_V2, tlvs),
    )


if __name__ == "__main__":
    main()
