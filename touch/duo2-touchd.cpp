// SPDX-License-Identifier: Apache-2.0
// From luma-duo2 (none24smu, Apache-2.0); modified for duo2-os (see git log).
// duo2-touchd: Surface Duo 2 touch, processed by Microsoft's own heat library.
//
// The Duo 2's digitizer (HID 045E:0C1D on spi0.0) streams raw capacitive heat
// frames, one HID collection per panel. Android turns them into contacts with
// /vendor/lib64/c1/libsurfacetouch_c1.so -- the Duo 2 ("Zeta") build of
// Microsoft's heat processor, with its tuning, panel alignment and hinge
// handling. This daemon loads that same library from the stock vendor image
// and drives it exactly as the stock vendor.surface.touchpen@1.3-service does
// (docs/hardware/touch.md), then injects the
// contacts through uinput as a standard Linux multitouch screen.
//
// It is an Android (bionic) program: it runs under the stock linker64 with
// the stock runtime libraries, because the library it hosts is one.
//
// Two modes (docs/hardware/touch.md):
//  - stitched: the stock service's own sequence. Every message the library
//    sends to the firmware is a SET_FEATURE (HeatService::SendMessageToTouchFw
//    -> HIDDevice::setFeatureReport), stitching is on from the start, and the
//    library is kept told what Android tells it: which panels are lit, the
//    hinge angle, the orientation, the handedness (from duo-posed, through
//    --pose-file). With both panels lit and the hinge under 235 degrees the
//    library stitches both panels into one surface (its topology 2).
//  - split: the bring-up sequence (stitching off, the firmware put into heat
//    mode by hand, library messages written as output reports). The library
//    then works per panel and its output is stretched and stops short of
//    the outer edges. Kept as the last-resort fallback: a stitched daemon that
//    gets the digitizer's single-point report (not in heat mode) exits and is
//    restarted stitched (a re-initialisation); after 3 of those within 10
//    minutes it writes /run/duo2-touchd.fallback and, restarted, runs split
//    until that file is removed (or the next boot).
// The mode comes from --mode, else /etc/duo2/touch-mode ("stitched" or
// "split"), else split.
//
// System sleep (digitizer_power.h): the system-sleep hook
// (/usr/lib/systemd/system-sleep/duo2-touchd) sends SIGURG before a suspend and
// waits until kPowerState says "off": the daemon has lifted every contact and
// closed hidraw, and spi-hid has powered the digitizer down. After the resume
// it sends SIGWINCH: the daemon opens hidraw again, and at "ready" tells the
// library OnDeviceReset (and puts the firmware in heat mode by hand in the
// split mode), as the stock HAL does when the screens come on. The touch
// devices, the library and its pose stay as they are. While the system is
// awake the digitizer stays on, dark panels included (a touch wakes them).
//
// Usage: duo2-touchd [--hidraw /dev/hidrawN] [--lib PATH] [--state DIR]
//                    [--mode stitched|split] [--pose-file FILE]
//                    [--calibration FILE] [--log-contacts] [--hinge DEG] [--dump]
//                    [--no-palm-rejection] [--pen-conf FILE]

#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/hidraw.h>
#include <linux/uinput.h>
#include <math.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include <set>
#include <vector>

#include "autobond.h"
#include "devgate.h"
#include "digitizer_power.h"
#include "pen_rules.h"

namespace {

bool g_dump = false;
bool g_force_mode = true;
bool g_log_contacts = false;   // raw contact log for calibration (--log-contacts, "log on"):
                               // only in Developer mode, with its switch on (devgate.h)

enum DaemonMode { kModeSplit, kModeStitched };
DaemonMode g_mode = kModeSplit;
// Library messages to the firmware as SET_FEATURE, as the stock service
// sends them (stitched mode), or written as output reports (split mode).
bool g_fw_feature = false;

void logf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
}

uint64_t now_ms() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

uint64_t now_us() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}

// CLOCK_BOOTTIME: counts the time the system slept (CLOCK_MONOTONIC does not).
uint64_t boot_ms() {
    struct timespec ts;
    clock_gettime(CLOCK_BOOTTIME, &ts);
    return (uint64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

PowerClock power_clock() { return PowerClock{ now_ms(), boot_ms() }; }

// ---------------------------------------------------------------------------
// The library's view of a panel. The stock service keeps two of these, one
// per heat collection: { region, kind, collection } with region 1 / kind 2 for
// the collection holding report 0x0A and region 2 / kind 1 for the one holding
// report 0x3C. The last field identifies the collection.
struct EosHeatDevice {
    uint8_t region;
    uint8_t pad[3];
    uint32_t kind;
    const void *collection;
};
static_assert(sizeof(EosHeatDevice) == 16, "EosHeatDevice layout");

// One contact as the library reports it: 31 bytes, packed.
struct __attribute__((packed)) TouchReport {
    uint16_t unknown0;
    uint16_t contact_id;     // 0xfe: not a contact
    uint8_t state;           // 7: touching; anything else: lifted
    int32_t x, y;
    int32_t width, height;
    uint16_t orientation;    // degrees, 0..180
    // HeatAdaptorEos::SendTouchReports copies the first 8 bytes of the
    // EosHeatDevice whose heat frame produced the contact here: the region
    // (1 = the collection with report 0x0A, 2 = the one with 0x3C), 3 bytes of
    // padding and the kind. So every contact names its digitizer side.
    uint8_t region;
    uint8_t rest[7];
};
static_assert(sizeof(TouchReport) == 31, "TouchReport layout");

// The pen as the library reports it (HeatAdaptorEos::SendPenReports copies
// its HeatPenReport into this; the stock service's InputEvdev::PenReports
// reads it): 31 bytes, packed, on the same surface as the contacts.
struct __attribute__((packed)) PenReport {
    uint16_t unknown0;
    // InputEvdev::PenReports: (flags & 6) == 4 is a pen in range (bit 2 set,
    // bit 1 clear), anything else takes it out of range; bit 0 the tip
    // touching, bit 3 the eraser end towards the screen (inverted), bit 4 the
    // eraser touching, bit 5 the barrel button. The stock service makes the
    // inverted pen MT_TOOL 15 + BTN_TOOL_RUBBER, the barrel BTN_STYLUS.
    uint8_t flags;
    int32_t x, y;
    uint16_t pressure;       // 0..65535 (the pen's 4096 levels x 16, PreparePenReport)
    // 0.01 degree (the stock service's azimuth: atan2(-sin tx, sin ty)).
    // PreparePenReport makes them from the pen tracker's tilt (from the
    // vertical) and azimuth: atan(tan(tilt) * sin / cos(360 - azimuth)). Both
    // 0 while the pen hovers: HastaPostProcessingReportManager::
    // GeneratePenReport keeps the tilt only with the tip down (measured
    // 2026-11-16: 0 in hover, up to ~40 degrees writing).
    int16_t tilt_x, tilt_y;
    uint32_t unknown17;
    uint16_t unknown21;
    uint8_t region;          // as in TouchReport: the digitizer side
    uint8_t rest[7];
};
static_assert(sizeof(PenReport) == 31, "PenReport layout");
// The flags: pen::Flags (pen_rules.h).
const unsigned kPenTip = pen::kTip, kPenInRange = pen::kInRange, kPenInvert = pen::kInvert,
               kPenEraser = pen::kEraser, kPenBarrel = pen::kBarrel;

struct AxisRange { int32_t x0, x1, y0, y1; };

// SurfaceHeatProcessorEos's virtual methods, by vtable slot.
enum ProcessorSlot {
    kInitPenTouchMw = 2,
    kProcessIncomingReport = 3,
    kPostDispatchProcessing = 4,
    kOnDeviceAttached = 5,
    kOnDeviceReset = 7,
    kOnHingeAngleChange = 8,
    kOnStitchingModeChange = 9,
    kOnDisplayChange = 10,
    kOnRightOrLeftHandModeChange = 11,
    kGetAxisRange = 12,
    kGetMaxConcurrentPenTouchSlots = 13,
    kOnDeviceRotationChange = 14,
};

struct Processor { void **vtable; };

template <typename Fn>
Fn slot(Processor *p, int index) {
    return reinterpret_cast<Fn>(p->vtable[index]);
}

// ---------------------------------------------------------------------------
// uinput: one touchscreen per panel.
//
// The library reports contacts on one stitched surface spanning both panels
// (x across left panel, hinge gap, right panel), which Android's input stack
// takes as is. Linux compositors map each touch device to one output, so the
// surface is split back into its panels -- at the boundaries the digitizer's
// own metadata gives (each panel's width) -- and each half becomes its own
// device, named for its panel; udev tags it with that panel's output
// (WL_OUTPUT, see config/mobile/duo2-physical's udev rule).
//
// Where a panel's contacts land depends on the library's topology
// (PostureManager::UpdateTopology): stitched with both panels lit (2), the
// two panels are one heat map and the contacts are on the stock 0..17455
// surface (left panel, 4.2 mm hinge gap, right panel; what Android's 2754 px
// logical display, 1344 + 66 + 1344, maps linearly); otherwise each panel is
// processed alone (topology 0 / 1, per digitizer side) and the right panel's
// contacts carry that side's offset (HeatAdaptorEos::
// CalculatePositionOffsetForDualScreen; its heartbeat contact shows it).
// Each geometry has its own calibration.
enum Geom { kGeomSplit = 0, kGeomStitched = 1, kGeomSingle = 2, kGeoms = 3 };
const char *const kGeomName[kGeoms] = { "split", "stitched", "single" };
const char *const kCalPrefix[kGeoms] = { "", "stitched-", "single-" };

struct Panel {
    explicit Panel(const char *n, const char *k, const char *pn) : name(n), key(k), pen_name(pn) {}
    const char *name;
    const char *key;                 // "left" / "right" in the calibration file
    const char *pen_name;            // this panel's pen device (pen_create)
    int fd = -1;
    int pen_fd = -1;
    int x_offset = 0, width = 0, height = 0;
    // Correction of the heat processor's output for this panel, per geometry,
    // applied in the panel's normalised coordinates BEFORE clamping (the same
    // form as a libinput calibration matrix): x' = m[0] x + m[1] y + m[2],
    // y' = m[3] x + m[4] y + m[5], x and y in 0..1 of the panel.
    double m[kGeoms][6] = { { 1, 0, 0, 0, 1, 0 }, { 1, 0, 0, 0, 1, 0 }, { 1, 0, 0, 0, 1, 0 } };
    std::vector<int> slot_contact;   // contact id per slot, -1 if free
    bool dirty = false;
};
Panel g_panels[2] = { Panel("Surface Duo 2 Touchscreen Left", "left", "Surface Duo 2 Pen Left"),
                      Panel("Surface Duo 2 Touchscreen Right", "right",
                            "Surface Duo 2 Pen Right") };
int g_slots = 10;
int g_contact_panel[0x10000];   // which panel a live contact started on
// How a live contact is mapped: chosen at touch-down, kept until the lift.
struct ContactMap {
    int x_offset;
    Geom geom;
};
ContactMap g_contact_map[0x10000];
unsigned long g_contact_reports = 0;
uint64_t g_last_contact_ms = 0;  // the last report of a real contact (not a heartbeat)
// Which panel each digitizer region is. The heat processor adds the right
// screen's offset to the contacts of the kind-2 device (region 1, report
// 0x0A: HeatAdaptorEos::CalculatePositionOffsetForDualScreen), so region 1 is
// the right panel and region 2 the left one.
int panel_of_region(uint8_t region) { return region == 1 ? 1 : region == 2 ? 0 : -1; }
// The x of each region's heartbeat contact (contact id 0xfe): the offset the
// library adds to that side's contacts. Measured: stitched 0 / 0; per panel
// 8518 + the gap for the right side (8518 in the split mode, where the
// library never sets the gap; 8938 with its 4.2 mm gap once it has been
// stitched), 0 for the left.
// The library's current geometry (set by the stitched mode, see lib_geometry).
Geom g_geom = kGeomSplit;
int g_heartbeat_x[3] = { -1, -1, -1 };
int g_heartbeat_geom[3] = { -1, -1, -1 };   // the geometry it was seen in
const int kDuo2RightSideOffset = 8518;   // the right side's offset without the gap
uint32_t lib_gap();   // the library's display gap (0.01 mm), 0 if not known
int right_side_offset() {
    if (g_heartbeat_x[1] > 0 && g_heartbeat_geom[1] == g_geom) return g_heartbeat_x[1];
    return kDuo2RightSideOffset + (int)lib_gap();
}

// Per contact, for the raw log: where it went down and how far it moved.
struct ContactTrace {
    int x0, y0, xmin, xmax, ymin, ymax;
    uint8_t region;
};
ContactTrace g_trace[0x10000];

// Calibration file: lines "[stitched-|single-]left|right a b c d e f" (see
// Panel::m; no prefix = the split mode); '#' comments. A geometry without
// lines gets no correction.
void calibration_load(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) {
        logf("calibration: %s: %s (no correction)", path, strerror(errno));
        return;
    }
    char line[256];
    while (fgets(line, sizeof line, f)) {
        char key[32];
        double v[6];
        if (line[0] == '#' || sscanf(line, "%31s %lf %lf %lf %lf %lf %lf", key, &v[0], &v[1], &v[2],
                                     &v[3], &v[4], &v[5]) != 7)
            continue;
        for (Panel &p : g_panels)
            for (int g = 0; g < kGeoms; g++) {
                char want[32];
                snprintf(want, sizeof want, "%s%s", kCalPrefix[g], p.key);
                if (strcmp(key, want)) continue;
                memcpy(p.m[g], v, sizeof v);
                logf("calibration %s: %.5f %.5f %.5f %.5f %.5f %.5f", want, v[0], v[1], v[2], v[3],
                     v[4], v[5]);
            }
    }
    fclose(f);
}

void emit(Panel &p, int type, int code, int value) {
    struct input_event ev = {};
    ev.type = type;
    ev.code = code;
    ev.value = value;
    if (write(p.fd, &ev, sizeof ev) != (ssize_t)sizeof ev)
        logf("uinput write: %s", strerror(errno));
}

void abs_setup(int fd, int code, int min, int max) {
    struct uinput_abs_setup a = {};
    a.code = code;
    a.absinfo.minimum = min;
    a.absinfo.maximum = max;
    if (ioctl(fd, UI_ABS_SETUP, &a) < 0) logf("UI_ABS_SETUP %d: %s", code, strerror(errno));
}

bool panel_create(Panel &p, int width, int height, int slots) {
    p.fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC);
    if (p.fd < 0) {
        logf("open /dev/uinput: %s", strerror(errno));
        return false;
    }
    p.width = width;
    p.height = height;
    p.slot_contact.assign(slots, -1);
    ioctl(p.fd, UI_SET_EVBIT, EV_KEY);
    ioctl(p.fd, UI_SET_EVBIT, EV_ABS);
    ioctl(p.fd, UI_SET_KEYBIT, BTN_TOUCH);
    ioctl(p.fd, UI_SET_PROPBIT, INPUT_PROP_DIRECT);
    // Axes as the stock service configures them (InputEvdev::Attach).
    int diag = 0;
    for (long d2 = (long)width * width + (long)height * height;
         (long)(diag + 1) * (diag + 1) <= d2;)
        diag++;
    abs_setup(p.fd, ABS_MT_SLOT, 0, slots - 1);
    abs_setup(p.fd, ABS_MT_TRACKING_ID, 0, 0xffff);
    abs_setup(p.fd, ABS_MT_POSITION_X, 0, width - 1);
    abs_setup(p.fd, ABS_MT_POSITION_Y, 0, height - 1);
    abs_setup(p.fd, ABS_MT_TOUCH_MAJOR, 0, diag);
    abs_setup(p.fd, ABS_MT_TOUCH_MINOR, 0, diag);
    abs_setup(p.fd, ABS_MT_ORIENTATION, -90, 90);
    abs_setup(p.fd, ABS_MT_PRESSURE, 0, 0xffff);
    abs_setup(p.fd, ABS_MT_TOOL_TYPE, 0, MT_TOOL_MAX);
    abs_setup(p.fd, ABS_X, 0, width - 1);
    abs_setup(p.fd, ABS_Y, 0, height - 1);
    struct uinput_setup s = {};
    s.id.bustype = BUS_VIRTUAL;
    s.id.vendor = 0x045e;
    s.id.product = 0x0c1d;
    s.id.version = 1;
    snprintf(s.name, sizeof s.name, "%s", p.name);
    if (ioctl(p.fd, UI_DEV_SETUP, &s) < 0 || ioctl(p.fd, UI_DEV_CREATE) < 0) {
        logf("uinput create %s: %s", p.name, strerror(errno));
        return false;
    }
    return true;
}

