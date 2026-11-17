// SPDX-License-Identifier: Apache-2.0
// Host test of duo2-touchd's pen rules (pen_rules.h):
//   touch/tests/run.sh
#include <stdio.h>

#include <initializer_list>

#include "../pen_rules.h"

static int failures = 0;
#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            fprintf(stderr, "%s:%d: FAILED: %s\n", __FILE__, __LINE__, #cond);                     \
            failures++;                                                                            \
        }                                                                                          \
    } while (0)

using namespace pen;

static void flags() {
    // Out of range: nothing else counts.
    Tool t = decode(0);
    CHECK(!t.in_range && !t.tip && !t.rubber && !t.barrel);
    t = decode(kTip | kBarrel);                    // no in-range bit
    CHECK(!t.in_range && !t.tip && !t.barrel);
    t = decode(kInRange | kInvalid | kTip);        // invalid: out of range
    CHECK(!t.in_range && !t.tip);
    // Hovering, the tip, the barrel button.
    t = decode(kInRange);
    CHECK(t.in_range && !t.tip && !t.rubber && !t.barrel);
    t = decode(kInRange | kTip);
    CHECK(t.in_range && t.tip && !t.rubber);
    t = decode(kInRange | kBarrel);
    CHECK(t.in_range && !t.tip && t.barrel);
    t = decode(kInRange | kTip | kBarrel);
    CHECK(t.tip && t.barrel);
    // The eraser end: towards the screen (inverted) it hovers as a rubber,
    // touching it is the eraser bit, never the tip bit.
    t = decode(kInRange | kInvert);
    CHECK(t.in_range && t.rubber && !t.tip);
    t = decode(kInRange | kInvert | kTip);
    CHECK(t.rubber && !t.tip);
    t = decode(kInRange | kInvert | kEraser);
    CHECK(t.rubber && t.tip);
    t = decode(kInRange | kEraser);                // eraser without invert: still the rubber
    CHECK(t.rubber && t.tip);
}

static void pressure_and_tilt() {
    // A hover never presses; the tip never goes below the quirk's threshold
    // (AttrPressureRange=2:1: libinput's contact follows the tip bit).
    CHECK(pressure(false, 0) == 0);
    CHECK(pressure(false, 40000) == 0);
    CHECK(pressure(true, 0) == kTipPressureMin);
    CHECK(pressure(true, 1) == kTipPressureMin);
    CHECK(pressure(true, 2) == kTipPressureMin);
    CHECK(pressure(true, 3) == 3);
    CHECK(pressure(true, 16) == 16);               // one of the pen's 4096 levels
    CHECK(pressure(true, 65535) == 65535);
    CHECK(pressure(true, 70000) == 65535);
    // Above both readings of the quirk (2:1 and its swapped 1:2), below 5 %.
    CHECK(kTipPressureMin > 2 && kTipPressureMin < 65535 / 20);
    CHECK(tilt(0) == 0);
    CHECK(tilt(-2000) == -2000);
    CHECK(tilt(9500) == 9000);
    CHECK(tilt(-12000) == -9000);
}

static void to_panel_maps_and_clamps() {
    const double identity[6] = { 1, 0, 0, 0, 1, 0 };
    int x, y;
    // The right panel of the stitched surface: x from its offset.
    to_panel(8935 + 4260, 6000, 8935, 8521, 12020, identity, &x, &y);
    CHECK(x == 4260 && y == 6000);
    // Past the panel (near the hinge, below the bottom): clamped last.
    to_panel(8935 - 200, 12500, 8935, 8521, 12020, identity, &x, &y);
    CHECK(x == 0 && y == 12019);
    to_panel(8935 + 9000, -5, 8935, 8521, 12020, identity, &x, &y);
    CHECK(x == 8520 && y == 0);
    // A calibration that stretches x by 2 % from the hinge side: a point just
    // past the panel's inner edge comes back onto it (clamping first would
    // have lost it).
    const double stretch[6] = { 1.02, 0, -0.01, 0, 1, 0 };
    to_panel(8935 - 40, 100, 8935, 8521, 12020, stretch, &x, &y);
    CHECK(x == 0);
    to_panel(8935 + 85, 100, 8935, 8521, 12020, stretch, &x, &y);
    CHECK(x > 0 && x < 20);
}

