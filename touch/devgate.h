// SPDX-License-Identifier: Apache-2.0
// duo2-touchd's contact log gate: the pure rules (no I/O but the two reads in
// touch_log_allowed), host-tested (touch/tests).
//
// The contact log ("log on" on the control FIFO, --log-contacts) writes every
// touch's position to the journal: from it the PIN typed on the lock screen
// could be read back. So it is a developer's tool (devmode/README.md, Settings >
// Developer > "Log touch positions"), and it runs only while
//  - Developer mode is on (/etc/duo-devmode.conf, developer_mode: the last
//    valid line wins, a file without one means off; no file at all is an
//    installation from before Developer mode, which counts as on, as in
//    devmode/duodevmode/conf.py), and
//  - its own switch is on: /run/duo-devmode/touch-log exists (root's, on a
//    tmpfs: gone after a reboot). duo-devmode makes and removes it, and turns
//    it off with Developer mode.
// The daemon checks both when the log is asked for and every few seconds
// while it runs, and stops logging as soon as either is off.
#pragma once

#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>

namespace devgate {

const char kDevmodeConf[] = "/etc/duo-devmode.conf";
const char kTouchLogSwitch[] = "/run/duo-devmode/touch-log";

// developer_mode from the conf file's text; nullptr = there is no file.
inline bool developer_mode(const char *text) {
    if (!text) return true;
    bool on = false;
    const char *line = text;
    while (*line) {
        const char *end = strchr(line, '\n');
        size_t len = end ? (size_t)(end - line) : strlen(line);
        char buf[256];
        if (len < sizeof buf) {
            memcpy(buf, line, len);
            buf[len] = 0;
            if (char *hash = strchr(buf, '#')) *hash = 0;
            char *eq = strchr(buf, '=');
            if (eq) {
                *eq = 0;
                char *key = buf, *value = eq + 1;
                auto trim = [](char *s) {
                    while (*s == ' ' || *s == '\t' || *s == '\r') s++;
                    char *e = s + strlen(s);
                    while (e > s && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\r')) *--e = 0;
                    return s;
                };
                key = trim(key);
                value = trim(value);
                if (!strcmp(key, "developer_mode")) {
                    if (!strcmp(value, "on"))
                        on = true;
                    else if (!strcmp(value, "off"))
                        on = false;
                }
            }
        }
        if (!end) break;
        line = end + 1;
    }
    return on;
}

// Both switches, from the files (the paths and the switch's owner are
// parameters for the tests).
inline bool touch_log_allowed(const char *conf = kDevmodeConf,
                              const char *switch_path = kTouchLogSwitch, uid_t owner = 0) {
    struct stat st;
    // root's file only: a link or someone else's file there is no switch
    if (lstat(switch_path, &st) != 0 || !S_ISREG(st.st_mode) || st.st_uid != owner) return false;
    FILE *f = fopen(conf, "r");
    if (!f) return developer_mode(nullptr);
    static char text[4096];
    size_t n = fread(text, 1, sizeof text - 1, f);
    text[n] = 0;
    fclose(f);
    return developer_mode(text);
}

}  // namespace devgate