// The pen: one tablet device per panel, beside its touchscreen, on the same
// panel coordinates (udev ties it to the panel's output, as the touchscreen).
// What libinput needs to take it as a tablet: ABS_X/ABS_Y with a resolution,
// BTN_TOOL_PEN (evdev-tablet.c, tablet_reject_device). The library's units
// are 0.01 mm, so 100 per mm; tilt in 0.01 degree, so 18000/pi per radian
// (libinput reads a tilt resolution as units per radian). The pressure range
// is the library's own (PreparePenReport: 0..65535); libinput's tip threshold
// on it is the quirk's (pen_rules.h), and its smoothing is off there too (the
// heat processor filters the pen itself). No ABS_DISTANCE: the library
// reports none. MSC_TIMESTAMP on every frame and a timestamp-only frame while
// the pen is in range and silent keep libinput from forcing a proximity-out
// (pen_rules.h, keepalive_due).
const int kPenTiltMax = pen::kTiltMax;
const int kPenTiltRes = 5730;   // 0.01 degree per radian: 18000 / pi

void abs_setup_res(int fd, int code, int min, int max, int res) {
    struct uinput_abs_setup a = {};
    a.code = code;
    a.absinfo.minimum = min;
    a.absinfo.maximum = max;
    a.absinfo.resolution = res;
    if (ioctl(fd, UI_ABS_SETUP, &a) < 0) logf("UI_ABS_SETUP %d: %s", code, strerror(errno));
}

bool pen_create(Panel &p) {
    p.pen_fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC);
    if (p.pen_fd < 0) {
        logf("open /dev/uinput: %s", strerror(errno));
        return false;
    }
    ioctl(p.pen_fd, UI_SET_EVBIT, EV_KEY);
    ioctl(p.pen_fd, UI_SET_EVBIT, EV_ABS);
    ioctl(p.pen_fd, UI_SET_EVBIT, EV_MSC);
    ioctl(p.pen_fd, UI_SET_MSCBIT, MSC_TIMESTAMP);
    for (int key : { BTN_TOOL_PEN, BTN_TOOL_RUBBER, BTN_TOUCH, BTN_STYLUS })
        ioctl(p.pen_fd, UI_SET_KEYBIT, key);
    ioctl(p.pen_fd, UI_SET_PROPBIT, INPUT_PROP_DIRECT);
    abs_setup_res(p.pen_fd, ABS_X, 0, p.width - 1, 100);
    abs_setup_res(p.pen_fd, ABS_Y, 0, p.height - 1, 100);
    abs_setup_res(p.pen_fd, ABS_PRESSURE, 0, 0xffff, 0);
    abs_setup_res(p.pen_fd, ABS_TILT_X, -kPenTiltMax, kPenTiltMax, kPenTiltRes);
    abs_setup_res(p.pen_fd, ABS_TILT_Y, -kPenTiltMax, kPenTiltMax, kPenTiltRes);
    struct uinput_setup s = {};
    s.id.bustype = BUS_VIRTUAL;
    s.id.vendor = 0x045e;
    s.id.product = 0x0c1d;
    s.id.version = 1;
    snprintf(s.name, sizeof s.name, "%s", p.pen_name);
    if (ioctl(p.pen_fd, UI_DEV_SETUP, &s) < 0 || ioctl(p.pen_fd, UI_DEV_CREATE) < 0) {
        logf("uinput create %s: %s", p.pen_name, strerror(errno));
        close(p.pen_fd);
        p.pen_fd = -1;
        return false;
    }
    return true;
}

void pen_out(const char *why);

// The library's stitched range, split at the panels' own width.
bool panels_create(const AxisRange &r, int panel_width, int slots) {
    int total = r.x1 - r.x0 + 1, height = r.y1 - r.y0 + 1;
    if (panel_width <= 0 || panel_width * 2 > total) panel_width = total / 2;
    g_slots = slots;
    for (int &c : g_contact_panel) c = -1;
    g_panels[0].x_offset = r.x0;
    g_panels[1].x_offset = r.x0 + total - panel_width;
    for (Panel &p : g_panels)
        if (!panel_create(p, panel_width, height, slots)) return false;
    // A pen device that cannot be made costs the pen, not the touch.
    for (Panel &p : g_panels) pen_create(p);
    logf("touchscreens: %d x %d each, right panel from x %d (stitched range %d..%d, %d slots); "
         "pens: %s",
         panel_width, height, g_panels[1].x_offset, r.x0, r.x1, slots,
         g_panels[0].pen_fd >= 0 && g_panels[1].pen_fd >= 0 ? "both" : "MISSING");
    return true;
}

// Lift every contact still down before the devices go away: a compositor that
// sees a touchscreen vanish mid-touch may never end the touch sequence, and
// the client holding it (Phosh's lock screen carousel, mid-swipe) stays stuck.
bool g_palm[0x10000];   // a contact held back as a palm (pen in range), until it lifts

void panels_lift_all() {
    pen_out("lift all");
    for (bool &palm : g_palm) palm = false;
    for (Panel &p : g_panels) {
        if (p.fd < 0) continue;
        bool any = false;
        for (int k = 0; k < (int)p.slot_contact.size(); k++) {
            if (p.slot_contact[k] < 0) continue;
            p.slot_contact[k] = -1;
            emit(p, EV_ABS, ABS_MT_SLOT, k);
            emit(p, EV_ABS, ABS_MT_TRACKING_ID, -1);
            any = true;
        }
        if (any) {
            emit(p, EV_KEY, BTN_TOUCH, 0);
            emit(p, EV_SYN, SYN_REPORT, 0);
        }
    }
    for (int &c : g_contact_panel) c = -1;
}

void panels_destroy() {
    panels_lift_all();
    usleep(50 * 1000);   // let the compositor read the lifts first
    for (Panel &p : g_panels) {
        if (p.fd >= 0) {
            ioctl(p.fd, UI_DEV_DESTROY);
            close(p.fd);
            p.fd = -1;
        }
        if (p.pen_fd >= 0) {
            ioctl(p.pen_fd, UI_DEV_DESTROY);
            close(p.pen_fd);
            p.pen_fd = -1;
        }
    }
}

// Contacts into evdev, as InputEvdev::TouchReports does, each on the panel
// whose digitizer side produced it (a finger that slides into the hinge gap
// stays put). The stitched x is turned into the panel's own coordinates, the
// panel's calibration is applied, and only then is it clamped to the panel:
// the heat processor's output runs past the panel near the hinge, and clamping
// first would make the last millimetres there unreachable (pen::to_panel).

// The panel a new contact belongs to, and how its x is made local, in the
// current geometry.
int contact_owner(const TouchReport &t, ContactMap &map) {
    map.geom = g_geom;
    int mid = g_panels[1].x_offset -
              (g_panels[1].x_offset - g_panels[0].x_offset - g_panels[0].width) / 2;
    int by_x = t.x >= mid;
    if (g_geom == kGeomStitched) {
        // One stitched surface: the contact is wherever its x is. (The region
        // byte names the side whose frame completed the stitched cycle.)
        map.x_offset = g_panels[by_x].x_offset;
        return by_x;
    }
    int owner = panel_of_region(t.region);
    if (g_geom == kGeomSingle) {
        // Per panel: the region is the panel; the right side's contacts carry
        // the offset its heartbeat shows.
        if (owner < 0) owner = t.x >= kDuo2RightSideOffset;
        map.x_offset = owner == 0 ? 0 : right_side_offset();
        return owner;
    }
    // Split: the region decides near the hinge, where the two panels' outputs
    // overlap. Far from it, x and region must agree; if they do not, the
    // region byte is not what it is taken for: trust x, and say so.
    int far = g_panels[0].width / 4;
    if (owner < 0 || (owner == 1 && t.x < g_panels[1].x_offset - far) ||
        (owner == 0 && t.x > g_panels[0].x_offset + g_panels[0].width + far)) {
        static unsigned warned = 0;
        if (warned++ < 10)
            logf("contact %u: region %u does not fit x %d, using the x split", t.contact_id,
                 t.region, t.x);
        owner = by_x;
    }
    map.x_offset = g_panels[owner].x_offset;
    return owner;
}

// Palm rejection (pen_rules.h): both panels, the pen's panel, or off. From
// the pen settings file (kPenConf, Duo Settings > Pen), read again when it
// changes; "palm ..." on the control FIFO holds until then;
// --no-palm-rejection holds always.
pen::PalmMode g_palm_mode = pen::kPalmBoth;
bool g_palm_forced_off = false;
pen::PalmPen g_palm_pen;
unsigned long g_palm_contacts = 0;
void palm_lift(int id);

