// SPDX-License-Identifier: Apache-2.0
// duo2-touchd's pen: the pure rules (no I/O), host-tested (touch/tests).
//
//  - What a pen report's flags mean (the heat processor's PenReport, as the
//    stock service's InputEvdev::PenReports reads them).
//  - The pressure and tilt that go to the pen's tablet device. libinput takes
//    a pen's contact from its PRESSURE, never from BTN_TOUCH, when the device
//    has a pressure axis (evdev-tablet.c, detect_tool_contact), with default
//    thresholds of 5 % of the range to touch down and 1 % to lift: 3277 of
//    the library's 0..65535, about 200 of the pen's 4096 levels. A light
//    stroke would never ink and every stroke would start late. So the
//    libinput quirk (rootfs/overlay/usr/share/libinput/90-duo2-pen.quirks)
//    sets AttrPressureRange=2:1 and the tip's pressure here never drops
//    below kTipPressureMin (a hover's is 0): libinput's contact is exactly
//    the library's tip bit. (libinput 1.28 applies a tablet's quirk range
//    with hi and lo swapped, *hi = r.lower; with nothing sent between 1 and
//    2 the result is the same either way.)
//  - Where a report lands on its panel (the panel's own coordinates, the
//    calibration of the geometry, clamped last): the same as a contact.
//  - Palm rejection: which contacts the hand holding the pen makes.
//  - Keeping libinput's proximity: every frame carries MSC_TIMESTAMP, and a
//    pen in range gets a timestamp-only frame after kKeepAliveMs without one.
//  - The library's pen output timing, for the 30 s statistics (Timing).
#pragma once

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace pen {

// PenReport flags. (flags & (kInRange | kInvalid)) == kInRange: in range.
enum Flags : uint8_t {
    kTip = 1 << 0,        // the tip touches
    kInvalid = 1 << 1,
    kInRange = 1 << 2,
    kInvert = 1 << 3,     // the eraser end towards the screen
    kEraser = 1 << 4,     // the eraser touches
    kBarrel = 1 << 5,     // the barrel (side) button
};

struct Tool {
    bool in_range = false;
    bool rubber = false;  // the eraser end (BTN_TOOL_RUBBER), as the stock service makes it
    bool tip = false;     // the end towards the screen touches (the tip, or the eraser)
    bool barrel = false;
};

inline Tool decode(uint8_t flags) {
    Tool t;
    t.in_range = (flags & (kInRange | kInvalid)) == kInRange;
    if (!t.in_range) return t;
    t.rubber = flags & (kInvert | kEraser);
    t.tip = t.rubber ? (flags & kEraser) != 0 : (flags & kTip) != 0;
    t.barrel = flags & kBarrel;
    return t;
}

const int kTipPressureMin = 3;   // see above: the libinput quirk's 2:1
const int kPressureMax = 0xffff;

inline int pressure(bool tip, unsigned raw) {
    if (!tip) return 0;
    if (raw < (unsigned)kTipPressureMin) return kTipPressureMin;
    return raw > (unsigned)kPressureMax ? kPressureMax : (int)raw;
}

// Tilt: 0.01 degree, +-90 degrees (5730 units per radian for libinput).
const int kTiltMax = 9000;
inline int tilt(int t) { return t < -kTiltMax ? -kTiltMax : t > kTiltMax ? kTiltMax : t; }

inline int clampi(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }

// A point of the library's surface (raw_x on the stitched or per-panel
// surface, raw_y) on its panel: made local by x_offset, corrected by the
// panel's calibration of the geometry (m: x' = m0 x + m1 y + m2, y' = m3 x +
// m4 y + m5 in 0..1 of the panel), and only then clamped to the panel (the
// heat processor's output runs past the panel near the hinge).
inline void to_panel(int raw_x, int raw_y, int x_offset, int width, int height, const double m[6],
                     int *x, int *y) {
    double xn = (double)(raw_x - x_offset) / width, yn = (double)raw_y / height;
    *x = clampi((int)lround((m[0] * xn + m[1] * yn + m[2]) * width), 0, width - 1);
    *y = clampi((int)lround((m[3] * xn + m[4] * yn + m[5]) * height), 0, height - 1);
}

// Palm rejection, while the pen is in range and for kPalmGraceMs after it
// left: a contact that starts then is held back (never delivered, to its
// lift), and contacts already down when the pen comes into range become
// palms (MT_TOOL_PALM: libinput cancels them). Which contacts:
//  - both: on both panels (the default: the hand that holds the phone
//    touches the other panel too);
//  - panel: only on the panel the pen is over (the other one stays usable
//    with a finger);
//  - off.
enum PalmMode { kPalmOff = 0, kPalmPanel = 1, kPalmBoth = 2 };
const uint64_t kPalmGraceMs = 300;

inline const char *palm_name(PalmMode m) {
    return m == kPalmBoth ? "both" : m == kPalmPanel ? "panel" : "off";
}

// "both" (also "on"), "panel", "off".
inline bool palm_parse(const char *word, PalmMode *out) {
    if (!strcmp(word, "both") || !strcmp(word, "on")) {
        *out = kPalmBoth;
        return true;
    }
    if (!strcmp(word, "panel")) {
        *out = kPalmPanel;
        return true;
    }
    if (!strcmp(word, "off")) {
        *out = kPalmOff;
        return true;
    }
    return false;
}

// Where the pen is for the palm rules: the panel it is in range of (-1: out
// of range), the panel it was last in range of, when it left (0: never).
struct PalmPen {
    int panel = -1;
    int last_panel = -1;
    uint64_t out_ms = 0;
};

// Whether a contact that starts now on `panel` is the hand's.
inline bool palm_holds(PalmMode mode, const PalmPen &p, int panel, uint64_t now_ms) {
    if (mode == kPalmOff) return false;
    int near = p.panel;
    if (near < 0) {
        if (!p.out_ms || now_ms - p.out_ms >= kPalmGraceMs) return false;
        near = p.last_panel;
    }
    return mode == kPalmBoth || panel == near;
}

// Whether a contact already down on `panel` becomes a palm when the pen comes
// into range of `pen_panel`.
inline bool palm_cancels(PalmMode mode, int pen_panel, int panel) {
    return mode == kPalmBoth || (mode == kPalmPanel && panel == pen_panel);
}

// Keeping libinput's proximity (measured 2026-11-16). libinput arms a timer
// at every proximity-in of a pen and forces a proximity-out when no frame
// came for 50 ms (evdev-tablet.c, FORCED_PROXOUT_TIMEOUT, for tablets that
// never send BTN_TOOL_PEN 0); the next frame brings the pen back in. It stops
// only after it has once seen a real BTN_TOOL_PEN 0, and no quirk turns it
// off. A hovering pen's device stays silent that long all the time: the heat
// processor gave ~260 reports/s in range, but the device got 156 frames/s
// while hovering (264/s with the tip down, where the pressure changes every
// report). A hover report that repeats the last one (the library has a
// HastaPostProcessingReportManager::GenerateHoverReportFromLastReport: the
// same position, pressure 0, tilt 0) changes no value, and the kernel's input
// core drops unchanged values and then the empty frame. So after every boot
// and every restart of duo2-touchd or duo-wc, until the pen first left for
// real: 120 false proximity out / in pairs in 35 s.
//  - Every frame of a pen device ends with MSC_TIMESTAMP (the input core
//    passes EV_MSC always, so a repeated report is a frame too). libinput
//    switches MSC_TIMESTAMP off in libevdev unless a quirk asks to watch it,
//    so it sees an empty frame: its timer is renewed, nothing goes on.
//  - A pen in range (duo2-touchd's view: until the library says it left, or
//    kPenTimeoutMs without a report) gets a frame with only MSC_TIMESTAMP when
//    nothing went to its device for kKeepAliveMs. No position is repeated or
//    made up; the real proximity-out goes out as before.
// MSC_TIMESTAMP is in microseconds and may wrap (input-event-codes.h).
const uint64_t kKeepAliveMs = 30;

inline bool keepalive_due(bool in_range, uint64_t last_frame_ms, uint64_t now_ms) {
    return in_range && now_ms - last_frame_ms >= kKeepAliveMs;
}

// How long the main loop may wait for input before a keep-alive is due
// (never more than `cap`).
inline int keepalive_wait_ms(bool in_range, uint64_t last_frame_ms, uint64_t now_ms, int cap) {
    if (!in_range) return cap;
    uint64_t since = now_ms - last_frame_ms;
    if (since >= kKeepAliveMs) return 0;
    uint64_t left = kKeepAliveMs - since;
    return left < (uint64_t)cap ? (int)left : cap;
}

inline int32_t msc_timestamp(uint64_t us) { return (int32_t)(uint32_t)us; }

// The library's pen output while in range, for the 30 s statistics: what it
// gives before the kernel drops anything. Per mode (0 hovering, 1 the tip
// down): reports, repeats (the same as the report before: the hover hold),
// heat frames, and between NEW positions: how many came under 1 ms after
// the one before (a burst), gaps over 20 and 50 ms, the longest. No position
// is kept beyond the last report (for the repeat check).
struct Sample {
    uint8_t flags = 0;
    int32_t x = 0, y = 0;
    uint16_t pressure = 0;
    int16_t tilt_x = 0, tilt_y = 0;
    bool same(const Sample &o) const {
        return flags == o.flags && x == o.x && y == o.y && pressure == o.pressure &&
               tilt_x == o.tilt_x && tilt_y == o.tilt_y;
    }
};

struct Timing {
    unsigned long reports[2] = {}, repeats[2] = {}, heat[2] = {}, bursts[2] = {}, gaps20[2] = {},
                  gaps50[2] = {};
    uint64_t max_gap_us[2] = {};
    unsigned long calls = 0, multi_calls = 0, max_per_call = 0, keepalives = 0;
    // The running state (kept across windows).
    bool have = false;
    Sample last;
    int mode = -1;
    uint64_t last_new_us = 0;

    // One SendPenReports call with `count` reports.
    void call(unsigned count) {
        if (!count) return;
        calls++;
        if (count > 1) multi_calls++;
        if (count > max_per_call) max_per_call = count;
    }
    // One report of a pen in range, at now_us.
    void report(const Sample &s, bool tip, uint64_t now_us) {
        int m = tip ? 1 : 0;
        reports[m]++;
        if (have && m == mode && s.same(last)) {
            repeats[m]++;
            return;
        }
        if (have && m == mode) {
            uint64_t gap = now_us - last_new_us;
            if (gap < 1000) bursts[m]++;
            if (gap > 20000) gaps20[m]++;
            if (gap > 50000) gaps50[m]++;
            if (gap > max_gap_us[m]) max_gap_us[m] = gap;
        }
        have = true;
        last = s;
        mode = m;
        last_new_us = now_us;
    }
    // Out of range: the next position starts a new run (the time out of
    // range is no gap).
    void out() {
        have = false;
        mode = -1;
    }
    // A heat frame given to the library; mode -1: the pen is out of range.
    void heat_frame(int m) {
        if (m == 0 || m == 1) heat[m]++;
    }
    void keepalive() { keepalives++; }
    bool any() const { return reports[0] || reports[1] || keepalives; }
    // The log line (without the "last 30 s:" prefix), cut to fit `n`.
    void format(char *buf, size_t n) const {
        static const char *const name[2] = { "hover", "tip" };
        size_t o = 0;
        auto room = [&]() { return o < n ? n - o : 0; };
        auto advance = [&](int k) {
            if (k > 0) o += (size_t)k;
            if (o >= n) o = n ? n - 1 : 0;
        };
        if (n) buf[0] = 0;
        for (int m = 0; m < 2; m++) {
            if (!reports[m] && !heat[m]) continue;
            advance(snprintf(buf + o, room(),
                             "%s%s %lu reports (%lu repeats) from %lu heat frames, new "
                             "positions: %lu under 1 ms apart, %lu gaps over 20 ms, %lu over 50 "
                             "ms, longest %.1f ms",
                             o ? "; " : "", name[m], reports[m], repeats[m], heat[m], bursts[m],
                             gaps20[m], gaps50[m], max_gap_us[m] / 1000.0));
        }
        advance(snprintf(buf + o, room(),
                         "%s%lu library calls (%lu with more than one report, at most %lu), %lu "
                         "keep-alive frames",
                         o ? "; " : "", calls, multi_calls, max_per_call, keepalives));
    }
    // A new window: the counters start again, the running state stays.
    void reset_window() {
        Timing fresh;
        fresh.have = have;
        fresh.last = last;
        fresh.mode = mode;
        fresh.last_new_us = last_new_us;
        *this = fresh;
    }
};

// The pen settings file (/etc/duo2/pen.conf, written by Duo Settings):
// "palm_rejection = both|panel|off", '#' comments. True if a valid line set
// *out (the last one counts).
inline bool conf_palm(const char *text, PalmMode *out) {
    bool found = false;
    while (*text) {
        const char *end = strchr(text, '\n');
        size_t len = end ? (size_t)(end - text) : strlen(text);
        char line[160];
        size_t n = len < sizeof line - 1 ? len : sizeof line - 1;
        memcpy(line, text, n);
        line[n] = 0;
        if (char *hash = strchr(line, '#')) *hash = 0;
        char key[40], value[40];
        PalmMode m;
        if (sscanf(line, " %39[a-z_] = %39s", key, value) == 2 && !strcmp(key, "palm_rejection") &&
            palm_parse(value, &m)) {
            *out = m;
            found = true;
        }
        text += len;
        if (*text == '\n') text++;
    }
    return found;
}

}  // namespace pen
