# Pen charger draft (2026-11-16): NOT TRIED, NOT ON THE PHONE

The Surface Duo 2's wireless power transmitter for the Slim Pen (used with the
Surface Duo 2 Pen Cover): TLMM GPIO 39, low = on. What the stock kernel and
vendor daemon do with it, and why this is safe to wire up: docs/hardware/touch.md.

- `0001-arm64-dts-qcom-sm8350-microsoft-surface-duo2-Add-the.patch`: a
  `regulator-fixed` on GPIO 39 (active low, off at boot: the level the boot
  loader leaves) and a `regulator-output` connector as its consumer, so the
  transmitter appears as `/sys/devices/platform/pen-charger/state`
  (`enabled` / `disabled`). Branch `duo2-pen` (worktree `src/linux-pen`, one
  commit on `duo2` 3ba7db140a11).
- `pen-charger.config`: `CONFIG_REGULATOR_USERSPACE_CONSUMER=m` (the
  connector's driver, mainline `drivers/regulator/userspace-consumer.c`;
  not in our config yet).

Checked on the build machine: the DTBs build (`SRC=src/linux-pen O=out/linux-pen
EXTRA_CONFIG=kernel/drafts/pen-charger/pen-charger.config scripts/build-kernel.sh dtbs`),
`dt-validate` reports nothing for the new nodes, and the module compiles
(its alias is `of:N*T*Cregulator-output`).

The policy is userspace's: power/duo2-pen-charger (the stock rule) and the
Duo Settings > Pen switch "Charge pen from phone" (off by default). Without
this kernel part both stay inert: the tool says `unsupported`, the switch is
hidden.

To try: a full build of `src/linux-pen` with the
fragment, then `duo2-try`; at boot GPIO 39 must still read `out high`
(`sudo cat /sys/kernel/debug/gpio`), and `cat
/sys/devices/platform/pen-charger/state` must say `disabled`. Nothing turns the
transmitter on until someone switches it on in Settings.