void touch_reports(const TouchReport *reports, unsigned count) {
    g_contact_reports += count;
    for (unsigned i = 0; i < count; i++) {
        const TouchReport &t = reports[i];
        if (g_dump)
            logf("contact id=%u state=%u region=%u x=%d y=%d w=%d h=%d o=%u", t.contact_id, t.state,
                 t.region, t.x, t.y, t.width, t.height, t.orientation);
        if (t.contact_id == 0xfe) {
            // A heartbeat: its x is the offset of that side's contacts.
            if (t.region <= 2 &&
                (g_heartbeat_x[t.region] != t.x || g_heartbeat_geom[t.region] != g_geom)) {
                if (g_heartbeat_x[t.region] != t.x)
                    logf("heartbeat contact of region %u at x %d (%s geometry)", t.region, t.x,
                         kGeomName[g_geom]);
                g_heartbeat_x[t.region] = t.x;
                g_heartbeat_geom[t.region] = g_geom;
            }
            continue;
        }
        g_last_contact_ms = now_ms();
        if (g_palm[t.contact_id]) {   // held back as a palm: nothing of it goes out
            if (t.state != 7) palm_lift(t.contact_id);
            continue;
        }
        int &owner = g_contact_panel[t.contact_id];
        ContactTrace &tr = g_trace[t.contact_id];
        ContactMap &map = g_contact_map[t.contact_id];
        if (owner < 0 && t.state == 7) {
            ContactMap new_map;
            int panel = contact_owner(t, new_map);
            if (pen::palm_holds(g_palm_mode, g_palm_pen, panel, now_ms())) {
                // Palm rejection: a contact that starts while the pen is in
                // range (or has just left it) is the hand holding the pen.
                g_palm[t.contact_id] = true;
                g_palm_contacts++;
                if (g_log_contacts)
                    logf("touch down id=%u on the %s panel held back: the pen is in range (palm)",
                         t.contact_id, g_panels[panel].key);
                continue;
            }
            owner = panel;
            map = new_map;
            tr = { t.x, t.y, t.x, t.x, t.y, t.y, t.region };
        }
        if (owner < 0) continue;
        Panel &p = g_panels[owner];
        const double *m = p.m[map.geom];
        int s = -1, free_slot = -1;
        for (int k = 0; k < g_slots; k++) {
            if (p.slot_contact[k] == t.contact_id) {
                s = k;
                break;
            }
            if (free_slot < 0 && p.slot_contact[k] < 0) free_slot = k;
        }
        p.dirty = true;
        if (t.state == 7) {
            if (s < 0) s = free_slot;
            if (s < 0) {
                logf("%s: all touch slots are taken", p.name);
                continue;
            }
            int x, y;
            pen::to_panel(t.x, t.y, map.x_offset, p.width, p.height, m, &x, &y);
            if (g_log_contacts) {
                if (p.slot_contact[s] != t.contact_id)
                    logf(
                        "touch down id=%u region=%u panel=%s raw=%d,%d local=%d,%d geom=%s xoff=%d",
                        t.contact_id, t.region, p.key, t.x, t.y, x, y, kGeomName[map.geom],
                        map.x_offset);
                tr.xmin = t.x < tr.xmin ? t.x : tr.xmin;
                tr.xmax = t.x > tr.xmax ? t.x : tr.xmax;
                tr.ymin = t.y < tr.ymin ? t.y : tr.ymin;
                tr.ymax = t.y > tr.ymax ? t.y : tr.ymax;
            }
            bool new_slot = p.slot_contact[s] != t.contact_id;
            p.slot_contact[s] = t.contact_id;
            emit(p, EV_ABS, ABS_MT_SLOT, s);
            // A new touch: the tool type first, so that libinput, which reads
            // the slot's tool type when the tracking id comes, does not take a
            // finger in a slot that held a palm for a palm.
            if (new_slot) emit(p, EV_ABS, ABS_MT_TOOL_TYPE, MT_TOOL_FINGER);
            emit(p, EV_ABS, ABS_MT_TRACKING_ID, t.contact_id);
            emit(p, EV_ABS, ABS_MT_POSITION_X, x);
            emit(p, EV_ABS, ABS_MT_POSITION_Y, y);
            emit(p, EV_ABS, ABS_MT_TOOL_TYPE, MT_TOOL_FINGER);
            emit(p, EV_ABS, ABS_MT_PRESSURE, 0xffff);
            emit(p, EV_ABS, ABS_MT_TOUCH_MAJOR, t.width > t.height ? t.width : t.height);
            emit(p, EV_ABS, ABS_MT_TOUCH_MINOR, t.width > t.height ? t.height : t.width);
            emit(p, EV_ABS, ABS_MT_ORIENTATION,
                 t.orientation > 90 ? t.orientation - 180 : t.orientation);
            if (s == 0) {   // single-touch emulation follows slot 0
                emit(p, EV_ABS, ABS_X, x);
                emit(p, EV_ABS, ABS_Y, y);
            }
        } else {
            owner = -1;
            if (s < 0) continue;
            if (g_log_contacts)
                logf("touch up id=%u region=%u panel=%s down=%d,%d raw x %d..%d y %d..%d geom=%s",
                     t.contact_id, tr.region, p.key, tr.x0, tr.y0, tr.xmin, tr.xmax, tr.ymin,
                     tr.ymax, kGeomName[map.geom]);
            p.slot_contact[s] = -1;
            emit(p, EV_ABS, ABS_MT_SLOT, s);
            emit(p, EV_ABS, ABS_MT_PRESSURE, 0);
            emit(p, EV_ABS, ABS_MT_TRACKING_ID, -1);
        }
    }
    for (Panel &p : g_panels) {
        if (!p.dirty) continue;
        bool any = false;
        for (int c : p.slot_contact) any |= c >= 0 && !g_palm[c];
        emit(p, EV_KEY, BTN_TOUCH, any ? 1 : 0);
        emit(p, EV_SYN, SYN_REPORT, 0);
        p.dirty = false;
    }
}

// ---------------------------------------------------------------------------
// The pen (HeatAdaptorEos::SendPenReports), as the stock service turns it into
// evdev (InputEvdev::PenReports), but as a tablet device per panel instead of
// one more multitouch slot: Linux compositors take a pen through libinput's
// tablet interface (Wayland tablet-v2: pressure, tilt, eraser, barrel button,
// hover). The panel is chosen as for a new contact (contact_owner), at every
// report, so a pen hovering across the hinge leaves one panel and comes into
// range of the other; the panel's calibration of the current geometry is
// applied as for a contact.
//
// Palm rejection (pen_rules.h, g_palm_mode): while the pen is in range, and
// for kPalmGraceMs after it left, a contact that starts (on both panels, or
// on the pen's) is held back (never delivered, until it lifts), and contacts
// already down when the pen comes into range are turned into palms
// (MT_TOOL_PALM: libinput cancels them, LIBINPUT_EVENT_TOUCH_CANCEL).
//
// Pressure (pen_rules.h): libinput takes the contact from the pressure, so
// the tip's is never below pen::kTipPressureMin (the quirk's threshold), a
// hover's is 0.
struct PenState {
    int panel = -1;   // the panel the pen is in range of; -1: out of range
    bool rubber = false, tip = false, barrel = false;
    uint64_t last_ms = 0;         // the last report from the library
    uint64_t last_frame_ms = 0;   // the last frame written to the pen's device
};
PenState g_pen;
unsigned long g_pen_reports = 0, g_pen_strokes = 0;
const uint64_t kPenTimeoutMs = 300;   // no report for this long while in range: out of range
// The library's pen output timing (pen_rules.h), said every 30 s with the
// other statistics while there is any.
pen::Timing g_pen_timing;
const uint64_t g_pen_epoch_us = now_us();   // MSC_TIMESTAMP counts from the start

void pen_emit(Panel &p, int type, int code, int value) {
    struct input_event ev = {};
    ev.type = type;
    ev.code = code;
    ev.value = value;
    if (p.pen_fd >= 0 && write(p.pen_fd, &ev, sizeof ev) != (ssize_t)sizeof ev)
        logf("uinput write (pen): %s", strerror(errno));
}

// The end of every frame of a pen device: MSC_TIMESTAMP, then SYN_REPORT
// (pen_rules.h, keepalive_due: a repeated report is still a frame).
void pen_sync(Panel &p) {
    pen_emit(p, EV_MSC, MSC_TIMESTAMP, pen::msc_timestamp(now_us() - g_pen_epoch_us));
    pen_emit(p, EV_SYN, SYN_REPORT, 0);
    g_pen.last_frame_ms = now_ms();
}

void pen_out(const char *why) {
    if (g_pen.panel < 0) return;
    Panel &p = g_panels[g_pen.panel];
    if (g_pen.tip) pen_emit(p, EV_KEY, BTN_TOUCH, 0);
    pen_emit(p, EV_ABS, ABS_PRESSURE, 0);
    if (g_pen.barrel) pen_emit(p, EV_KEY, BTN_STYLUS, 0);
    pen_emit(p, EV_KEY, g_pen.rubber ? BTN_TOOL_RUBBER : BTN_TOOL_PEN, 0);
    pen_sync(p);
    if (g_log_contacts) logf("pen out of range of the %s panel (%s)", p.key, why);
    g_palm_pen.last_panel = g_pen.panel;
    g_palm_pen.panel = -1;
    g_palm_pen.out_ms = now_ms();
    g_pen = PenState();
    g_pen_timing.out();
}

// A pen in range whose device got no frame for pen::kKeepAliveMs: a frame
// with only MSC_TIMESTAMP, so that libinput does not take the silence for
// the pen leaving.
void pen_keepalive() {
    if (!pen::keepalive_due(g_pen.panel >= 0, g_pen.last_frame_ms, now_ms())) return;
    Panel &p = g_panels[g_pen.panel];
    if (p.pen_fd < 0) {   // no device to keep (the loop must not spin on it)
        g_pen.last_frame_ms = now_ms();
        return;
    }
    pen_sync(p);
    g_pen_timing.keepalive();
}

// The pen came into range of `pen_panel`: the contacts already down there
// (or on both panels) are palms.
void palm_cancel_delivered(int pen_panel) {
    for (int i = 0; i < 2; i++) {
        Panel &p = g_panels[i];
        if (!pen::palm_cancels(g_palm_mode, pen_panel, i)) continue;
        bool any = false;
        for (int k = 0; k < (int)p.slot_contact.size(); k++) {
            int c = p.slot_contact[k];
            if (c < 0 || g_palm[c]) continue;
            g_palm[c] = true;
            g_palm_contacts++;
            emit(p, EV_ABS, ABS_MT_SLOT, k);
            emit(p, EV_ABS, ABS_MT_TOOL_TYPE, MT_TOOL_PALM);
            any = true;
            if (g_log_contacts)
                logf("touch id=%d on the %s panel cancelled: the pen came into range (palm)", c,
                     p.key);
        }
        if (any) {
            emit(p, EV_KEY, BTN_TOUCH, 0);
            emit(p, EV_SYN, SYN_REPORT, 0);
        }
    }
}

// A palm lifted: free its slot, if it had one (a contact turned into a palm).
void palm_lift(int id) {
    g_palm[id] = false;
    int &owner = g_contact_panel[id];
    if (owner < 0) return;
    Panel &p = g_panels[owner];
    for (int k = 0; k < (int)p.slot_contact.size(); k++) {
        if (p.slot_contact[k] != id) continue;
        p.slot_contact[k] = -1;
        emit(p, EV_ABS, ABS_MT_SLOT, k);
        emit(p, EV_ABS, ABS_MT_TRACKING_ID, -1);
        p.dirty = true;
    }
    owner = -1;
}

void pen_report(const PenReport &r) {
    if (!g_pen_reports++)
        // Whether the library makes pen reports at all was not known before a
        // real pen came near (docs/hardware/touch.md): said once per run.
        logf("the first pen report from the heat processor: flags %#04x region %u x %d y %d "
             "pressure %u tilt %d,%d",
             r.flags, r.region, r.x, r.y, r.pressure, r.tilt_x, r.tilt_y);
    if (g_dump)
        logf("pen flags=%#04x region=%u x=%d y=%d pressure=%u tilt=%d,%d (unknown %#06x %#010x "
             "%#06x)",
             r.flags, r.region, r.x, r.y, r.pressure, r.tilt_x, r.tilt_y, r.unknown0, r.unknown17,
             r.unknown21);
    pen::Tool tool = pen::decode(r.flags);
    if (!tool.in_range) {
        pen_out("the heat processor");
        return;
    }
    TouchReport t = {};
    t.contact_id = 0x7ffe;
    t.state = 7;
    t.x = r.x;
    t.y = r.y;
    t.region = r.region;
    ContactMap map;
    int panel = contact_owner(t, map);
    if (panel < 0 || panel > 1) return;
    if (g_pen.panel >= 0 && (panel != g_pen.panel || tool.rubber != g_pen.rubber))
        pen_out(panel != g_pen.panel ? "over the other panel" : "turned round");
    Panel &p = g_panels[panel];
    if (p.pen_fd < 0) return;
    int x, y;
    pen::to_panel(r.x, r.y, map.x_offset, p.width, p.height, p.m[map.geom], &x, &y);
    if (g_pen.panel < 0) {
        if (g_palm_mode != pen::kPalmOff) palm_cancel_delivered(panel);
        g_pen.panel = panel;
        g_pen.rubber = tool.rubber;
        g_palm_pen.panel = panel;
        if (!g_pen_strokes++ || g_log_contacts)
            logf("pen in range of the %s panel: %s, raw=%d,%d local=%d,%d tilt %d,%d geom=%s",
                 p.key, tool.rubber ? "eraser" : "pen", r.x, r.y, x, y, r.tilt_x, r.tilt_y,
                 kGeomName[map.geom]);
        pen_emit(p, EV_KEY, tool.rubber ? BTN_TOOL_RUBBER : BTN_TOOL_PEN, 1);
    }
    {
        pen::Sample s;
        s.flags = r.flags;
        s.x = r.x;
        s.y = r.y;
        s.pressure = r.pressure;
        s.tilt_x = r.tilt_x;
        s.tilt_y = r.tilt_y;
        g_pen_timing.report(s, tool.tip, now_us());
    }
    pen_emit(p, EV_ABS, ABS_X, x);
    pen_emit(p, EV_ABS, ABS_Y, y);
    pen_emit(p, EV_ABS, ABS_PRESSURE, pen::pressure(tool.tip, r.pressure));
    pen_emit(p, EV_ABS, ABS_TILT_X, pen::tilt(r.tilt_x));
    pen_emit(p, EV_ABS, ABS_TILT_Y, pen::tilt(r.tilt_y));
    if (tool.tip != g_pen.tip) {
        pen_emit(p, EV_KEY, BTN_TOUCH, tool.tip);
        if (g_log_contacts)
            logf("pen %s %s on the %s panel: raw=%d,%d local=%d,%d pressure %u",
                 tool.rubber ? "eraser" : "tip", tool.tip ? "down" : "up", p.key, r.x, r.y, x, y,
                 r.pressure);
    }
    if (tool.barrel != g_pen.barrel) {
        pen_emit(p, EV_KEY, BTN_STYLUS, tool.barrel);
        if (g_log_contacts) logf("pen barrel button %s", tool.barrel ? "pressed" : "released");
    }
    pen_sync(p);
    g_pen.tip = tool.tip;
    g_pen.barrel = tool.barrel;
    g_pen.last_ms = now_ms();
}

void pen_reports(const PenReport *reports, unsigned count) {
    g_pen_timing.call(count);
    for (unsigned i = 0; i < count; i++) pen_report(reports[i]);
}

// While in range, the library sends a report every scan; none for a while
// means the pen has gone without a last report.
void pen_check_timeout() {
    if (g_pen.panel >= 0 && now_ms() - g_pen.last_ms > kPenTimeoutMs) pen_out("no reports");
}

// Each panel's width, from the digitizer's metadata feature report (report 6,
// Digitizer usage 0x63): rows, columns, width, height as uint32 after a
// 14-byte header, in the library's 0.01 mm units (85.21 x 120.20 mm here).
// spi-hid answers a GET_FEATURE with whatever response arrives next, so a
// late answer to an earlier request can stand in for this one: read it a few
// times, and fall back to the Duo 2's own value (85.21 mm) rather than to half
// the stitched range, which would misplace the right panel by 2 mm.
const int kDuo2PanelWidth = 8521;

