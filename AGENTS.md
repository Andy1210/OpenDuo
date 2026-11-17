# AGENTS.md: a guide for AI coding agents

This file is for AI coding agents (Claude Code, Codex, Copilot, Cursor,
Gemini and others) working in the OpenDuo repository. Humans: the same
rules are in [CONTRIBUTING.md](CONTRIBUTING.md).

OpenDuo is Debian 13 on a mainline Linux kernel for the Microsoft Surface
Duo 2 (SM8350), with its own dual-screen compositor, shell and Settings
([README.md](README.md)). **The code runs on a real phone that someone may
use for calls.** The device-safety rules below are not style advice: some
mistakes on this phone cannot be undone.

## Repository layout

| Path | What | Language |
|---|---|---|
| `compositor/duo-wc/` | the Wayland compositor (Smithay): two panels, spanning, postures, laptop mode, XWayland | Rust (MIT) |
| `shell/duo-shell/` | the shell: bars, home, app grid, quick menu, lock screen, notifications | Python, GTK 4 |
| `shell/duo-settings/` | Duo Settings and its root helpers | Python, GTK 4, libadwaita |
| `shell/duo-screencast/`, `shell/duo-apps/` | screen-cast portal glue; app set-up and fixes | Python, shell |
| `touch/` | `duo2-touchd`: Microsoft's heat-map library driven on Android's runtime, the pen | C++ (Apache-2.0), Python tools |
| `sensors/` | postures (`duo-posed`), automatic brightness, patches for hexagonrpc / libssc / diag | Python, patches |
| `power/` | automatic sleep, wake sources, charge hold, RTC, torch | Python, shell, udev/systemd |
| `telephony/` | modem, calls audio, SMS, mobile data, eSIM (lpac), VoLTE trial (81voltd) | Python, shell, patches |
| `hotspot/`, `firewall/`, `devmode/`, `homecrypt/`, `maint/`, `battery/`, `android/`, `video/`, `camera/` | hotspot, nftables firewall, developer mode and bug reports, home encryption, updates and backups, battery statistics, Waydroid, browser video, libcamera packages | Python, shell |
| `apps/nibfair/` | pen games | Rust, C (MIT) |
| `rootfs/` | the image recipe: `Dockerfile`, `overlay/` (files copied into the root), `install-components.sh`, the boot splash | Dockerfile, shell |
| `rescue/init` | the initramfs init (boot, early display, USB rescue shell) | shell |
| `kernel/patches/` | the kernel series on top of Linux v7.3.4, with [SERIES.md](kernel/patches/SERIES.md) | patches (GPL-2.0) |
| `kernel/config/`, `kernel/drafts/` | the config fragment; patches not applied yet | |
| `scripts/` | build scripts, firmware extraction, the phone-driving tools (`lib/remote.sh`, `duo2-try`, `duo2-promote`, `duo2-*` tests), `check-personal-data`, `lint-services` | shell, Python |
| `build/` | the `duo2-build` container (kernel, firmware tools) | Dockerfile |
| `docs/` | install, restore, updates, devices proposal; `docs/hardware/` = subsystem notes | Markdown |

Never committed (ignored by git): `out/` (all build output), `src/` (the
kernel tree and other sources), `firmware/` (stock images and the user's
partition backups), `dumps/`, `backups/`, `local.env`, `local.personal-data`.

Naming: files, services and tools are prefixed `duo2-` (device) or `duo-`
(OpenDuo's own UI); the system is called OpenDuo in user-facing text.

## Commands

From the repository root. Host tests need no phone and no root.

```sh
# Python components (each has tests/ and its own ruff.toml)
cd <component> && python3 -m unittest discover -s tests
#   components: android battery devmode homecrypt hotspot maint power scripts
#               telephony shell/duo-settings shell/duo-shell shell/duo-screencast
cd sensors/duo-posed && python3 -m unittest discover -p 'test_*.py'        # also sensors/duo-brightness
touch/tests/run.sh                          # C++ host tests + Python tests of the touch tools

# Lint: ruff (format + check), shellcheck, clang-format; LINT_BIN = a venv's bin
# (ruff==0.17.5 shellcheck-py==0.11.0.1 clang-format==23.1.5)
scripts/lint-services [--fix] <component>...

# Rust (in Docker; cargo is not needed on the host)
compositor/duo-wc/build-aarch64.sh [build|test|check]      # check = rustfmt + clippy -D warnings
apps/nibfair/build-aarch64.sh [build|test]

# GTK pages and tiles rendered in a container
shell/duo-settings/tests/run-gtk.sh tests/gtk_<page>.py
shell/duo-shell/tests/run-gtk.sh tests/gtk_quick_tiles.py

# Personal data in what you are about to commit
scripts/check-personal-data                 # staged changes; --all for the whole tree

# Kernel (needs src/linux and the duo2-build image, see CONTRIBUTING.md)
scripts/build-kernel.sh [config|dtbs|all]
<kernel tree>/scripts/checkpatch.pl --strict <patch>

# The image (heavy: an hour or more, partly arm64 under QEMU)
scripts/build-images.sh
```

Installing onto a phone: each component's `install.sh` (over SSH, through
`scripts/lib/remote.sh`, which reads `local.env`). Kernels: see the safety
rules.