static void palm_rules() {
    PalmPen p;                     // the pen never came
    for (PalmMode m : { kPalmOff, kPalmPanel, kPalmBoth }) {
        CHECK(!palm_holds(m, p, 0, 1000));
        CHECK(!palm_holds(m, p, 1, 1000));
    }
    p.panel = 1;                   // in range of the right panel
    CHECK(!palm_holds(kPalmOff, p, 1, 1000));
    CHECK(palm_holds(kPalmBoth, p, 0, 1000));
    CHECK(palm_holds(kPalmBoth, p, 1, 1000));
    CHECK(palm_holds(kPalmPanel, p, 1, 1000));
    CHECK(!palm_holds(kPalmPanel, p, 0, 1000));    // the other panel stays a finger's
    // It left at 5000: held back for the grace time, on its last panel.
    p.panel = -1;
    p.last_panel = 1;
    p.out_ms = 5000;
    CHECK(palm_holds(kPalmBoth, p, 0, 5000 + kPalmGraceMs - 1));
    CHECK(palm_holds(kPalmPanel, p, 1, 5000 + kPalmGraceMs - 1));
    CHECK(!palm_holds(kPalmPanel, p, 0, 5000 + kPalmGraceMs - 1));
    CHECK(!palm_holds(kPalmBoth, p, 0, 5000 + kPalmGraceMs));
    CHECK(!palm_holds(kPalmPanel, p, 1, 5000 + kPalmGraceMs));
    // Contacts already down when the pen comes.
    CHECK(palm_cancels(kPalmBoth, 0, 1) && palm_cancels(kPalmBoth, 0, 0));
    CHECK(palm_cancels(kPalmPanel, 0, 0) && !palm_cancels(kPalmPanel, 0, 1));
    CHECK(!palm_cancels(kPalmOff, 0, 0));
}

static void settings() {
    PalmMode m = kPalmOff;
    CHECK(palm_parse("both", &m) && m == kPalmBoth);
    CHECK(palm_parse("on", &m) && m == kPalmBoth);
    CHECK(palm_parse("panel", &m) && m == kPalmPanel);
    CHECK(palm_parse("off", &m) && m == kPalmOff);
    CHECK(!palm_parse("maybe", &m) && m == kPalmOff);
    CHECK(!strcmp(palm_name(kPalmPanel), "panel"));
    m = kPalmBoth;
    CHECK(conf_palm("# Duo Settings > Pen\npalm_rejection = panel\n", &m) && m == kPalmPanel);
    CHECK(conf_palm("palm_rejection=off", &m) && m == kPalmOff);
    CHECK(conf_palm("  palm_rejection = both   # the default\n", &m) && m == kPalmBoth);
    m = kPalmPanel;
    CHECK(!conf_palm("", &m) && m == kPalmPanel);
    CHECK(!conf_palm("# palm_rejection = off\n", &m) && m == kPalmPanel);
    CHECK(!conf_palm("palm_rejection = sometimes\nother = 1\n", &m) && m == kPalmPanel);
    CHECK(conf_palm("palm_rejection = off\npalm_rejection = panel\n", &m) &&
          m == kPalmPanel);   // the last one
}

// libinput forces a proximity-out after 50 ms without a frame: a pen in
// range is never left silent that long, and only a pen in range is kept.
static void keepalive() {
    CHECK(kKeepAliveMs < 50 && kKeepAliveMs >= 10);
    CHECK(!keepalive_due(false, 1000, 5000));          // out of range: never
    CHECK(!keepalive_due(true, 1000, 1000));
    CHECK(!keepalive_due(true, 1000, 1000 + kKeepAliveMs - 1));
    CHECK(keepalive_due(true, 1000, 1000 + kKeepAliveMs));
    CHECK(keepalive_due(true, 1000, 1400));
    // The loop's wait: the time left to the next one, at most the cap.
    CHECK(keepalive_wait_ms(false, 0, 99999, 1000) == 1000);
    CHECK(keepalive_wait_ms(true, 1000, 1000, 1000) == (int)kKeepAliveMs);
    CHECK(keepalive_wait_ms(true, 1000, 1010, 1000) == (int)kKeepAliveMs - 10);
    CHECK(keepalive_wait_ms(true, 1000, 1010, 5) == 5);
    CHECK(keepalive_wait_ms(true, 1000, 1000 + kKeepAliveMs, 1000) == 0);
    CHECK(keepalive_wait_ms(true, 1000, 2000, 1000) == 0);
    // A loop that sends one whenever it is due and waits as told: no gap
    // between frames ever reaches libinput's 50 ms, with or without reports.
    uint64_t last = 0, worst = 0;
    for (uint64_t now = 0; now < 2000;) {
        if (keepalive_due(true, last, now)) {
            if (now - last > worst) worst = now - last;
            last = now;
        }
        if (now % 7 == 0 && now < 500) last = now;     // reports, then silence
        int w = keepalive_wait_ms(true, last, now, 1000);
        now += w > 0 ? (uint64_t)w : 1;
    }
    CHECK(worst <= kKeepAliveMs && worst < 50);
    // MSC_TIMESTAMP: microseconds, wrapping at 32 bits.
    CHECK(msc_timestamp(0) == 0);
    CHECK(msc_timestamp(1234567) == 1234567);
    CHECK(msc_timestamp(0xffffffffull) == -1);
    CHECK(msc_timestamp(0x100000005ull) == 5);
}