int panel_width_from_metadata(int fd) {
    for (int attempt = 0; attempt < 4; attempt++) {
        if (attempt) usleep(200 * 1000);
        uint8_t buf[120] = { 0x06 };
        int n = ioctl(fd, HIDIOCGFEATURE(sizeof buf), buf);
        if (n < 30) {
            logf("metadata report: %s", n < 0 ? strerror(errno) : "short");
            continue;
        }
        uint32_t dims[4];
        memcpy(dims, buf + 14, sizeof dims);
        if (dims[0] == 0 || dims[0] > 200 || dims[1] == 0 || dims[1] > 200 || dims[2] < 1000 ||
            dims[2] > 20000) {
            logf("metadata report: unexpected layout (attempt %d)", attempt + 1);
            continue;
        }
        logf("digitizer: %u x %u sensors per panel, %u.%02u x %u.%02u mm", dims[0], dims[1],
             dims[2] / 100, dims[2] % 100, dims[3] / 100, dims[3] % 100);
        return (int)dims[2];
    }
    logf("metadata report unusable: assuming the Duo 2's %d", kDuo2PanelWidth);
    return kDuo2PanelWidth;
}

// The digitizer's hidraw node by its HID id: a re-created HID device (spi-hid
// does that when the report descriptor reads back differently) gets the next
// free hidraw minor, so /dev/hidraw0 is not a stable name.
bool find_hidraw(char *out, size_t len) {
    DIR *d = opendir("/sys/class/hidraw");
    if (!d) return false;
    bool found = false;
    while (struct dirent *e = readdir(d)) {
        if (strncmp(e->d_name, "hidraw", 6)) continue;
        char path[300], buf[512] = {};
        snprintf(path, sizeof path, "/sys/class/hidraw/%s/device/uevent", e->d_name);
        int fd = open(path, O_RDONLY | O_CLOEXEC);
        if (fd < 0) continue;
        ssize_t n = read(fd, buf, sizeof buf - 1);
        close(fd);
        if (n > 0 && strstr(buf, "HID_ID=001C:0000045E:00000C1D")) {
            snprintf(out, len, "/dev/%s", e->d_name);
            found = true;
            break;
        }
    }
    closedir(d);
    return found;
}

// ---------------------------------------------------------------------------
// The host side of the library: IEosHeatService. Slots as the library calls
// them (HeatAdaptorEos): 2 SetAngleSensitivity, 3 SendMessageToTouchFw,
// 4 SendPenReports, 5 SendTouchReports, 6 SendHidReportsToTPAgent. Slots
// beyond are not called by this library; they are answered harmlessly.
int g_hidraw = -1;   // -1 while the digitizer is off (system sleep)

// The digitizer across system sleep (digitizer_power.h). The sleep hook's
// signals: SIGURG before the suspend, SIGWINCH after the resume (both ignored
// by default: a daemon still starting, or one without this, is not killed by
// them). The handler keeps the last word (1 sleep, 2 wake) and wakes the main
// loop's poll through a pipe. kPowerState is what the hook waits for: "on",
// "opening" or "off"; it exists only while the main loop runs.
DigitizerPower g_power;
volatile sig_atomic_t g_power_request = 0;
int g_signal_pipe[2] = { -1, -1 };
const char kPowerState[] = "/run/duo2-touchd.digitizer";
uint64_t g_wake_word_ms = 0;   // CLOCK_MONOTONIC of the last word that the system is back

void write_power_state(const char *state) {
    char tmp[sizeof kPowerState + 8];
    snprintf(tmp, sizeof tmp, "%s.tmp", kPowerState);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) {
        logf("%s: %s", tmp, strerror(errno));
        return;
    }
    char line[16];
    int n = snprintf(line, sizeof line, "%s\n", state);
    bool ok = write(fd, line, n) == n;
    close(fd);
    if (!ok || rename(tmp, kPowerState) < 0) logf("%s: %s", kPowerState, strerror(errno));
}

void on_power_signal(int sig) {
    int saved = errno;
    g_power_request = sig == SIGURG ? 1 : 2;
    if (g_signal_pipe[1] >= 0) {
        char c = 0;
        ssize_t n = write(g_signal_pipe[1], &c, 1);
        (void)n;
    }
    errno = saved;
}

void svc_noop(void *) {}
// The hinge-angle step the library wants to hear about: the stock service
// keeps it as its vote in HingeAngleSensor (HeatService's client starts at
// 15 degrees; HeatService::SetAngleSensitivity changes it).
float g_lib_angle_sensitivity = 15.0f;
void svc_set_angle_sensitivity(void *, float s) {
    if (s != g_lib_angle_sensitivity)
        logf("heat processor asks for hinge angle steps of %.1f degrees", s);
    g_lib_angle_sensitivity = s;
}
// HidReport { uint32 length; uint8 *data } is passed by value in two registers;
// the device argument is not used (one digitizer for both panels). Returns
// *true on failure*. The stock service sends every message as a feature
// report (HIDDevice::setFeatureReport, HIDIOCSFEATURE): the library's
// switch-mode (heat mode), display-state (lit panels, stitching, hinge angle,
// panel alignment) and the other feedback reports are all features
// (vendor usages 0xC8, 0xC9, ...). Written as output reports they reach the
// firmware as something else (spi-hid: OUTPUT_REPORT, not SET_FEATURE).
unsigned long g_fw_messages = 0, g_fw_failures = 0, g_fw_dropped = 0;
// The report ids of the messages the library sends while this is on (what
// OnDeviceReset sends after a reopen, for the log).
bool g_fw_capture = false;
char g_fw_captured[3 * 16 + 1];
bool svc_send_message_to_touch_fw(void *, uint32_t length, const uint8_t *data,
                                  const EosHeatDevice *) {
    if (g_dump) {
        char hex[3 * 48 + 1] = {};
        int o = 0;
        for (uint32_t k = 0; k < length && k < 48; k++)
            o += snprintf(hex + o, sizeof hex - o, "%02x ", data[k]);
        logf("to touch fw (%s): %u bytes: %s", g_fw_feature ? "feature" : "output", length, hex);
    }
    if (g_hidraw < 0) {   // the digitizer is off (system sleep): nothing reaches it
        if (!g_fw_dropped++) logf("a message to the digitizer while it is off: dropped");
        return true;
    }
    if (g_fw_capture && length) {
        size_t o = strlen(g_fw_captured);
        if (o + 4 <= sizeof g_fw_captured)
            snprintf(g_fw_captured + o, sizeof g_fw_captured - o, " %02x", data[0]);
    }
    g_fw_messages++;
    if (g_fw_feature) {
        int n = ioctl(g_hidraw, HIDIOCSFEATURE(length), const_cast<uint8_t *>(data));
        if (n != (int)length) {
            g_fw_failures++;
            logf("feature report %#04x (%u bytes) to the digitizer failed: %s",
                 length ? data[0] : 0, length, n < 0 ? strerror(errno) : "short");
        }
        return n != (int)length;
    }
    ssize_t n = write(g_hidraw, data, length);
    if (n != (ssize_t)length) logf("hidraw write (%u bytes): %s", length, strerror(errno));
    return n != (ssize_t)length;
}
void svc_send_pen_reports(void *, const PenReport *reports, uint32_t count) {
    pen_reports(reports, count);
}
void svc_send_touch_reports(void *, const TouchReport *reports, uint32_t count) {
    touch_reports(reports, count);
}
long svc_unused(void *) { return 0; }

void *g_service_vtable[16] = {
    (void *)svc_noop,
    (void *)svc_noop,
    (void *)svc_set_angle_sensitivity,
    (void *)svc_send_message_to_touch_fw,
    (void *)svc_send_pen_reports,
    (void *)svc_send_touch_reports,
    (void *)svc_unused,
    (void *)svc_unused,
    (void *)svc_unused,
    (void *)svc_unused,
    (void *)svc_unused,
    (void *)svc_unused,
    (void *)svc_unused,
    (void *)svc_unused,
    (void *)svc_unused,
    (void *)svc_unused,
};
struct Service { void **vtable; } g_service = { g_service_vtable };

// ---------------------------------------------------------------------------
// Which panel a report belongs to: the report IDs inside each of the two
// Digitizer "capacitive heat map" (0x0D/0x0F) application collections.
struct Collections { std::set<int> panel[2]; };

Collections parse_descriptor(const uint8_t *d, int n) {
    Collections c;
    int usage_page = 0, depth = 0, heat = -1, heat_seen = 0, last_usage = -1;
    for (int i = 0; i < n;) {
        uint8_t b = d[i];
        int size = b & 3;
        if (size == 3) size = 4;
        uint32_t v = 0;
        for (int k = 0; k < size && i + 1 + k < n; k++) v |= (uint32_t)d[i + 1 + k] << (8 * k);
        switch (b & 0xfc) {
            case 0x04: usage_page = v; break;
            case 0x08: last_usage = v; break;
            case 0xa0:   // Collection
                if (depth == 0 && usage_page == 0x0d && last_usage == 0x0f) heat = heat_seen++;
                depth++;
                break;
            case 0xc0:   // End Collection
                if (--depth == 0) heat = -1;
                break;
            case 0x84:   // Report ID
                if (heat >= 0 && heat < 2) c.panel[heat].insert(v);
                break;
        }
        i += 1 + size;
    }
    return c;
}

const char kSpiReady[] = "/sys/bus/spi/devices/spi0.0/ready";

bool wait_ready(int timeout_ms) {
    for (int waited = 0; waited <= timeout_ms; waited += 50) {
        char s[16] = {};
        int fd = open(kSpiReady, O_RDONLY | O_CLOEXEC);
        if (fd >= 0) {
            ssize_t n = read(fd, s, sizeof s - 1);
            close(fd);
            if (n > 0 && !strncmp(s, "ready", 5)) return true;
        }
        usleep(50 * 1000);
    }
    return false;
}

// ---------------------------------------------------------------------------
// The stitched mode: what the stock service tells the library besides the
// heat frames, and the library's own view of the result.
Processor *g_proc = nullptr;
// The two panels, as the stock service sets them up (HeatService::init):
// dev_a = the collection with report 0x0A (region 1, kind 2: the RIGHT
// panel), dev_b = the one with 0x3C (region 2, kind 1: the LEFT panel).
EosHeatDevice g_dev_a, g_dev_b;

enum Display { kDisplayUnknown = -1, kDisplayLeft = 0, kDisplayRight = 1, kDisplayBoth = 2 };
const char *const kDisplayName[] = { "left", "right", "both" };
// What Android would tell the service (HIDL setDisplayState / hinge angle /
// setDisplayOrientation / setDexterity), as duo-posed writes it to the pose
// file, or a control command sets it. -1: not known (yet).
struct Pose {
    int display = kDisplayUnknown;
    double hinge = -1;
    int rotation = -1;
    int hand = -1;
};
Pose g_want, g_sent;

// The library's PostureManager singleton and its getters (all exported):
// whether it is stitched and which screens it takes as lit.
struct LibPosture {
    void *instance = nullptr;
    const uint8_t *guard = nullptr;
    bool (*is_stitched)(void *) = nullptr;
    bool (*screen_state)(void *, int) = nullptr;
    uint32_t (*gap)(void *) = nullptr;
    uint32_t (*hinge)(void *) = nullptr;
    bool ok() const { return instance && guard && (*guard & 1) && is_stitched && screen_state; }
} g_lib;

uint32_t lib_gap() { return g_lib.ok() && g_lib.gap ? g_lib.gap(g_lib.instance) : 0; }

void lib_posture_init(void *h) {
    g_lib.instance = dlsym(h, "_ZZN14PostureManager8InstanceEvE9mInstance");
    g_lib.guard =
        reinterpret_cast<const uint8_t *>(dlsym(h, "_ZGVZN14PostureManager8InstanceEvE9mInstance"));
    g_lib.is_stitched =
        reinterpret_cast<bool (*)(void *)>(dlsym(h, "_ZN14PostureManager13GetIsStitchedEv"));
    g_lib.screen_state = reinterpret_cast<bool (*)(void *, int)>(
        dlsym(h, "_ZN14PostureManager14GetScreenStateE10eREGION_ID"));
    g_lib.gap =
        reinterpret_cast<uint32_t (*)(void *)>(dlsym(h, "_ZN14PostureManager10GetGapSizeEv"));
    g_lib.hinge =
        reinterpret_cast<uint32_t (*)(void *)>(dlsym(h, "_ZN14PostureManager13GetHingeAngleEv"));
}

// The geometry the library's contacts are in now: stitched with both screens
// lit is its topology 2 (PostureManager::UpdateTopology), anything else is
// per panel. Without the library's own view: from what it was told.
Geom lib_geometry() {
    if (g_mode == kModeSplit) return kGeomSplit;
    if (g_lib.ok())
        return g_lib.is_stitched(g_lib.instance) && g_lib.screen_state(g_lib.instance, 0) &&
                       g_lib.screen_state(g_lib.instance, 1)
                   ? kGeomStitched
                   : kGeomSingle;
    return g_sent.display == kDisplayBoth && !(g_sent.hinge >= 235) ? kGeomStitched : kGeomSingle;
}

void geometry_update(const char *why) {
    Geom g = lib_geometry();
    if (g == g_geom) return;
    logf("geometry %s -> %s (%s; library: stitched %d, screens %d %d, gap %d, hinge %d)",
         kGeomName[g_geom], kGeomName[g], why, g_lib.ok() ? g_lib.is_stitched(g_lib.instance) : -1,
         g_lib.ok() ? g_lib.screen_state(g_lib.instance, 0) : -1,
         g_lib.ok() ? g_lib.screen_state(g_lib.instance, 1) : -1,
         g_lib.ok() && g_lib.gap ? (int)g_lib.gap(g_lib.instance) : -1,
         g_lib.ok() && g_lib.hinge ? (int)g_lib.hinge(g_lib.instance) : -1);
    g_geom = g;
}

// HeatService::onDisplayParameterUpdate: the panel of kind 1 (the left one),
// then the one of kind 2 (the right one), then stitching on exactly when
// both are lit.
void send_display(int display) {
    bool left = display != kDisplayRight, right = display != kDisplayLeft;
    slot<void (*)(Processor *, const EosHeatDevice *, bool)>(g_proc, kOnDisplayChange)(
        g_proc, &g_dev_b, left);
    slot<void (*)(Processor *, const EosHeatDevice *, bool)>(g_proc, kOnDisplayChange)(
        g_proc, &g_dev_a, right);
    slot<void (*)(Processor *, bool)>(g_proc, kOnStitchingModeChange)(g_proc, left && right);
    logf("display state %s passed to the heat processor (stitching %s)", kDisplayName[display],
         left && right ? "on" : "off");
    g_sent.display = display;
}