## Code standards

Summary; the details are in [CONTRIBUTING.md](CONTRIBUTING.md#code-standards).

- **English only: docs, comments, commit messages, log text.**
- Match the surrounding code. Comments say why, in plain English.
- Python: ruff with the component's `ruff.toml`; `unittest`; Debian's
  Python 3.13 on the phone, no pip dependencies there.
- Shell: `set -eu` (bash: `set -euo pipefail`), shellcheck clean; secrets on
  stdin, never on the command line.
- C/C++: `clang-format` with `touch/.clang-format`; `-Wall -Wextra -Werror`.
- Rust: `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`,
  `--locked`.
- Kernel patches: upstream style, checkpatch `--strict`, `BRING-UP:` prefix
  for device-local hacks, `Assisted-by: AGENT:MODEL`. **Never add a
  `Signed-off-by:` line**: only the human author can certify the DCO.
- Docs: English. A status claims only what was tested on a real phone; never
  round a status up, and say "not tested" when it was not.

## Device safety rules

These apply whenever you can reach a phone (SSH, adb, fastboot, a USB
cable), and also to commands you suggest to a human.

1. **Never write the modem's partitions, NV items or EFS.** No writes to
   `modemst1`, `modemst2`, `fsg`, `fsc` or `persist`; no QMI NV or EFS
   writes; no carrier configuration (PDC) loads or activations; no changes
   to IMEI-related data. Reading is allowed. Anything that changes modem
   state persistently needs the human's explicit decision first.
2. **No I2C probing or raw bus access**: no `i2cdetect`, `i2cget`,
   `i2cset`, `i2ctransfer`, and no raw SPI access. Battery gauges, PMICs,
   camera modules and the pen charger sit on these buses.
3. **No PMIC or GPIO writes from user space**: no `gpioset`, no writes into
   `/sys/kernel/debug/regmap/*`, no `devmem`, no poking PMIC registers.
   Hardware changes go into a driver or device-tree patch that a human
   reviews and tries with `duo2-try`.
4. **Kernels only through `scripts/duo2-try`** (one boot with systemd-boot's
   boot counting; a failure falls back to the known-good kernel by itself)
   and **`scripts/duo2-promote`** only after the try has proved itself and
   the human agrees. **Never edit boot entries, `loader.conf` or anything on
   the ESP by hand**, and never write a boot partition.
5. **Never write a partition** (`dd` to `/dev/block/*` or `/dev/disk/*`,
   `fastboot flash`, `fastboot erase`, `parted`, `mkfs`) and never run
   `fastboot flashing lock/unlock`. Those steps belong to a human following
   docs/INSTALL.md or docs/RESTORE.md.
6. **Mask identifiers in everything you print, log, commit or report**:
   IMEI, IMSI, ICCID, EID, serial numbers, MAC and Bluetooth addresses,
   phone numbers, network and operator names, IP addresses outside
   OpenDuo's own subnets, account names, coordinates. Show at most the
   first and last two characters (`scripts/check-personal-data` masks this
   way).
7. **No vendor files or personal data in git**: no firmware, libraries or
   binaries from the stock images, no camera tables, no sensor registry, no
   partition dumps, nothing from `out/`, `src/`, `firmware/`, `dumps/`; no
   host names, home paths, network names or keys. Run
   `scripts/check-personal-data` on the staged changes before every commit.
8. **Cap heavy runs on the phone with a memory limit**, for example
   `systemd-run --user --scope -p MemoryMax=500M ...` (or `sudo systemd-run
   --scope -p MemoryMax=1G ...`). The phone has no swap; an off-screen
   benchmark once ran it out of memory.
9. **Leave the phone as you found it.** Restore every setting you change
   (read it before you change it). While you work remotely, keep the phone
   awake with a timed hold (`sudo duo2-autosleep-switch hold 2h`), never with
   a permanent switch you might forget; do not leave it asleep, locked out
   or off the network. Do not stop the modem, `rmtfs` or the compositor
   unless the task is about them. Reboot only when needed (only a cold
   restart works on this UEFI).
10. **Only kill your own processes**, by their PID.
11. When a step would be irreversible, or you are not sure it is safe:
    **stop and ask the human.**

## Pull requests

- One topic per pull request; small is better. Do not push to `main`.
- Fill in the pull request template: **the AI disclosure** (the tool and the
  model, and what the AI did) and **the self-review checklist** (host tests,
  lint, how it was tested on a real Surface Duo 2 or that it was not,
  `check-personal-data`, no blobs, docs updated).
- Commit subjects as in the history: `component: what changed`; the body
  says why and what was tested. Mark AI help with a trailer
  (`Assisted-by:` or `Co-Authored-By:`, per your tool's convention; kernel
  patches: `Assisted-by: AGENT:MODEL`).
- Update the component's README, CHANGELOG.md ("Unreleased") and, if the
  hardware status changed, the README's table, in the same pull request.
- Never claim something works that was not tested; say what was tested and
  how.
