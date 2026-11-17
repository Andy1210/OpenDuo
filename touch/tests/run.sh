#!/bin/sh
# duo2-touchd's host tests: the digitizer's power across system sleep (the
# daemon's state machine, built with the host compiler), the pen's rules
# (pen_rules.h: flags, pressure, mapping, palm rejection, its settings file),
# AutoBond (autobond.h: duo2-pend's state file, the digitizer's reports),
# the contact log's gate (devgate.h: Developer mode and its own switch),
# the sleep hook and pen-fw (the pen's firmware tool: parsers, packets, the
# CFU transaction against a fake pen, a test-signed catalog).
#   touch/tests/run.sh
set -eu
here=$(cd "$(dirname "$0")" && pwd)
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
${CXX:-c++} -std=gnu++17 -O2 -Wall -Wextra -Werror -o "$out/test_digitizer_power" "$here/test_digitizer_power.cpp"
"$out/test_digitizer_power"
${CXX:-c++} -std=gnu++17 -O2 -Wall -Wextra -Werror -o "$out/test_pen_rules" "$here/test_pen_rules.cpp"
"$out/test_pen_rules"
${CXX:-c++} -std=gnu++17 -O2 -Wall -Wextra -Werror -o "$out/test_autobond" "$here/test_autobond.cpp"
"$out/test_autobond"
${CXX:-c++} -std=gnu++17 -O2 -Wall -Wextra -Werror -o "$out/test_devgate" "$here/test_devgate.cpp"
"$out/test_devgate"
python3 -I -B -m unittest discover -s "$here" -p 'test_*.py'