void send_hinge(double deg) {
    slot<void (*)(Processor *, const EosHeatDevice *, const EosHeatDevice *, float)>(
        g_proc, kOnHingeAngleChange)(g_proc, &g_dev_a, &g_dev_b, (float)deg);
    logf("hinge angle %.1f passed to the heat processor", deg);
    g_sent.hinge = deg;
}

// The stitched mode: pass on what changed, as the stock service does. Not
// while the digitizer is off: the library tells the firmware at once, so what
// changed meanwhile waits for the reopen.
void pose_apply() {
    if (g_mode != kModeStitched || !g_proc || g_power.state() != DigitizerPower::kOn) return;
    if (g_want.display != kDisplayUnknown && g_want.display != g_sent.display)
        send_display(g_want.display);
    if (g_want.hinge >= 0 && g_want.hinge != g_sent.hinge) {
        // HingeAngleSensor: the first angle goes through; after it, a change
        // of at least the smallest vote: HeatService's (the library's
        // sensitivity) and HeatDevice's (the distance from 5 degrees, so that
        // closing the phone is always heard).
        double step = fabs(g_sent.hinge - 5.0);
        if (g_lib_angle_sensitivity < step) step = g_lib_angle_sensitivity;
        if (g_sent.hinge < 0 || fabs(g_want.hinge - g_sent.hinge) >= step) send_hinge(g_want.hinge);
    }
    if (g_want.rotation >= 0 && g_want.rotation != g_sent.rotation) {
        // HeatService::onDisplayOrientationChange: degrees (the library rounds
        // them to 0 / 90 / 180 / 270, PostureManager::SetScreenRotation).
        slot<void (*)(Processor *, uint16_t)>(g_proc, kOnDeviceRotationChange)(
            g_proc, (uint16_t)g_want.rotation);
        logf("rotation %d passed to the heat processor", g_want.rotation);
        g_sent.rotation = g_want.rotation;
    }
    if (g_want.hand >= 0 && g_want.hand != g_sent.hand) {
        // HeatService::onDexterityChange: left-handed 0, right-handed 1.
        slot<void (*)(Processor *, int)>(g_proc, kOnRightOrLeftHandModeChange)(g_proc, g_want.hand);
        logf("handedness %s passed to the heat processor", g_want.hand ? "right" : "left");
        g_sent.hand = g_want.hand;
    }
    geometry_update("pose");
}

// One line of the pose: "display both|left|right|none", "hinge DEG",
// "rotation DEG", "hand left|right". Returns false if not understood.
bool pose_line(const char *line, Pose &p) {
    char word[16];
    double v;
    if (sscanf(line, "display %15s", word) == 1) {
        if (!strcmp(word, "both"))
            p.display = kDisplayBoth;
        else if (!strcmp(word, "left"))
            p.display = kDisplayLeft;
        else if (!strcmp(word, "right"))
            p.display = kDisplayRight;
        else if (strcmp(word, "none"))
            return false;   // dark: nothing changes (touch still wakes)
        return true;
    }
    if (sscanf(line, "hinge %lf", &v) == 1 && v >= 0 && v <= 360) {
        p.hinge = v;
        return true;
    }
    if (sscanf(line, "rotation %lf", &v) == 1 && v >= 0 && v < 360) {
        p.rotation = (int)v;
        return true;
    }
    if (sscanf(line, "hand %15s", word) == 1 && (!strcmp(word, "left") || !strcmp(word, "right"))) {
        p.hand = word[0] == 'r';
        return true;
    }
    return false;
}

// duo-posed's pose file (--pose-file): read again when it changes (it is
// replaced as a whole, so a new inode or mtime).
const char *g_pose_file = "/run/duo-posed/touch";
void pose_file_poll() {
    static ino_t ino = 0;
    static struct timespec mtime = {};
    static uint64_t last = 0;
    uint64_t now = now_ms();
    if (now - last < 250) return;
    last = now;
    struct stat st;
    if (stat(g_pose_file, &st) < 0) {
        ino = 0;
        return;
    }
    if (st.st_ino == ino && st.st_mtim.tv_sec == mtime.tv_sec &&
        st.st_mtim.tv_nsec == mtime.tv_nsec)
        return;
    ino = st.st_ino;
    mtime = st.st_mtim;
    FILE *f = fopen(g_pose_file, "r");
    if (!f) return;
    char line[128];
    Pose p = g_want;
    while (fgets(line, sizeof line, f)) {
        line[strcspn(line, "\r\n")] = 0;
        if (line[0] && line[0] != '#' && !pose_line(line, p))
            logf("%s: not understood: %s", g_pose_file, line);
    }
    fclose(f);
    if (p.display != g_want.display || p.hinge != g_want.hinge || p.rotation != g_want.rotation ||
        p.hand != g_want.hand)
        logf("pose from %s: display %s, hinge %.1f, rotation %d, hand %d", g_pose_file,
             p.display >= 0 ? kDisplayName[p.display] : "?", p.hinge, p.rotation, p.hand);
    g_want = p;
}

// The pen settings file (Duo Settings > Pen, through duo-settings-helper):
// "palm_rejection = both|panel|off". Read at the start and again when it
// changes (replaced as a whole: a new inode or mtime); none, or no valid
// line: both panels. --no-palm-rejection wins over it.
const char *g_pen_conf = "/etc/duo2/pen.conf";
void pen_conf_poll(bool force = false) {
    static ino_t ino = 0;
    static struct timespec mtime = {};
    static uint64_t last = 0;
    static bool seen = false;
    uint64_t now = now_ms();
    if (!force && now - last < 1000) return;
    last = now;
    pen::PalmMode mode = pen::kPalmBoth;
    struct stat st;
    if (stat(g_pen_conf, &st) == 0) {
        if (seen && st.st_ino == ino && st.st_mtim.tv_sec == mtime.tv_sec &&
            st.st_mtim.tv_nsec == mtime.tv_nsec)
            return;
        ino = st.st_ino;
        mtime = st.st_mtim;
        char text[4096] = {};
        if (FILE *f = fopen(g_pen_conf, "r")) {
            size_t n = fread(text, 1, sizeof text - 1, f);
            text[n] = 0;
            fclose(f);
        }
        if (!pen::conf_palm(text, &mode))
            logf("%s: no palm_rejection line (both|panel|off): both", g_pen_conf);
    } else {
        if (seen && ino == 0) return;   // still none
        ino = 0;
    }
    seen = true;
    if (g_palm_forced_off) mode = pen::kPalmOff;
    if (mode != g_palm_mode || force)
        logf("palm rejection %s (%s)", pen::palm_name(mode),
             g_palm_forced_off ? "--no-palm-rejection"
             : ino             ? g_pen_conf
                               : "default");
    g_palm_mode = mode;
}

// AutoBond (autobond.h): the host state the stock touch-pen HAL keeps on the
// digitizer for the Surface pen, from duo2-pend's file. Sent whenever the
// digitizer is on and the state differs from what it was last told; after
// every power-on or reset of the digitizer all of it again. Stitched mode
// only: the split mode's own 0x38 (force_mode) is left as it is.
const char *g_ab_file = "/run/duo2-pen/autobond";
autobond::State g_ab_want, g_ab_sent;
bool g_ab_configured = false;   // false: the digitizer has not been told since its last power-on

