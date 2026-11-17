// SPDX-License-Identifier: Apache-2.0
// duo2-touchd's AutoBond side: the pure rules (no I/O), host-tested (touch/tests).
//
// The stock touch-pen HAL (vendor.surface.touchpen@1.3-service,
// AutoBondDevice; the stock TouchPenService drives it) keeps three things on
// the digitizer for the Surface pen, all in the digitizer's AutoBond
// collection (vendor page 0xFFF4):
//  - feature 0x70 = 01: AutoBond on (the digitizer reports 02, "capable");
//  - feature 0x56 = the host's LE address, LSB first, and its type (0 public,
//    1 random): 8 bytes with the id. The pen learns the host through the
//    digitizer's uplink when it is near the screen; its report 0x6e (a pen in
//    range: its id, its address, whether it is paired with this host) needs it;
//  - output 0x38 = bit 0 the pen's Bluetooth link is up, bit 1 Bluetooth is on.
// The digitizer forgets them whenever it is powered off (every system sleep),
// so they are sent again each time it is configured, and whenever the state
// changes. The state comes from duo2-pend (/run/duo2-pen/autobond: "host
// <12 hex digits, MSB first> public|random", "adapter on|off",
// "pen-link up|down"). Without a host line nothing is sent at all.
// (Stock also clears the address, FF:FF:FF:FF:FF:FF, while the screen is off;
// here the phone sleeps soon after the screen goes dark and the digitizer
// is off then anyway, and touchd does not learn "dark" from duo-posed.)
#pragma once

#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace autobond {

const uint8_t kReportCapability = 0x70;   // feature
const uint8_t kReportHost = 0x56;         // feature
const uint8_t kReportLink = 0x38;         // output

struct State {
    bool have_host = false;
    uint8_t host[6] = {};     // LSB first, as the report carries it
    uint8_t host_type = 0;    // 0 public, 1 random
    bool adapter_on = false;
    bool link_up = false;
    bool operator==(const State &o) const {
        return have_host == o.have_host && !memcmp(host, o.host, 6) && host_type == o.host_type &&
               adapter_on == o.adapter_on && link_up == o.link_up;
    }
    bool operator!=(const State &o) const { return !(*this == o); }
};

inline int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

// One line of the state file; false if not understood (comments and empty
// lines are the caller's).
inline bool parse_line(const char *line, State &s) {
    char a[32], b[16];
    int n = sscanf(line, "%31s %15s", a, b);
    if (n < 1) return false;
    if (!strcmp(a, "host")) {
        char hex[16], type[16] = "public";
        if (sscanf(line, "host %15s %15s", hex, type) < 1 || strlen(hex) != 12) return false;
        uint8_t msb[6];
        for (int i = 0; i < 6; i++) {
            int hi = hexval(hex[2 * i]), lo = hexval(hex[2 * i + 1]);
            if (hi < 0 || lo < 0) return false;
            msb[i] = (uint8_t)(hi << 4 | lo);
        }
        if (strcmp(type, "public") && strcmp(type, "random")) return false;
        for (int i = 0; i < 6; i++) s.host[i] = msb[5 - i];
        s.host_type = !strcmp(type, "random");
        s.have_host = true;
        return true;
    }
    if (n < 2) return false;
    if (!strcmp(a, "adapter") && (!strcmp(b, "on") || !strcmp(b, "off"))) {
        s.adapter_on = !strcmp(b, "on");
        return true;
    }
    if (!strcmp(a, "pen-link") && (!strcmp(b, "up") || !strcmp(b, "down"))) {
        s.link_up = !strcmp(b, "up");
        return true;
    }
    return false;
}

// The whole file: a missing line keeps its default (no host: send nothing).
inline State parse(const char *text) {
    State s;
    const char *p = text;
    while (p && *p) {
        const char *e = strchr(p, '\n');
        size_t len = e ? (size_t)(e - p) : strlen(p);
        char line[128];
        if (len >= sizeof line) len = sizeof line - 1;
        memcpy(line, p, len);
        line[len] = 0;
        if (line[0] && line[0] != '#') parse_line(line, s);
        p = e ? e + 1 : nullptr;
    }
    return s;
}

inline void capability_report(uint8_t out[2]) {
    out[0] = kReportCapability;
    out[1] = 0x01;
}

inline void host_report(const State &s, uint8_t out[8]) {
    out[0] = kReportHost;
    memcpy(out + 1, s.host, 6);
    out[7] = s.host_type;
}

inline void link_report(const State &s, uint8_t out[2]) {
    out[0] = kReportLink;
    out[1] = (uint8_t)((s.link_up ? 1 : 0) | (s.adapter_on ? 2 : 0));
}

// What to send now, given what the digitizer was last told (`sent`, valid
// only if `configured`: false after every power-on / reset of the
// digitizer). The capability and the host go together (stock: AutoBond on,
// then the address); the link status on its own.
struct Plan {
    bool capability = false, host = false, link = false;
    bool any() const { return capability || host || link; }
};

inline Plan plan(const State &want, const State &sent, bool configured) {
    Plan p;
    if (!want.have_host) return p;
    if (!configured || !sent.have_host || memcmp(want.host, sent.host, 6) ||
        want.host_type != sent.host_type)
        p.capability = p.host = true;
    if (!configured || p.host || want.adapter_on != sent.adapter_on || want.link_up != sent.link_up)
        p.link = true;
    return p;
}

}  // namespace autobond
