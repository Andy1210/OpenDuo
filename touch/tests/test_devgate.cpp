// SPDX-License-Identifier: Apache-2.0
// Host test of duo2-touchd's contact log gate (devgate.h):
//   touch/tests/run.sh
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#include "../devgate.h"

static int failures = 0;
#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            fprintf(stderr, "%s:%d: FAILED: %s\n", __FILE__, __LINE__, #cond);                     \
            failures++;                                                                            \
        }                                                                                          \
    } while (0)

using namespace devgate;

static void conf_text() {
    CHECK(developer_mode(nullptr));  // no file: an installation from before Developer mode
    CHECK(!developer_mode(""));
    CHECK(!developer_mode("# Developer mode\nusb_network = on\n"));
    CHECK(developer_mode("developer_mode = on\n"));
    CHECK(developer_mode("developer_mode=on"));
    CHECK(developer_mode("  developer_mode =  on  # yes\r\nusb_network = off\n"));
    CHECK(!developer_mode("developer_mode = off\n"));
    CHECK(!developer_mode("developer_mode = yes\n"));  // not a valid value: off
    CHECK(!developer_mode("developer_mode = on\ndeveloper_mode = off\n"));  // the last one wins
    CHECK(developer_mode("developer_mode = off\ndeveloper_mode = on\n"));
    CHECK(developer_mode("developer_mode = on\ndeveloper_mode = maybe\n"));  // invalid: ignored
    CHECK(!developer_mode("xdeveloper_mode = on\n"));
    CHECK(!developer_mode("# developer_mode = on\n"));
}

static void files() {
    char dir[] = "/tmp/devgate-XXXXXX";
    if (!mkdtemp(dir)) {
        perror("mkdtemp");
        failures++;
        return;
    }
    char conf[256], sw[256], none[256];
    snprintf(conf, sizeof conf, "%s/duo-devmode.conf", dir);
    snprintf(sw, sizeof sw, "%s/touch-log", dir);
    snprintf(none, sizeof none, "%s/no-such-file", dir);
    uid_t me = getuid();
    auto write = [](const char *path, const char *text) {
        FILE *f = fopen(path, "w");
        fputs(text, f);
        fclose(f);
    };
    write(conf, "developer_mode = on\n");
    CHECK(!touch_log_allowed(conf, sw, me));  // no switch
    write(sw, "");
    CHECK(touch_log_allowed(conf, sw, me));
    CHECK(!touch_log_allowed(conf, sw, me + 1));  // someone else's file is no switch
    CHECK(touch_log_allowed(none, sw, me));       // no conf: from before Developer mode
    write(conf, "developer_mode = off\n");
    CHECK(!touch_log_allowed(conf, sw, me));
    unlink(sw);
    char link[256];
    snprintf(link, sizeof link, "%s/target", dir);
    write(link, "");
    CHECK(symlink(link, sw) == 0);
    write(conf, "developer_mode = on\n");
    CHECK(!touch_log_allowed(conf, sw, me));  // a link is no switch
    unlink(sw);
    unlink(link);
    unlink(conf);
    rmdir(dir);
}

int main() {
    conf_text();
    files();
    if (failures) {
        fprintf(stderr, "test_devgate: %d failed\n", failures);
        return 1;
    }
    printf("test_devgate: ok\n");
    return 0;
}