void autobond_poll() {
    static ino_t ino = 0;
    static struct timespec mtime = {};
    static uint64_t last = 0;
    uint64_t now = now_ms();
    if (now - last < 1000) return;
    last = now;
    struct stat st;
    if (stat(g_ab_file, &st) < 0) {
        if (ino) logf("AutoBond: %s is gone; the digitizer keeps what it was told", g_ab_file);
        ino = 0;
        return;
    }
    if (st.st_ino == ino && st.st_mtim.tv_sec == mtime.tv_sec &&
        st.st_mtim.tv_nsec == mtime.tv_nsec)
        return;
    ino = st.st_ino;
    mtime = st.st_mtim;
    char text[512] = {};
    int fd = open(g_ab_file, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return;
    ssize_t n = read(fd, text, sizeof text - 1);
    close(fd);
    if (n < 0) return;
    autobond::State s = autobond::parse(text);
    if (s != g_ab_want)
        logf("AutoBond state from %s: host %s, Bluetooth %s, pen link %s", g_ab_file,
             s.have_host ? "known" : "none", s.adapter_on ? "on" : "off",
             s.link_up ? "up" : "down");
    g_ab_want = s;
}

void autobond_apply() {
    if (g_mode != kModeStitched || g_hidraw < 0 || g_power.state() != DigitizerPower::kOn) return;
    autobond::Plan p = autobond::plan(g_ab_want, g_ab_sent, g_ab_configured);
    if (!p.any()) return;
    bool ok = true;
    if (p.capability) {
        uint8_t r[2];
        autobond::capability_report(r);
        if (ioctl(g_hidraw, HIDIOCSFEATURE(sizeof r), r) != (int)sizeof r) {
            logf("AutoBond: feature 0x70 to the digitizer: %s", strerror(errno));
            ok = false;
        }
    }
    if (ok && p.host) {
        uint8_t r[8];
        autobond::host_report(g_ab_want, r);
        if (ioctl(g_hidraw, HIDIOCSFEATURE(sizeof r), r) != (int)sizeof r) {
            logf("AutoBond: feature 0x56 to the digitizer: %s", strerror(errno));
            ok = false;
        }
    }
    if (ok && p.link) {
        uint8_t r[2];
        autobond::link_report(g_ab_want, r);
        if (write(g_hidraw, r, sizeof r) != (ssize_t)sizeof r) {
            logf("AutoBond: output 0x38 to the digitizer: %s", strerror(errno));
            ok = false;
        }
    }
    if (!ok) return;   // again at the next pass (the file poll: once a second)
    logf("AutoBond: told the digitizer%s%s (Bluetooth %s, pen link %s)",
         p.host ? " AutoBond on, the host" : "",
         p.link ? (p.host ? " and the link" : " the link") : "",
         g_ab_want.adapter_on ? "on" : "off", g_ab_want.link_up ? "up" : "down");
    g_ab_sent = g_ab_want;
    g_ab_configured = true;
}

// Reports the library is not given that only a pen makes, said in the
// journal the first times they come (they are counted in the 30 s
// statistics as well): the firmware's own pen report (0x01, the standard Pen
// collection, which the stock system does not use), the pen's id (0x22), the
// pen tail (0x58) and AutoBond's "a pen is in range" (0x6e: the pen's id and
// Bluetooth address, which the stock HAL hands to Android for the pairing of
// the top button; logged without them). If the heat processor makes no pen
// reports but these come, the firmware sees the pen (docs/hardware/touch.md).
void pen_raw_report(const uint8_t *buf, ssize_t n) {
    static unsigned logged[256];
    uint8_t id = buf[0];
    if (id != 0x01 && id != 0x22 && id != 0x58 && id != 0x6e) return;
    if (logged[id]++ >= (g_log_contacts ? 20u : 3u)) return;
    if (id == 0x01 && n >= 16) {
        // in range, tip, barrel, invert, eraser bits; X 0..9600, Y 0..7200,
        // pressure 0..4096, X / Y tilt 0..18000 (the report descriptor)
        auto u16 = [&](int at) { return (unsigned)(buf[at] | buf[at + 1] << 8); };
        logf("the firmware's own pen report (0x01): bits %#04x x %u y %u pressure %u tilt %u,%u "
             "(%zd bytes)",
             buf[1], u16(2), u16(4), u16(6), u16(8), u16(10), n);
    } else if (id == 0x58 && n >= 3) {
        logf("pen tail report (0x58): %#06x", (unsigned)(buf[1] | buf[2] << 8));
    } else {
        logf("%s (report %#04x, %zd bytes)",
             id == 0x6e ? "AutoBond: the firmware has a pen in range" : "pen id report", id, n);
    }
}

// The fallback (stitched mode only). The one sure sign that the stitched
// path is not working is the digitizer's single-point report (0x40: it is
// not in heat mode). Then the daemon exits and systemd starts it again
// (Restart=), which runs the stock sequence anew: a re-initialisation, still
// stitched. Only when that has happened kReinitMax times within
// kReinitWindowS does it note why in kFallbackNote and the restarted daemon
// runs split (the outer edges out of reach, but touch alive) until the note
// is removed or the phone reboots.
// Heuristics on heat frames without contacts were dropped (2026-11-07):
// 600 frames in 8 s with the phone in the hand, half an hour into a working
// stitched run, put the daemon in the split mode for the rest of the boot;
// a hand or a palm near the glass looks exactly like that.
const char kFallbackNote[] = "/run/duo2-touchd.fallback";
const char kReinitLog[] = "/run/duo2-touchd.reinit";   // CLOCK_BOOTTIME seconds, one per line
const int kReinitMax = 3;
const long kReinitWindowS = 600;
const int kFallbackSinglePoint = 10;       // 0x40 reports ...
const uint64_t kFallbackSinglePointMs = 5000;
bool g_fallback = false;
// A reset that happened while this process was frozen (system suspend): the
// digitizer is set up again by a clean restart (systemd), see the main loop.
bool g_restart = false;

void fallback(const char *reason) {
    struct timespec ts;
    clock_gettime(CLOCK_BOOTTIME, &ts);
    int recent = 0;
    if (FILE *f = fopen(kReinitLog, "r")) {
        long t;
        while (fscanf(f, "%ld", &t) == 1)
            if (ts.tv_sec - t < kReinitWindowS) recent++;
        fclose(f);
    }
    if (recent < kReinitMax) {
        logf("STITCHED MODE FAILED: %s. Re-initialising it (restart %d of %d within %ld s)", reason,
             recent + 1, kReinitMax, kReinitWindowS);
        if (FILE *f = fopen(kReinitLog, "a")) {
            fprintf(f, "%ld\n", (long)ts.tv_sec);
            fclose(f);
        }
    } else {
        logf("STITCHED MODE FAILED: %s, again after %d re-initialisations. Falling back to the "
             "split mode "
             "(restart; %s holds the reason)",
             reason, recent, kFallbackNote);
        if (FILE *f = fopen(kFallbackNote, "w")) {
            fprintf(f, "%s\n", reason);
            fclose(f);
        }
    }
    g_fallback = true;
}

// report_id: the report the digitizer sent; side: which panel a heat frame
// came from (unused now: no heat-frame heuristic, see above).
// After a reopen (system sleep), until the first heat frame, the same burst
// means the reopen did not take (in either mode): the clean restart, which is
// not counted as a failure of the stitched mode.
void fallback_check(uint8_t report_id, int side) {
    (void)side;
    if (report_id != 0x40 || g_fallback || g_restart) return;
    bool fresh = g_power.fresh_reopen();
    if (g_mode != kModeStitched && !fresh) return;
    uint64_t now = now_ms();
    static uint64_t ring[kFallbackSinglePoint];
    static int head = 0;
    ring[head] = now;
    head = (head + 1) % kFallbackSinglePoint;
    uint64_t oldest = ring[head];
    if (!oldest || now - oldest > kFallbackSinglePointMs) return;
    if (fresh) {
        logf("the digitizer sends its single-point report (0x40) after the reopen: not in heat "
             "mode; "
             "restarting for a clean start");
        g_restart = true;
        return;
    }
    fallback("the digitizer sends its single-point report (0x40): not in heat mode");
}

// Bring-up control: a FIFO taking lines "tap PANEL FX FY" (PANEL l or r,
// FX/FY as fractions of the panel) to test the input stack hands-off, and
// (stitched mode) the pose lines above, which hold until the pose file changes.
const char kControl[] = "/run/duo2-touchd.ctl";
int g_control = -1;
extern double g_hinge_request;
extern int g_stitch_request;

void scripted_tap(int panel, double fx, double fy) {
    Panel &p = g_panels[panel];
    TouchReport t = {};
    t.contact_id = 0x7f01;
    t.state = 7;
    t.width = 900;
    t.height = 900;
    t.orientation = 90;
    t.region = panel == 1 ? 1 : 2;
    int x_offset = g_geom == kGeomSingle && panel == 1 ? right_side_offset() : p.x_offset;
    t.x = x_offset + (int)(fx * (p.width - 1));
    t.y = (int)(fy * 12019);
    for (int i = 0; i < 4; i++) {
        touch_reports(&t, 1);
        usleep(10000);
    }
    t.state = 0;
    touch_reports(&t, 1);
    usleep(120000);
}

// A synthetic pen report through the same path as the library's: PANEL 0/1,
// FX/FY as fractions of the panel, the flags of PenReport.
void scripted_pen(int panel, double fx, double fy, unsigned pressure, unsigned flags,
                  int tilt_x = 0, int tilt_y = 0) {
    Panel &p = g_panels[panel];
    PenReport r = {};
    r.flags = (uint8_t)flags;
    r.region = panel == 1 ? 1 : 2;
    int x_offset = g_geom == kGeomSingle && panel == 1 ? right_side_offset() : p.x_offset;
    r.x = x_offset + (int)(fx * (p.width - 1));
    r.y = (int)(fy * (p.height - 1));
    r.pressure = (uint16_t)pressure;
    r.tilt_x = (int16_t)tilt_x;
    r.tilt_y = (int16_t)tilt_y;
    pen_reports(&r, 1);
}

// A scripted stroke on a panel: hover in, the tip down with the pressure
// rising and falling across the middle of the panel, the barrel button held
// for the middle third, tip up, hover, out of range; then the eraser end down
// and out. ~2 s; to test the pen path hands-off.
void scripted_pen_stroke(int panel) {
    const unsigned in = kPenInRange;
    for (int i = 0; i < 10; i++) {
        scripted_pen(panel, 0.30, 0.40, 0, in, -2000, 1500);
        usleep(8000);
    }
    for (int i = 0; i <= 60; i++) {
        double f = i / 60.0;
        unsigned pressure = (unsigned)(65535 * (f < 0.5 ? f * 2 : (1 - f) * 2));
        unsigned flags = in | kPenTip | (i > 20 && i < 40 ? kPenBarrel : 0);
        scripted_pen(panel, 0.30 + 0.40 * f, 0.40 + 0.10 * f, pressure ? pressure : 1, flags,
                     -2000 + 4000 * f, 1500);
        usleep(8000);
    }
    for (int i = 0; i < 10; i++) {
        scripted_pen(panel, 0.70, 0.50, 0, in, 2000, 1500);
        usleep(8000);
    }
    scripted_pen(panel, 0.70, 0.50, 0, 0);
    usleep(400000);
    for (int i = 0; i < 5; i++) {
        scripted_pen(panel, 0.50, 0.60, 0, in | kPenInvert);
        usleep(8000);
    }
    for (int i = 0; i <= 20; i++) {
        scripted_pen(panel, 0.50 + 0.01 * i, 0.60, 30000, in | kPenInvert | kPenEraser);
        usleep(8000);
    }
    scripted_pen(panel, 0.70, 0.60, 0, 0);
}

void control_open() {
    unlink(kControl);
    if (mkfifo(kControl, 0600) < 0) {
        logf("%s: %s", kControl, strerror(errno));
        return;
    }
    g_control = open(kControl, O_RDWR | O_NONBLOCK | O_CLOEXEC);
}

// The contact log stops as soon as Developer mode or its switch is off
// (devgate.h); checked every 2 s while it runs.
void touch_log_poll() {
    static uint64_t last = 0;
    if (!g_log_contacts) return;
    uint64_t now = now_ms();
    if (now - last < 2000) return;
    last = now;
    if (!devgate::touch_log_allowed()) {
        g_log_contacts = false;
        logf("contact log off: Developer mode or its switch is off");
    }
}

void control_read() {
    static char line[256];
    static size_t len = 0;
    char c;
    while (read(g_control, &c, 1) == 1) {
        if (c != '\n') {
            if (len < sizeof line - 1) line[len++] = c;
            continue;
        }
        line[len] = 0;
        len = 0;
        char panel;
        double fx, fy;
        if (sscanf(line, "tap %c %lf %lf", &panel, &fx, &fy) == 3 &&
            (panel == 'l' || panel == 'r')) {
            scripted_tap(panel == 'r', fx, fy);
            logf("scripted tap %c %.2f %.2f", panel, fx, fy);
        } else if (sscanf(line, "pen %c %lf %lf", &panel, &fx, &fy) == 3 &&
                   (panel == 'l' || panel == 'r')) {
            // "pen l|r FX FY [PRESSURE [FLAGS]]": one report (default: in range, the
            // tip down when a pressure is given)
            unsigned pressure = 0, flags = 0;
            int n = sscanf(line, "pen %*c %*f %*f %u %i", &pressure, &flags);
            if (n < 2) flags = kPenInRange | (pressure ? kPenTip : 0);
            scripted_pen(panel == 'r', fx, fy, pressure, flags);
        } else if (!strcmp(line, "pen out")) {
            pen_out("control FIFO");
        } else if (sscanf(line, "penstroke %c", &panel) == 1 && (panel == 'l' || panel == 'r')) {
            scripted_pen_stroke(panel == 'r');
            logf("scripted pen stroke on the %s panel", panel == 'r' ? "right" : "left");
        } else if (!strncmp(line, "palm ", 5) && pen::palm_parse(line + 5, &g_palm_mode)) {
            // "palm on|both|panel|off": until the pen settings file changes
            logf("palm rejection %s (control FIFO)", pen::palm_name(g_palm_mode));
        } else if (!strcmp(line, "lift")) {
            panels_lift_all();
            logf("lifted all contacts");
        } else if (!strcmp(line, "log on") || !strcmp(line, "log off")) {
            bool want = line[5] == 'n';
            if (want && !devgate::touch_log_allowed()) {
                logf("contact log refused: it needs Developer mode and its switch on "
                     "(Settings > Developer > Log touch positions)");
                want = false;
            }
            g_log_contacts = want;
            logf("contact log %s", g_log_contacts ? "on" : "off");
        } else if (g_mode == kModeStitched && pose_line(line, g_want)) {
            logf("control: %s", line);
        } else if (g_mode == kModeStitched && !strcmp(line, "fallback")) {
            fallback("asked for on the control FIFO");   // to test the fallback path
        } else if (!strcmp(line, "suspend") || !strcmp(line, "resume")) {
            // what the sleep hook's signals do, without a suspend (to test the
            // reopen; "suspend" alone comes back on its own after 20 s)
            logf("control: %s", line);
            g_power_request = line[0] == 's' ? 1 : 2;
        } else if (!strcmp(line, "status")) {
            logf("status: mode %s, geometry %s, sent display %s hinge %.1f rotation %d hand %d, "
                 "wanted display %s "
                 "hinge %.1f, heartbeat x %d / %d, firmware messages %lu (%lu failed)",
                 g_mode == kModeStitched ? "stitched" : "split", kGeomName[g_geom],
                 g_sent.display >= 0 ? kDisplayName[g_sent.display] : "?", g_sent.hinge,
                 g_sent.rotation, g_sent.hand,
                 g_want.display >= 0 ? kDisplayName[g_want.display] : "?", g_want.hinge,
                 g_heartbeat_x[1], g_heartbeat_x[2], g_fw_messages, g_fw_failures);
            logf("status: pen %s, %lu pen reports, %lu times in range; palm rejection %s, %lu "
                 "contacts held back",
                 g_pen.panel < 0 ? "out of range"
                 : g_pen.panel   ? "over the right panel"
                                 : "over the left panel",
                 g_pen_reports, g_pen_strokes, pen::palm_name(g_palm_mode), g_palm_contacts);
            logf("status: digitizer %s; off for system sleep %lu times, on again %lu times (last: "
                 "off %.1f s, "
                 "ready %llu ms after the open); %lu messages dropped while off",
                 g_power.state() == DigitizerPower::kOn    ? "on"
                 : g_power.state() == DigitizerPower::kOff ? "off"
                                                           : "opening",
                 g_power.closes(), g_power.reopens(), g_power.last_off_ms() / 1000.0,
                 (unsigned long long)g_power.last_ready_ms(), g_fw_dropped);
        } else if (!strncmp(line, "hinge ", 6)) {
            g_hinge_request = atof(line + 6);
        } else if (!strcmp(line, "stitch on") || !strcmp(line, "stitch off")) {
            g_stitch_request = line[8] == 'n' ? 1 : 0;
        } else {
            logf("control: unknown command '%s'", line);
        }
    }
}

volatile sig_atomic_t g_stop = 0;
double g_hinge_request = -1;   // "hinge DEG" on the control FIFO
int g_stitch_request = -1;     // "stitch on|off" on the control FIFO
volatile sig_atomic_t g_swipe = 0;   // 1: full swipe up (SIGUSR1), 2: partial (SIGUSR2)
void on_signal(int) { g_stop = 1; }
void on_swipe_signal(int sig) { g_swipe = sig == SIGUSR2 ? 2 : 1; }

// A scripted swipe up the middle of the right panel through the running
// daemon's own devices -- SIGUSR1 a full one, SIGUSR2 a short one -- to test
// the input stack exactly as touches take it, without hands on the device.
void scripted_swipe(int steps) {
    TouchReport t = {};
    t.contact_id = 0x7f00;
    t.state = 7;
    t.width = 1500;
    t.height = 1500;
    t.orientation = 90;
    t.region = 1;
    t.x = g_panels[1].x_offset + g_panels[1].width / 2;
    for (int step = 0; step <= steps; step++) {
        t.y = 11900 - step * 200;
        touch_reports(&t, 1);
        usleep(16000);
    }
    t.state = 0;
    touch_reports(&t, 1);
    logf("scripted swipe sent (%d steps)", steps);
}

}  // namespace

