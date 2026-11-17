// SPDX-License-Identifier: Apache-2.0
// duo2-touchd: the digitizer across a system suspend. Pure logic (no I/O, no
// clock of its own): duo2-touchd.cpp drives it, touch/tests runs it on the
// build host.
//
// The stock touch HAL powers the digitizer off with the screens
// (HidrawDevice::powerControl: hidraw closed, and spi-hid powers the device
// down) and, opened again, tells the library OnDeviceReset at the next
// "ready". duo2-touchd keeps it on while the system is awake (a touch on a
// dark panel wakes the panels: duo-wc's touch_wake) and powers it off across
// a system suspend only: the system-sleep hook
// (/usr/lib/systemd/system-sleep/duo2-touchd) tells it before the suspend and
// after the resume. Powered while the system slept, the digitizer drew ~95 mA
// of the ~150 mA sleep floor and reset itself, so every wake ended in a
// restart of the daemon (docs/hardware/touch.md).
#pragma once

#include <stdint.h>

struct PowerClock {
    uint64_t mono_ms;   // CLOCK_MONOTONIC: stands still while the system sleeps
    uint64_t boot_ms;   // CLOCK_BOOTTIME: counts the sleep
};

class DigitizerPower {
public:
    enum State { kOn, kOff, kOpening };
    // What the caller does: kClose (contacts lifted, hidraw closed), kOpen
    // (hidraw found and opened; when that fails, the clean restart), kConfigure
    // (spi-hid said "ready": OnDeviceReset and the mode, as after a firmware
    // reset), kRestart (exit for systemd's clean restart, which always works).
    enum Action { kNothing, kClose, kOpen, kConfigure, kRestart };
    enum Why { kWhyNone, kWhyResume, kWhyNoResume };

    // Off at least this long (CLOCK_BOOTTIME, so the sleep counts) before it is
    // powered again: back on within ~160 ms it came up half reset, in its
    // single-point fallback (docs/hardware/touch.md; the service's
    // ExecStartPre=/bin/sleep 1 is the same rule).
    static constexpr uint64_t kMinOffMs = 1000;
    // Off for this long awake (CLOCK_MONOTONIC) without the word that the
    // system is back: the resume's message got lost, or no suspend followed
    // the one before it. On again anyway.
    static constexpr uint64_t kNoResumeMs = 20000;
    // Opened, and no "ready" within this: give up on the reopen (the restart).
    static constexpr uint64_t kReadyTimeoutMs = 3000;

    State state() const { return state_; }
    Why why() const { return why_; }

    // The system goes to sleep. On or opening: close it. Already off: nothing
    // (a reopen still pending is called off; the digitizer stays off).
    Action sleep(PowerClock now) {
        sleep_mono_ = now.mono_ms;
        wake_pending_ = false;
        if (state_ == kOff) return kNothing;
        state_ = kOff;
        off_boot_ = now.boot_ms;
        closes_++;
        fresh_reopen_ = false;
        return kClose;
    }

    // The system is back: open it again (at once, or once it has been off for
    // kMinOffMs). On or opening already: nothing.
    Action wake(PowerClock now) {
        if (state_ != kOff) return kNothing;
        wake_pending_ = true;
        return tick(now);
    }

    // Time has passed: whatever is due.
    Action tick(PowerClock now) {
        switch (state_) {
            case kOn: return kNothing;
            case kOff:
                if (!wake_pending_ && now.mono_ms - sleep_mono_ < kNoResumeMs) return kNothing;
                if (now.boot_ms - off_boot_ < kMinOffMs) return kNothing;
                why_ = wake_pending_ ? kWhyResume : kWhyNoResume;
                wake_pending_ = false;
                state_ = kOpening;
                open_mono_ = now.mono_ms;
                last_off_ms_ = now.boot_ms - off_boot_;
                return kOpen;
            case kOpening:
                if (now.mono_ms - open_mono_ >= kReadyTimeoutMs) return kRestart;
                return kNothing;
        }
        return kNothing;
    }

    // spi-hid says "ready". Opening: configure it, and it is on.
    Action ready(PowerClock now) {
        if (state_ != kOpening) return kNothing;
        state_ = kOn;
        reopens_++;
        last_ready_ms_ = now.mono_ms - open_mono_;
        fresh_reopen_ = true;
        return kConfigure;
    }

    // A heat frame came: the digitizer took its mode again.
    void heat_frame() { fresh_reopen_ = false; }
    // On again after a reopen, and no heat frame yet: if the digitizer now
    // sends its single-point report, the reopen did not take (the clean
    // restart), it is not the stitched mode failing (no fallback counted).
    bool fresh_reopen() const { return state_ == kOn && fresh_reopen_; }

    // How long the caller may wait before tick() has something to do; -1:
    // nothing is due until sleep() or wake().
    long timeout_ms(PowerClock now) const {
        switch (state_) {
            case kOn: return -1;
            case kOff: {
                long min_off = remaining(now.boot_ms - off_boot_, kMinOffMs);
                if (wake_pending_) return min_off;
                long no_resume = remaining(now.mono_ms - sleep_mono_, kNoResumeMs);
                return no_resume > min_off ? no_resume : min_off;
            }
            case kOpening: return remaining(now.mono_ms - open_mono_, kReadyTimeoutMs);
        }
        return -1;
    }

    unsigned long closes() const { return closes_; }
    unsigned long reopens() const { return reopens_; }
    uint64_t last_off_ms() const { return last_off_ms_; } // how long it was off (CLOCK_BOOTTIME)
    uint64_t last_ready_ms() const { return last_ready_ms_; } // open -> "ready"
    bool wake_pending() const { return wake_pending_; }

private:
    static long remaining(uint64_t elapsed, uint64_t limit) {
        return elapsed >= limit ? 0 : (long)(limit - elapsed);
    }

    State state_ = kOn;
    Why why_ = kWhyNone;
    bool wake_pending_ = false;
    bool fresh_reopen_ = false;
    uint64_t sleep_mono_ = 0, off_boot_ = 0, open_mono_ = 0;
    uint64_t last_off_ms_ = 0, last_ready_ms_ = 0;
    unsigned long closes_ = 0, reopens_ = 0;
};