static Sample at(int x, int y, uint8_t flags = kInRange, uint16_t pressure = 0) {
    Sample s;
    s.flags = flags;
    s.x = x;
    s.y = y;
    s.pressure = pressure;
    return s;
}

static void timing() {
    Timing t;
    CHECK(!t.any());
    // Hover: a new position every 15 ms, each repeated once (the library's
    // hold), then a still pen for 400 ms (repeats only), then moving again.
    uint64_t us = 1000000;
    for (int i = 0; i < 10; i++) {
        t.report(at(100 + i, 200), false, us);
        t.report(at(100 + i, 200), false, us + 7500);   // the same again
        us += 15000;
    }
    CHECK(t.reports[0] == 20 && t.repeats[0] == 10);
    CHECK(t.gaps20[0] == 0 && t.gaps50[0] == 0 && t.bursts[0] == 0);
    CHECK(t.max_gap_us[0] == 15000);
    for (int i = 0; i < 50; i++) t.report(at(109, 200), false, us + i * 8000);   // still
    CHECK(t.repeats[0] == 60 && t.gaps20[0] == 0);
    us += 400000;
    t.report(at(110, 200), false, us);                 // moves: one gap of 415 ms
    CHECK(t.gaps20[0] == 1 && t.gaps50[0] == 1 && t.max_gap_us[0] == 415000);
    t.report(at(111, 200), false, us + 50);            // a burst: 0.05 ms later
    CHECK(t.bursts[0] == 1);
    // A change of any value is a new position (here the pressure, tip down).
    t.report(at(111, 200, kInRange | kTip, 100), true, us + 1000);
    t.report(at(111, 200, kInRange | kTip, 120), true, us + 8500);
    CHECK(t.reports[1] == 2 && t.repeats[1] == 0 && t.gaps20[1] == 0);
    CHECK(t.max_gap_us[1] == 7500);
    // The mode changed: the hover run starts again, no gap across the tip.
    t.report(at(111, 200), false, us + 100000);
    CHECK(t.gaps20[0] == 1);
    t.report(at(111, 200), false, us + 107500);
    CHECK(t.repeats[0] == 61);
    // Out of range: the time away is no gap, and the first report back is
    // no repeat even at the same spot.
    t.out();
    t.report(at(111, 200), false, us + 5000000);
    CHECK(t.repeats[0] == 61 && t.gaps20[0] == 1 && t.gaps50[0] == 1);
    // Library calls, heat frames, keep-alives.
    t.call(0);
    t.call(1);
    t.call(3);
    t.call(1);
    CHECK(t.calls == 3 && t.multi_calls == 1 && t.max_per_call == 3);
    t.heat_frame(-1);
    t.heat_frame(0);
    t.heat_frame(0);
    t.heat_frame(1);
    CHECK(t.heat[0] == 2 && t.heat[1] == 1);
    t.keepalive();
    CHECK(t.keepalives == 1 && t.any());
    char line[640];
    t.format(line, sizeof line);
    CHECK(strstr(line, "hover 75 reports (61 repeats) from 2 heat frames") != nullptr);
    CHECK(strstr(line, "1 gaps over 20 ms, 1 over 50 ms, longest 415.0 ms") != nullptr);
    CHECK(strstr(line, "tip 2 reports (0 repeats) from 1 heat frames") != nullptr);
    CHECK(strstr(line, "3 library calls (1 with more than one report, at most 3), 1 keep-alive") !=
          nullptr);
    // A short buffer is cut, never overrun.
    char small[24];
    memset(small, 'x', sizeof small);
    t.format(small, 16);
    CHECK(strlen(small) == 15 && small[16] == 'x');
    // A new window: counters again from 0, the running state kept (a repeat
    // of the last report is still a repeat).
    t.reset_window();
    CHECK(!t.any() && t.reports[0] == 0 && t.max_gap_us[0] == 0);
    t.report(at(111, 200), false, us + 5007500);
    CHECK(t.reports[0] == 1 && t.repeats[0] == 1);
    t.format(line, sizeof line);
    CHECK(strstr(line, "hover 1 reports (1 repeats)") != nullptr);
}

int main() {
    flags();
    pressure_and_tilt();
    to_panel_maps_and_clamps();
    palm_rules();
    settings();
    keepalive();
    timing();
    if (failures) {
        fprintf(stderr, "%d failure(s)\n", failures);
        return 1;
    }
    printf("pen rules: all tests passed\n");
    return 0;
}
