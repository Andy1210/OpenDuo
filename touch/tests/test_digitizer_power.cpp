// SPDX-License-Identifier: Apache-2.0
// Host test of the digitizer's power across system sleep (digitizer_power.h):
//   touch/tests/run.sh
#include <stdio.h>

#include "../digitizer_power.h"

static int failures = 0;
#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            fprintf(stderr, "%s:%d: FAILED: %s\n", __FILE__, __LINE__, #cond);                     \
            failures++;                                                                            \
        }                                                                                          \
    } while (0)

using P = DigitizerPower;

// A clock: awake time moves both, sleep only BOOTTIME.
struct Clock {
    PowerClock c{ 100000, 100000 };
    PowerClock awake(uint64_t ms) {
        c.mono_ms += ms;
        c.boot_ms += ms;
        return c;
    }
    PowerClock asleep(uint64_t ms) {
        c.boot_ms += ms;
        return c;
    }
    PowerClock now() const { return c; }
};

static void suspend_and_resume() {
    P p;
    Clock t;
    CHECK(p.state() == P::kOn);
    CHECK(p.timeout_ms(t.now()) == -1);
    CHECK(p.tick(t.now()) == P::kNothing);
    CHECK(p.sleep(t.now()) == P::kClose);           // the hook, before the suspend
    CHECK(p.state() == P::kOff);
    t.awake(300);                                   // the freeze
    CHECK(p.tick(t.now()) == P::kNothing);
    t.asleep(600000);                               // ten minutes asleep
    t.awake(3500);                                  // the devices' resume (the WiFi)
    CHECK(p.wake(t.now()) == P::kOpen);             // the hook, after the resume
    CHECK(p.state() == P::kOpening);
    CHECK(p.why() == P::kWhyResume);
    CHECK(p.last_off_ms() == 603800);
    CHECK(p.timeout_ms(t.now()) == (long)P::kReadyTimeoutMs);
    t.awake(250);
    CHECK(p.tick(t.now()) == P::kNothing);
    CHECK(p.ready(t.now()) == P::kConfigure);
    CHECK(p.state() == P::kOn);
    CHECK(p.last_ready_ms() == 250);
    CHECK(p.fresh_reopen());
    p.heat_frame();
    CHECK(!p.fresh_reopen());
    CHECK(p.closes() == 1 && p.reopens() == 1);
    // the same once more: the counters move
    CHECK(p.sleep(t.awake(60000)) == P::kClose);
    CHECK(p.wake(t.asleep(1000)) == P::kOpen);
    CHECK(p.ready(t.awake(100)) == P::kConfigure);
    CHECK(p.closes() == 2 && p.reopens() == 2);
}

// A suspend that failed at once: the word that the system is back comes
// right after the close; the digitizer stays off for a second first.
static void aborted_suspend_keeps_it_off_a_second() {
    P p;
    Clock t;
    CHECK(p.sleep(t.now()) == P::kClose);
    CHECK(p.wake(t.awake(100)) == P::kNothing);
    CHECK(p.wake_pending());
    CHECK(p.timeout_ms(t.now()) == 900);
    CHECK(p.tick(t.awake(500)) == P::kNothing);
    CHECK(p.timeout_ms(t.now()) == 400);
    CHECK(p.tick(t.awake(399)) == P::kNothing);
    CHECK(p.tick(t.awake(1)) == P::kOpen);
    CHECK(p.why() == P::kWhyResume);
    CHECK(p.last_off_ms() == 1000);
}

// No word of a resume: on again after 20 s awake; asleep, the time does not count.
static void no_resume_word() {
    P p;
    Clock t;
    CHECK(p.sleep(t.now()) == P::kClose);
    CHECK(p.timeout_ms(t.now()) == (long)P::kNoResumeMs);
    CHECK(p.tick(t.asleep(3600000)) == P::kNothing);   // an hour asleep: CLOCK_MONOTONIC stood
    CHECK(p.tick(t.awake(19999)) == P::kNothing);
    CHECK(p.timeout_ms(t.now()) == 1);
    CHECK(p.tick(t.awake(1)) == P::kOpen);
    CHECK(p.why() == P::kWhyNoResume);
    CHECK(p.ready(t.awake(10)) == P::kConfigure);
}

static void ready_timeout_restarts() {
    P p;
    Clock t;
    p.sleep(t.now());
    t.asleep(5000);
    CHECK(p.wake(t.awake(10)) == P::kOpen);
    CHECK(p.tick(t.awake(2999)) == P::kNothing);
    CHECK(p.timeout_ms(t.now()) == 1);
    CHECK(p.tick(t.awake(1)) == P::kRestart);
}

// The next suspend while it is still opening (a short modem wake): closed again,
// and a late "ready" changes nothing.
static void sleep_while_opening() {
    P p;
    Clock t;
    p.sleep(t.now());
    t.asleep(5000);
    CHECK(p.wake(t.awake(10)) == P::kOpen);
    CHECK(p.sleep(t.awake(50)) == P::kClose);
    CHECK(p.state() == P::kOff);
    CHECK(p.ready(t.awake(10)) == P::kNothing);
    CHECK(p.state() == P::kOff);
    CHECK(p.closes() == 2 && p.reopens() == 0);
    // it was on for 50 ms: the next reopen keeps it off a second again
    // (10 ms since the close at the late "ready")
    CHECK(p.wake(t.awake(100)) == P::kNothing);
    CHECK(p.tick(t.awake(880)) == P::kNothing);
    CHECK(p.tick(t.awake(10)) == P::kOpen);
}

// A second word to sleep while off: nothing to close; a reopen still pending
// is called off, and the 20 s count starts again.
static void sleep_while_off() {
    P p;
    Clock t;
    p.sleep(t.now());
    CHECK(p.wake(t.awake(100)) == P::kNothing);     // pending (off for less than a second)
    CHECK(p.sleep(t.awake(100)) == P::kNothing);
    CHECK(!p.wake_pending());
    CHECK(p.state() == P::kOff);
    CHECK(p.tick(t.awake(5000)) == P::kNothing);    // no reopen: the system is going to sleep
    CHECK(p.tick(t.awake(14999)) == P::kNothing);   // 19.999 s since the second word
    CHECK(p.tick(t.awake(1)) == P::kOpen);
    CHECK(p.closes() == 1);
}

static void words_out_of_place() {
    P p;
    Clock t;
    CHECK(p.wake(t.now()) == P::kNothing);          // on: a resume changes nothing
    CHECK(p.ready(t.now()) == P::kNothing);
    CHECK(!p.fresh_reopen());
    CHECK(p.state() == P::kOn);
    p.sleep(t.now());
    t.asleep(10000);
    p.wake(t.awake(1));
    CHECK(p.wake(t.awake(1)) == P::kNothing); // opening: a second resume word changes nothing
    CHECK(p.state() == P::kOpening);
    p.ready(t.awake(1));
    CHECK(p.fresh_reopen());
    // closed again before any heat frame: no longer a fresh reopen
    p.sleep(t.awake(1));
    CHECK(!p.fresh_reopen());
}

int main() {
    suspend_and_resume();
    aborted_suspend_keeps_it_off_a_second();
    no_resume_word();
    ready_timeout_restarts();
    sleep_while_opening();
    sleep_while_off();
    words_out_of_place();
    if (failures) {
        fprintf(stderr, "test_digitizer_power: %d failed\n", failures);
        return 1;
    }
    printf("test_digitizer_power: ok\n");
    return 0;
}
