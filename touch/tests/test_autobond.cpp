// SPDX-License-Identifier: Apache-2.0
// Host test of duo2-touchd's AutoBond rules (autobond.h):
//   touch/tests/run.sh
#include <stdio.h>

#include "../autobond.h"

static int failures = 0;
#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            fprintf(stderr, "%s:%d: FAILED: %s\n", __FILE__, __LINE__, #cond);                     \
            failures++;                                                                            \
        }                                                                                          \
    } while (0)

using namespace autobond;

static void parsing() {
    // duo2-pend's file (placeholder address)
    State s =
        parse("# duo2-pend -> duo2-touchd\nhost 001122334455 public\nadapter on\npen-link down\n");
    CHECK(s.have_host);
    const uint8_t lsb[6] = { 0x55, 0x44, 0x33, 0x22, 0x11, 0x00 };
    CHECK(!memcmp(s.host, lsb, 6));
    CHECK(s.host_type == 0 && s.adapter_on && !s.link_up);
    s = parse("host AABBCCDDEEFF random\npen-link up\n");
    CHECK(s.have_host && s.host[0] == 0xFF && s.host[5] == 0xAA && s.host_type == 1 && s.link_up &&
          !s.adapter_on);
    // Not understood: ignored, the rest still counts.
    s = parse("host 0011223344 public\nadapter maybe\npen-link up\n");
    CHECK(!s.have_host && !s.adapter_on && s.link_up);
    s = parse("host 00112233445g public\n");
    CHECK(!s.have_host);
    s = parse("host 001122334455 other\n");
    CHECK(!s.have_host);
    s = parse("");
    CHECK(!s.have_host);
    // A host without a type: public.
    s = parse("host 001122334455\n");
    CHECK(s.have_host && s.host_type == 0);
}

static void reports() {
    State s = parse("host 001122334455 public\nadapter on\npen-link up\n");
    uint8_t c[2], h[8], l[2];
    capability_report(c);
    CHECK(c[0] == 0x70 && c[1] == 0x01);
    host_report(s, h);
    const uint8_t want[8] = { 0x56, 0x55, 0x44, 0x33, 0x22, 0x11, 0x00, 0x00 };
    CHECK(!memcmp(h, want, 8));
    link_report(s, l);
    CHECK(l[0] == 0x38 && l[1] == 0x03);
    s.link_up = false;
    link_report(s, l);
    CHECK(l[1] == 0x02);   // what stock sends with Bluetooth on and no pen link
    s.adapter_on = false;
    link_report(s, l);
    CHECK(l[1] == 0x00);
}

static void planning() {
    State none;
    State a = parse("host 001122334455 public\nadapter on\npen-link down\n");
    // No host known: nothing, even right after a power-on.
    CHECK(!plan(none, none, false).any());
    // After a power-on / reset: everything.
    Plan p = plan(a, a, false);
    CHECK(p.capability && p.host && p.link);
    // Configured, nothing changed: nothing.
    CHECK(!plan(a, a, true).any());
    // The link changes: only 0x38.
    State b = a;
    b.link_up = true;
    p = plan(b, a, true);
    CHECK(!p.capability && !p.host && p.link);
    b = a;
    b.adapter_on = false;
    p = plan(b, a, true);
    CHECK(!p.host && p.link);
    // A new host address: 0x70, 0x56 and 0x38 again.
    b = a;
    b.host[0] ^= 1;
    p = plan(b, a, true);
    CHECK(p.capability && p.host && p.link);
    // Told nothing yet (sent has no host): everything.
    p = plan(a, none, true);
    CHECK(p.capability && p.host && p.link);
}

int main() {
    parsing();
    reports();
    planning();
    if (failures) {
        fprintf(stderr, "test_autobond: %d failure(s)\n", failures);
        return 1;
    }
    printf("test_autobond: ok\n");
    return 0;
}