int main(int argc, char **argv) {
    const char *hidraw = nullptr;
    const char *lib = "/vendor/lib64/c1/libsurfacetouch_c1.so";
    const char *state = "/var/lib/duo2-touchd";
    const char *calibration = "/etc/duo2/touch-calibration";
    const char *mode_file = "/etc/duo2/touch-mode";
    const char *mode_arg = nullptr;
    double hinge = -1;            // degrees; < 0: the library is not told
    int lib_log = -1; // the library's g_EosTraceLoggingSeverity (its logs need liblog-stderr.so)
    int force_mode_arg =
        -1; // --force-mode / --no-force-mode: else per mode (split yes, stitched no)
    int fw_feature_arg =
        -1; // --fw-feature / --fw-output: else per mode (stitched feature, split output)
    bool selftest = false;
    int selftest_x = 13194; // middle of the right panel
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--hidraw") && i + 1 < argc)
            hidraw = argv[++i];
        else if (!strcmp(argv[i], "--lib") && i + 1 < argc)
            lib = argv[++i];
        else if (!strcmp(argv[i], "--state") && i + 1 < argc)
            state = argv[++i];
        else if (!strcmp(argv[i], "--dump"))
            g_dump = true;
        else if (!strcmp(argv[i], "--calibration") && i + 1 < argc)
            calibration = argv[++i];
        else if (!strcmp(argv[i], "--log-contacts"))
            g_log_contacts = true;
        else if (!strcmp(argv[i], "--hinge") && i + 1 < argc)
            hinge = atof(argv[++i]);
        else if (!strcmp(argv[i], "--mode") && i + 1 < argc)
            mode_arg = argv[++i];
        else if (!strcmp(argv[i], "--mode-file") && i + 1 < argc)
            mode_file = argv[++i];
        else if (!strcmp(argv[i], "--pose-file") && i + 1 < argc)
            g_pose_file = argv[++i];
        else if (!strcmp(argv[i], "--lib-log") && i + 1 < argc)
            lib_log = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--force-mode"))
            force_mode_arg = 1;
        else if (!strcmp(argv[i], "--no-force-mode"))
            force_mode_arg = 0;
        else if (!strcmp(argv[i], "--fw-feature"))
            fw_feature_arg = 1;
        else if (!strcmp(argv[i], "--fw-output"))
            fw_feature_arg = 0;
        else if (!strcmp(argv[i], "--no-palm-rejection"))
            g_palm_forced_off = true;
        else if (!strcmp(argv[i], "--pen-conf") && i + 1 < argc)
            g_pen_conf = argv[++i];
        else if (!strcmp(argv[i], "--selftest"))
            selftest = true;
        else if (!strcmp(argv[i], "--selftest-x") && i + 1 < argc) {
            selftest = true;
            selftest_x = atoi(argv[++i]);
        } else {
            logf("usage: %s [--hidraw DEV] [--lib PATH] [--state DIR] [--mode stitched|split] "
                 "[--mode-file FILE]"
                 " [--pose-file FILE] [--calibration FILE] [--log-contacts] [--hinge DEG] "
                 "[--lib-log N]"
                 " [--force-mode|--no-force-mode] [--fw-feature|--fw-output] [--no-palm-rejection] "
                 "[--pen-conf FILE]"
                 " [--dump]",
                 argv[0]);
            return 2;
        }
    }
    // --log-contacts too only with Developer mode and the log's switch on.
    if (g_log_contacts && !devgate::touch_log_allowed()) {
        logf("--log-contacts refused: it needs Developer mode and its switch on "
             "(Settings > Developer > Log touch positions)");
        g_log_contacts = false;
    }
    // The mode: --mode, else the mode file, else split; stitched only while
    // no earlier instance of this boot fell back from it (whichever way it
    // was asked for).
    {
        char word[32] = {};
        const char *from = "--mode";
        if (mode_arg)
            snprintf(word, sizeof word, "%s", mode_arg);
        else if (FILE *f = fopen(mode_file, "r")) {
            char line[128];
            while (fgets(line, sizeof line, f))
                if (line[0] != '#' && sscanf(line, "%31s", word) == 1) break;
            fclose(f);
            from = mode_file;
        } else {
            from = "default";
        }
        if (!strcmp(word, "stitched"))
            g_mode = kModeStitched;
        else if (word[0] && strcmp(word, "split"))
            logf("mode '%s' (%s) not known: split", word, from);
        if (g_mode == kModeStitched) {
            if (FILE *f = fopen(kFallbackNote, "r")) {
                char why[200] = {};
                if (!fgets(why, sizeof why, f)) why[0] = 0;
                fclose(f);
                why[strcspn(why, "\n")] = 0;
                logf("the stitched mode fell back earlier in this boot (%s): split until %s is "
                     "removed",
                     why, kFallbackNote);
                g_mode = kModeSplit;
            }
        }
        g_fw_feature = fw_feature_arg >= 0 ? fw_feature_arg : g_mode == kModeStitched;
        g_force_mode = force_mode_arg >= 0 ? force_mode_arg : g_mode == kModeSplit;
        logf("mode %s (%s); firmware messages as %s; heat mode forced: %s",
             g_mode == kModeStitched ? "stitched" : "split", mode_arg ? "--mode" : from,
             g_fw_feature ? "feature reports" : "output reports", g_force_mode ? "yes" : "no");
    }
    if (selftest) {
        // A scripted swipe up a panel (default: the middle of the right one), through the
        // same contact-to-evdev path the library's reports take; for testing
        // the input stack without hands on the device. Ranges are the ones
        // the library reports for the Duo 2.
        AxisRange r = { 0, 17455, 0, 12019 };
        if (!panels_create(r, 8521, 11)) return 1;
        sleep(2); // let the compositor open the new device
        TouchReport t = {};
        t.contact_id = 1;
        t.state = 7;
        t.x = selftest_x;
        t.width = 1500;
        t.height = 1500;
        t.orientation = 90;
        t.region = selftest_x >= r.x1 / 2 ? 1 : 2;
        for (int step = 0; step <= 30; step++) {
            t.y = 11900 - step * 200;
            touch_reports(&t, 1);
            usleep(16000);
        }
        t.state = 0;
        touch_reports(&t, 1);
        sleep(1);
        panels_destroy();
        return 0;
    }
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    signal(SIGUSR1, on_swipe_signal);
    signal(SIGUSR2, on_swipe_signal);
    // System sleep: the hook's signals, from here on (before the slow start).
    // A state file left by an instance that died goes: the hook waits only
    // for a daemon whose main loop runs.
    if (pipe2(g_signal_pipe, O_NONBLOCK | O_CLOEXEC) < 0) logf("pipe: %s", strerror(errno));
    {
        struct sigaction sa = {};
        sa.sa_handler = on_power_signal;
        sigemptyset(&sa.sa_mask);
        sa.sa_flags = SA_RESTART;
        sigaction(SIGURG, &sa, nullptr);
        sigaction(SIGWINCH, &sa, nullptr);
    }
    unlink(kPowerState);
    mkdir(state, 0700);
    calibration_load(calibration);
    pen_conf_poll(true);

    static char found[64];
    const bool hidraw_given = hidraw != nullptr;
    if (!hidraw) {
        // The node appears with the spi-hid device; give udev a moment.
        for (int i = 0; i < 50 && !find_hidraw(found, sizeof found); i++) usleep(100 * 1000);
        if (!found[0]) {
            logf("no hidraw node with HID id 045E:0C1D");
            return 1;
        }
        hidraw = found;
    }
    // Opening hidraw wakes the digitizer; spi-hid reports it usable through
    // its "ready" attribute, and the stock service waits for that before it
    // starts the library (HidrawDevice::startPollingOnReadyFile). Writes
    // before then fail with ENODEV. Opened while spi-hid is still setting the
    // device up (right after boot), the open does not power it, and spi-hid
    // then powers it down under us: "ready" never comes. Closing and opening
    // again powers it up properly (as long as nothing else holds it open).
    for (int attempt = 1;; attempt++) {
        g_hidraw = open(hidraw, O_RDWR | O_CLOEXEC);
        if (g_hidraw < 0) {
            logf("open %s: %s", hidraw, strerror(errno));
            return 1;
        }
        if (wait_ready(3000)) break;
        close(g_hidraw);
        if (attempt == 5) {
            logf("%s never became ready", kSpiReady);
            return 1;
        }
        logf("%s not ready after open (attempt %d): closing and opening again", kSpiReady, attempt);
        sleep(1);
    }
    struct hidraw_devinfo info = {};
    ioctl(g_hidraw, HIDIOCGRAWINFO, &info);
    if ((uint16_t)info.vendor != 0x045e || (uint16_t)info.product != 0x0c1d) {
        logf("%s is %04x:%04x, not the Duo 2 digitizer (045e:0c1d)", hidraw, (uint16_t)info.vendor,
             (uint16_t)info.product);
        return 1;
    }
    struct hidraw_report_descriptor desc = {};
    ioctl(g_hidraw, HIDIOCGRDESCSIZE, &desc.size);
    ioctl(g_hidraw, HIDIOCGRDESC, &desc);
    Collections col = parse_descriptor(desc.value, desc.size);
    if (col.panel[0].count(0x0a) == 0 || col.panel[1].count(0x3c) == 0) {
        logf("descriptor: the two heat collections are not where the stock service expects them");
        return 1;
    }

    void *h = dlopen(lib, RTLD_NOW);
    if (!h) {
        logf("dlopen %s: %s", lib, dlerror());
        return 1;
    }
    if (int *sev = reinterpret_cast<int *>(dlsym(h, "g_EosTraceLoggingSeverity"))) {
        if (lib_log >= 0) {
            logf("library log severity %d -> %d", *sev, lib_log);
            *sev = lib_log;
        }
    }
    auto create = reinterpret_cast<Processor *(*)()>(dlsym(h, "CreateHeatProcessorInstance"));
    if (!create) {
        logf("no CreateHeatProcessorInstance in %s", lib);
        return 1;
    }
    Processor *proc = create();
    if (!proc) {
        logf("CreateHeatProcessorInstance failed");
        return 1;
    }

    // The two panels, as the stock service sets them up.
    g_dev_a = { 1, {}, 2, &col.panel[0] };
    g_dev_b = { 2, {}, 1, &col.panel[1] };
    EosHeatDevice &dev_a = g_dev_a, &dev_b = g_dev_b;
    g_proc = proc;
    lib_posture_init(h);

    g_fw_capture = true; // what the start sends the firmware, logged below
    // HeatService::init: InitPenTouchMw with the first panel, OnDeviceAttached
    // with the second, OnStitchingModeChange(true), then the axis range and
    // the slot count for the input device.
    slot<void (*)(Processor *, const EosHeatDevice *, const char *, Service *, uint16_t)>(
        proc, kInitPenTouchMw)(proc, &dev_a, state, &g_service, 0x0c1d);
    slot<void (*)(Processor *, const EosHeatDevice *)>(proc, kOnDeviceAttached)(proc, &dev_b);
    // Split: no stitching. Stitched (in one heat map across both panels) the
    // library needs a frame from each panel of the same scan cycle and drops
    // the rest ("ParserInvalidStitchingCycleNumber"); the firmware sends
    // those once it is told (the display-state feature report).
    slot<void (*)(Processor *, bool)>(proc, kOnStitchingModeChange)(proc, g_mode == kModeStitched);
    AxisRange range = slot<AxisRange (*)(Processor *)>(proc, kGetAxisRange)(proc);
    int slots =
        (uint8_t)slot<uint16_t (*)(Processor *)>(proc, kGetMaxConcurrentPenTouchSlots)(proc) + 1;
    if (!panels_create(range, panel_width_from_metadata(g_hidraw), slots)) return 1;
    if (g_mode == kModeStitched && !g_lib.ok())
        logf("no PostureManager in %s: geometry from what it is told", lib);

    auto set_hinge = [&](double deg) { send_hinge(deg); };
    if (g_mode == kModeStitched) {
        // HeatService::hidDeviceReady -> onDisplayParameterUpdate: the lit
        // panels as Android last said (BOTH_DISPLAYS_ON until it says
        // anything), then whatever else is known.
        pose_file_poll();
        if (hinge >= 0) g_want.hinge = hinge;
        if (g_want.display == kDisplayUnknown) g_want.display = kDisplayBoth;
        pose_apply();
        geometry_update("start");
    } else {
        if (hinge >= 0) set_hinge(hinge);
        // Both panels on.
        slot<void (*)(Processor *, const EosHeatDevice *, bool)>(proc, kOnDisplayChange)(
            proc, &dev_a, true);
        slot<void (*)(Processor *, const EosHeatDevice *, bool)>(proc, kOnDisplayChange)(
            proc, &dev_b, true);
    }
    // Split mode: heat mode and display state by hand. The library's messages
    // to the firmware, written as output reports, leave the digitizer in its
    // single-point fallback (report 0x40); feature report 5 (vendor usage
    // 0xC8) = 1 puts it in heat mode, output report 0x38 (bit 0 left, bit 1
    // right) says which panels are on. The stitched mode sends the library's
    // own messages as feature reports, as the stock service does, instead.
    auto force_mode = [&]() {
        if (!g_force_mode) return;
        const uint8_t mode[2] = { 0x05, 0x01 };
        if (ioctl(g_hidraw, HIDIOCSFEATURE(sizeof mode), mode) < 0)
            logf("set heat mode: %s", strerror(errno));
        const uint8_t on[2] = { 0x38, 0x03 };
        if (write(g_hidraw, on, sizeof on) != (ssize_t)sizeof on)
            logf("display state: %s", strerror(errno));
    };
    force_mode();
    g_fw_capture = false;
    logf("start: %lu messages to the firmware (%s), %lu failed", g_fw_messages,
         g_fw_captured[0] ? g_fw_captured + 1 : "none", g_fw_failures);
    control_open();
    // A firmware reset while running (spi-hid: "Spontaneous FW reset!") drops
    // "ready" and raises it again once the device is described anew; the
    // stock service then tells the library (HeatService::deviceReset ->
    // OnDeviceReset), which configures the digitizer again.
    int ready_fd = open(kSpiReady, O_RDONLY | O_CLOEXEC);
    bool ready = true;
    auto read_ready = [&]() {
        char r[16] = {};
        lseek(ready_fd, 0, SEEK_SET);
        return read(ready_fd, r, sizeof r - 1) > 0 && !strncmp(r, "ready", 5);
    };
    if (ready_fd >= 0) read_ready();
    logf("running: %s through %s", hidraw, lib);

    // What the digitizer sends in the first seconds after the start and after
    // each reopen, by report id, logged once: the firmware's replies to the
    // library's messages (0x2e..0x37) show that it heard them, before any
    // finger does.
    static unsigned long probe[256];
    static uint64_t probe_until = now_ms() + 3000;
    static const char *probe_what = "the start";
    auto probe_check = [&]() {
        if (!probe_until || now_ms() < probe_until) return;
        char line[512] = "";
        int o = 0;
        for (int r = 0; r < 256; r++)
            if (probe[r] && o < (int)sizeof line - 16)
                o += snprintf(line + o, sizeof line - o, " %02x:%lu", r, probe[r]);
        logf("reports in the 3 s after %s:%s", probe_what, o ? line : " none");
        probe_until = 0;
    };
    auto probe_start = [&](const char *what) {
        memset(probe, 0, sizeof probe);
        probe_what = what;
        probe_until = now_ms() + 3000;
    };

    // System sleep (digitizer_power.h): what the state machine asks for.
    static char hidraw_now[64];
    snprintf(hidraw_now, sizeof hidraw_now, "%s", hidraw);
    auto power_act = [&](DigitizerPower::Action a) {
        switch (a) {
            case DigitizerPower::kNothing: return;
            case DigitizerPower::kClose: {
                // The library keeps its state and the touch devices stay: only
                // what is down is lifted. Closing the last hidraw handle powers
                // the digitizer down (spi-hid ll_close: interrupt off, L3C off).
                panels_lift_all();
                int fd = g_hidraw;
                g_hidraw = -1;
                if (fd >= 0) close(fd);
                write_power_state("off");
                logf("digitizer off for the system sleep (hidraw closed)");
                return;
            }
            case DigitizerPower::kOpen: {
                // A re-created HID device has a new hidraw number.
                char path[64];
                if (!hidraw_given && find_hidraw(path, sizeof path))
                    snprintf(hidraw_now, sizeof hidraw_now, "%s", path);
                int fd = open(hidraw_now, O_RDWR | O_CLOEXEC);
                if (fd < 0) {
                    logf("digitizer: reopening %s: %s; restarting for a clean start", hidraw_now,
                         strerror(errno));
                    g_restart = true;
                    return;
                }
                g_hidraw = fd;
                g_ab_configured = false; // powered off and on: it has forgotten AutoBond
                write_power_state("opening");
                if (ready_fd >= 0) read_ready(); // the "ready" drop at the close, seen now
                logf("digitizer on again (%s; it was off %.1f s): %s opened, waiting for ready",
                     g_power.why() == DigitizerPower::kWhyResume
                         ? "the system is back"
                         : "no word of a resume, on again anyway",
                     g_power.last_off_ms() / 1000.0, hidraw_now);
                return;
            }
            case DigitizerPower::kConfigure: {
                // The same device as at the start (the HID id, the report
                // descriptor), or the clean restart.
                struct hidraw_devinfo now_info = {};
                static struct hidraw_report_descriptor now_desc;
                now_desc = {};
                bool same = ioctl(g_hidraw, HIDIOCGRAWINFO, &now_info) >= 0 &&
                            now_info.vendor == info.vendor && now_info.product == info.product &&
                            ioctl(g_hidraw, HIDIOCGRDESCSIZE, &now_desc.size) >= 0 &&
                            now_desc.size == desc.size &&
                            ioctl(g_hidraw, HIDIOCGRDESC, &now_desc) >= 0 &&
                            !memcmp(now_desc.value, desc.value, desc.size);
                if (!same) {
                    logf("digitizer: after the reopen it is not the device it was (HID id or "
                         "report descriptor); "
                         "restarting for a clean start");
                    g_restart = true;
                    return;
                }
                // As the stock service at a second "ready" (HeatService::deviceReset):
                // OnDeviceReset, which configures the firmware again; the split
                // mode then puts it in heat mode by hand, as at the start.
                unsigned long sent = g_fw_messages, failed = g_fw_failures;
                g_fw_captured[0] = 0;
                g_fw_capture = true;
                slot<void (*)(Processor *, const EosHeatDevice *)>(proc, kOnDeviceReset)(proc,
                                                                                         &dev_a);
                g_fw_capture = false;
                sent = g_fw_messages - sent;
                failed = g_fw_failures - failed;
                force_mode();
                ready = true;
                logf("digitizer ready %llu ms after the open (%.1f s after the word of the "
                     "resume); OnDeviceReset: "
                     "%lu messages to the firmware (%s), %lu failed",
                     (unsigned long long)g_power.last_ready_ms(),
                     g_wake_word_ms ? (now_ms() - g_wake_word_ms) / 1000.0 : -1.0, sent,
                     g_fw_captured[0] ? g_fw_captured + 1 : "none", failed);
                if (g_mode == kModeStitched && (!sent || failed)) {
                    // The firmware has to hear the library's switch-mode message
                    // (heat mode) and display state, or it stays in its
                    // single-point fallback.
                    logf("digitizer: the library did not configure it again; restarting for a "
                         "clean start");
                    g_restart = true;
                    return;
                }
                write_power_state("on");
                probe_start("the reopen");
                pose_apply(); // whatever changed while it was off
                g_ab_configured = false;
                autobond_apply();
                return;
            }
            case DigitizerPower::kRestart:
                logf("digitizer: no ready within %llu ms of the reopen; restarting for a clean "
                     "start",
                     (unsigned long long)DigitizerPower::kReadyTimeoutMs);
                g_restart = true;
                return;
        }
    };
    write_power_state("on");

    static uint8_t buf[16384];
    unsigned long frames = 0, heat_frames = 0, side_heat[2] = { 0, 0 }, single_point = 0;
    static unsigned long other_reports[256];
    while (!g_stop && !g_fallback && !g_restart) {
        if (g_swipe) {
            scripted_swipe(g_swipe == 2 ? 8 : 30);
            g_swipe = 0;
        }
        {
            static time_t last_stats = time(nullptr);
            static unsigned long last_frames = 0, last_contacts = 0, last_heat = 0,
                                 last_side[2] = { 0, 0 }, last_single = 0, last_pen = 0;
            if (time(nullptr) - last_stats >= 30) {
                char others[256] = "";
                int o = 0;
                for (int r = 0; r < 256; r++)
                    if (other_reports[r] && o < (int)sizeof others - 16) {
                        o += snprintf(others + o, sizeof others - o, " %02x:%lu", r,
                                      other_reports[r]);
                        other_reports[r] = 0;
                    }
                if (g_pen_reports != last_pen || o)
                    logf(
                        "last 30 s: %lu pen reports from the heat processor; reports not for it:%s",
                        g_pen_reports - last_pen, o ? others : " none");
                last_pen = g_pen_reports;
                if (g_pen_timing.any()) {
                    char line[640];
                    g_pen_timing.format(line, sizeof line);
                    logf("last 30 s, the pen in range: %s", line);
                }
                g_pen_timing.reset_window();
                // Heat frames are the health sign: a working digitizer sends
                // one per panel every 2 minutes even untouched; one that came
                // up half reset sends none, only heartbeats and 0x40 reports.
                if (frames != last_frames || g_contact_reports != last_contacts ||
                    single_point != last_single)
                    logf("last 30 s: %lu panel reports (%lu heat frames: %lu right, %lu left), %lu "
                         "contact reports, "
                         "%lu single-point reports; geometry %s",
                         frames - last_frames, heat_frames - last_heat, side_heat[0] - last_side[0],
                         side_heat[1] - last_side[1], g_contact_reports - last_contacts,
                         single_point - last_single, kGeomName[g_geom]);
                last_frames = frames;
                last_contacts = g_contact_reports;
                last_heat = heat_frames;
                last_side[0] = side_heat[0];
                last_side[1] = side_heat[1];
                last_single = single_point;
                last_stats = time(nullptr);
            }
        }
        // System sleep: the hook's last word (or the FIFO's), then whatever is due.
        if (int request = __atomic_exchange_n(&g_power_request, 0, __ATOMIC_SEQ_CST)) {
            PowerClock now = power_clock();
            if (request == 1) {
                power_act(g_power.sleep(now));
            } else {
                g_wake_word_ms = now.mono_ms;
                power_act(g_power.wake(now));
            }
        }
        power_act(g_power.tick(power_clock()));
        if (g_power.state() == DigitizerPower::kOpening && ready_fd >= 0 && read_ready())
            power_act(g_power.ready(power_clock()));
        if (g_restart) continue;
        probe_check();
        const bool on = g_power.state() == DigitizerPower::kOn;
        if (g_mode == kModeStitched) {
            pose_file_poll();
            pose_apply();
        }
        pen_conf_poll();
        touch_log_poll();
        autobond_poll();
        autobond_apply();
        // Requests that reach the firmware wait while the digitizer is off.
        if (on && g_hinge_request >= 0) {
            set_hinge(g_hinge_request);
            g_hinge_request = -1;
        }
        if (on && g_stitch_request >= 0) {
            // The heat processor itself turns stitching on when told the
            // hinge is flat (180 degrees), as Android does in the spanned posture.
            slot<void (*)(Processor *, bool)>(proc, kOnStitchingModeChange)(proc,
                                                                            g_stitch_request == 1);
            logf("stitching %s", g_stitch_request ? "on" : "off");
            g_stitch_request = -1;
        }
        pen_check_timeout();
        pen_keepalive();
        // hidraw only while on; "ready" while on or opening.
        struct pollfd p[4] = { { on ? g_hidraw : -1, POLLIN, 0 },
                               { g_power.state() != DigitizerPower::kOff ? ready_fd : -1,
                                 POLLPRI | POLLERR, 0 },
                               { g_control, POLLIN, 0 },
                               { g_signal_pipe[0], POLLIN, 0 } };
        // In range: awake for the next keep-alive frame (and the timeout).
        int wait_ms = pen::keepalive_wait_ms(g_pen.panel >= 0, g_pen.last_frame_ms, now_ms(), 1000);
        long power_ms = g_power.timeout_ms(power_clock());
        if (power_ms >= 0 && power_ms < wait_ms) wait_ms = (int)power_ms;
        if (g_power.state() == DigitizerPower::kOpening && wait_ms > 100) wait_ms = 100;
        if (poll(p, 4, wait_ms) <= 0) continue;
        if (p[3].revents & POLLIN) {
            char drain[16];
            while (read(g_signal_pipe[0], drain, sizeof drain) > 0) {}
        }
        if (g_control >= 0 && (p[2].revents & POLLIN)) control_read();
        if (!on) continue; // off, or opening ("ready" is looked at the top of the loop)
        if (ready_fd >= 0 && (p[1].revents & (POLLPRI | POLLERR))) {
            bool now = read_ready();
            // "ready" notified while it already read ready: it dropped and came
            // back while this process could not look (frozen for a system
            // suspend, where spi-hid's error handler may reset the digitizer),
            // which leaves it in its single-point fallback just the same.
            bool missed = now && ready;
            if (missed) {
                // OnDeviceReset right away (the stock service's way) did not
                // always take (2026-11-08: once the digitizer then sent
                // nothing for 4 minutes, its firmware still coming out of its
                // own reset); a restart (hidraw closed: powered off >= 1 s,
                // then the full start) always has.
                logf("digitizer reset while user space was frozen (suspend?): restarting for a "
                     "clean start");
                panels_lift_all();
                g_restart = true;
                continue;
            }
            if (now != ready) {
                ready = now;
                panels_lift_all();
                if (now) {
                    // the same: a clean restart, not OnDeviceReset in place
                    logf("digitizer ready again after a firmware reset: restarting for a clean "
                         "start");
                    g_restart = true;
                    continue;
                }
                logf("digitizer not ready (reset)");
            }
        }
        if (p[0].revents & (POLLERR | POLLHUP | POLLNVAL) && !(p[0].revents & POLLIN)) {
            // The HID device went away under the open node (spi-hid re-creates
            // it when the report descriptor reads back differently): poll
            // would report it forever.
            logf("hidraw: the device is gone (poll %#x); restarting for a clean start",
                 p[0].revents);
            g_restart = true;
            continue;
        }
        if (!(p[0].revents & POLLIN)) continue;
        ssize_t n = read(g_hidraw, buf, sizeof buf);
        if (n <= 0) {
            if (n < 0 && errno == EINTR) continue;
            // as above: a node whose device is gone reads EIO; the restart
            // finds the new one (exiting 0 would leave touch off)
            logf("hidraw read: %s; restarting for a clean start",
                 n < 0 ? strerror(errno) : "end of file");
            g_restart = true;
            continue;
        }
        if (probe_until) probe[buf[0]]++;
        if (g_dump) {
            static unsigned counts[256];
            static time_t last = time(nullptr);
            counts[buf[0]]++;
            if (time(nullptr) - last >= 5) {
                char line[512];
                int o = 0;
                for (int r = 0; r < 256; r++)
                    if (counts[r]) {
                        o += snprintf(line + o, sizeof line - o, " %02x:%u", r, counts[r]);
                        counts[r] = 0;
                    }
                logf("reports in the last 5 s:%s", line);
                last = time(nullptr);
            }
        }
        if (g_dump && buf[0] >= 0x2e && buf[0] <= 0x37) { // firmware messages (replies)
            char hex[3 * 48 + 1] = {};
            int o = 0;
            for (ssize_t k = 0; k < n && k < 48; k++)
                o += snprintf(hex + o, sizeof hex - o, "%02x ", buf[k]);
            logf("from touch fw: %zd bytes: %s", n, hex);
        }
        const EosHeatDevice *dev = nullptr;
        if (buf[0] == 0xfe && n > 1)
            dev = buf[1] == 1 ? &dev_a : buf[1] == 2 ? &dev_b : nullptr;
        else if (col.panel[0].count(buf[0]))
            dev = &dev_a;
        else if (col.panel[1].count(buf[0]))
            dev = &dev_b;
        if (!dev) {
            if (buf[0] == 0x40) { // the single-point fallback: the digitizer is not in heat mode
                ++single_point;
                fallback_check(buf[0], -1);
            } else {
                // Reports the library is not given (the stock service neither):
                // counted, for the stats (0x01 = the firmware's own pen report,
                // 0x22 = the pen's id, 0x6e = AutoBond, 0x2e..0x37 firmware
                // logs); the pen's said the first times (pen_raw_report).
                ++other_reports[buf[0]];
                pen_raw_report(buf, n);
            }
            continue;
        }
        slot<void (*)(Processor *, const EosHeatDevice *, uint32_t, const uint8_t *)>(
            proc, kProcessIncomingReport)(proc, dev, (uint32_t)n, buf);
        // As the stock service: after every report but heartbeats.
        if (buf[0] != 0xfe) {
            slot<void (*)(Processor *, const EosHeatDevice *)>(proc, kPostDispatchProcessing)(proc,
                                                                                              dev);
            ++heat_frames;
            ++side_heat[dev == &dev_a ? 0 : 1];
            g_pen_timing.heat_frame(g_pen.panel < 0 ? -1 : g_pen.tip ? 1 : 0);
            fallback_check(buf[0], dev == &dev_a ? 0 : 1);
            if (g_power.fresh_reopen())
                logf("digitizer: the first heat frame after the reopen, %.1f s after the word of "
                     "the resume",
                     g_wake_word_ms ? (now_ms() - g_wake_word_ms) / 1000.0 : -1.0);
            g_power.heat_frame();
        }
        ++frames;
    }
    logf("stopping");
    unlink(kPowerState);
    if (ready_fd >= 0) close(ready_fd);
    if (g_control >= 0) {
        close(g_control);
        unlink(kControl);
    }
    panels_destroy();
    // Fallen back: exit non-zero so that systemd (Restart=on-failure) starts
    // the daemon again, which then runs split (kFallbackNote).
    return g_fallback || g_restart ? 75 : 0;
}
