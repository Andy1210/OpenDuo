# Contributing to OpenDuo

Thank you for helping. OpenDuo runs on a phone that people use for calls,
so changes are judged by one question first: **was it tested on a real
Surface Duo 2, and how?** A change that could only be tested on a computer
is welcome too; say so plainly.

Before you start on something big, open an issue to talk about it. Small,
single-topic pull requests are reviewed fastest.

If you use an AI coding agent, it must follow [AGENTS.md](AGENTS.md).

## Contents

- [Setting up](#setting-up)
- [Building and testing each component](#building-and-testing-each-component)
- [Code standards](#code-standards)
- [Commits and pull requests](#commits-and-pull-requests)
- [AI use: disclose it](#ai-use-disclose-it)
- [Self-review checklist](#self-review-checklist)
- [Device safety](#device-safety)
- [Personal data and vendor files](#personal-data-and-vendor-files)
- [Licensing of contributions](#licensing-of-contributions)

## Setting up

**Host:** Linux on x86_64 with `git`, `python3`, Docker (with arm64
emulation for the root file system: `qemu-user-static` and
`binfmt-support`), and `adb` / `fastboot` if you install images. Most builds
run in Docker containers defined in the repository, so the host needs few
packages of its own.

**Linters,** pinned to the versions CI uses, in a virtual environment under
`out/` (ignored by git):

```sh
python3 -m venv out/lint
out/lint/bin/pip install ruff==0.17.5 shellcheck-py==0.11.0.1 clang-format==23.1.5
export LINT_BIN=$PWD/out/lint/bin          # scripts/lint-services takes the tools from here
```

**A phone,** for anything that touches the device: OpenDuo installed
([docs/INSTALL.md](docs/INSTALL.md)), Settings > Developer > Developer mode
and SSH on, your key added ([devmode/README.md](devmode/README.md)). Copy
`local.env.example` to `local.env` (never committed) and set `DUO2_HOST` to
`duo2admin@ADDRESS`; the scripts that drive the phone
([scripts/lib/remote.sh](scripts/lib/remote.sh)) read it.

**The kernel tree** (only for kernel work): Linux v7.3.4 with the series
applied, in `src/linux` (ignored by git):

```sh
git clone --branch v7.3.4 https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git src/linux
git -C src/linux switch -c duo2
git -C src/linux am "$PWD"/kernel/patches/*.patch
docker build -t duo2-build build/
```

**Personal-data guard:** `scripts/install-personal-data-hook` (warns) or
`scripts/install-personal-data-hook --block` (refuses the commit) installs a
pre-commit hook that runs `scripts/check-personal-data` on what you stage.
List your own host names, network names and the like in `local.personal-data`
(copy `local.personal-data.example`; never committed).

## Building and testing each component

Run the commands from the repository root unless the column says otherwise.
"Phone" means it installs onto the phone in `local.env` over SSH.

| Component | Build | Host tests | Lint | Onto the phone |
|---|---|---|---|---|
| [compositor/duo-wc](compositor/duo-wc/README.md) (Rust) | `compositor/duo-wc/build-aarch64.sh` | `compositor/duo-wc/build-aarch64.sh test` | `compositor/duo-wc/build-aarch64.sh check` (rustfmt, clippy) | `compositor/duo-wc/install.sh` |
| [apps/nibfair](apps/nibfair/README.md) (Rust) | `apps/nibfair/build-aarch64.sh` | `apps/nibfair/build-aarch64.sh test` | `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings` | `apps/nibfair/install.sh` |
| [shell/duo-shell](shell/duo-shell/README.md) | none (Python) | `cd shell/duo-shell && python3 -m unittest discover -s tests`; the GTK look: `tests/run-gtk.sh tests/gtk_quick_tiles.py` | `scripts/lint-services shell/duo-shell` | `shell/duo-shell/install.sh` |
| [shell/duo-settings](shell/duo-settings/README.md) | none | `cd shell/duo-settings && python3 -m unittest discover -s tests`; pages: `tests/run-gtk.sh tests/gtk_<page>.py` | `scripts/lint-services shell/duo-settings` | `shell/duo-settings/install.sh` |
| shell/duo-screencast, shell/duo-apps | none | `cd shell/duo-screencast && python3 -m unittest discover -s tests` | `scripts/lint-services shell/duo-screencast` | their `install.sh` |
| [touch](touch/README.md) (C++, runs on Android's runtime) | `scripts/build-touch-runtime.sh` (Android NDK, your stock images) | `touch/tests/run.sh` | `scripts/lint-services touch` (clang-format, ruff, shellcheck) | `scripts/install-touch-runtime.sh` |
| sensors | `scripts/build-sensors.sh` (arm64 under QEMU), `scripts/build-sensors-runtime.sh` (your stock images) | `cd sensors/duo-posed && python3 -m unittest discover -p 'test_*.py'`, the same in `sensors/duo-brightness` | `scripts/lint-services sensors` | `scripts/install-sensors.sh` |
| power, telephony, hotspot, android, maint, battery, devmode, homecrypt, firewall | none (Python and shell) | `cd <component> && python3 -m unittest discover -s tests`; hotspot and firewall also `unshare -rn python3 tests/netns_check.py` | `scripts/lint-services <component>` | `<component>/install.sh` |
| camera | `camera/build-debs.sh` (libcamera, PipeWire packages) | none | `scripts/lint-services camera` | `camera/install-debs.sh`; the sensor tables: `scripts/build-camera-tables.sh --install` |
| telephony/81voltd, telephony/lpac | `telephony/81voltd/build.sh`, `telephony/lpac/build.sh` | `cd telephony && python3 -m unittest discover -s tests` | | `telephony/install.sh` |
| video, rootfs | none | none | `scripts/lint-services video rootfs` | `video/install.sh`; rootfs: the image |
| scripts | none | `cd scripts && python3 -m unittest discover -s tests` | `scripts/lint-services scripts` | |
| [kernel](kernel/patches/SERIES.md) | `scripts/build-kernel.sh` | none | `checkpatch.pl --strict` (below) | `scripts/duo2-try`, then `scripts/duo2-promote` |
| stock | none (Python) | `cd stock && python3 -m unittest discover -s tests` | `scripts/lint-services stock` | the package `openduo-stock-surface-duo2` (runs once at the first start) |
| the image | `scripts/build-images.sh` (a developer's image, with `firmware/linux`) | `ROOTFS_ONLY=1 scripts/build-images.sh` + `scripts/rootfs-compare.sh` (compare with a phone) | | [docs/INSTALL.md](docs/INSTALL.md), by hand |
| the release image | `scripts/build-release.sh` (from the packages, no vendor files; checked by `scripts/lib/release-audit.py`) | `cd scripts && python3 -m unittest tests.test_install_openduo` (the installer against a pretend phone) | | `scripts/install-openduo.sh` ([docs/INSTALL.md](docs/INSTALL.md)) |

`scripts/lint-services [--fix] COMPONENT...` runs ruff (format and check,
with the component's `ruff.toml`), shellcheck and clang-format over every
tracked file of the components, including scripts without an extension
(found by their `#!` line). Without `--fix` it only reports.

**A new kernel** is never installed as the default directly: `scripts/duo2-try`
boots it once with systemd-boot's boot counting (if it does not come up, the
next start boots the known-good kernel by itself), and `scripts/duo2-promote`
makes it the default after it has proved itself. See the script headers.

## Code standards

**Everyone:** match the code around you. **English only: docs, comments,
commit messages, log text.** Comments and docs say why, in plain English; no names of people or private machines in code, comments or
tests (use "the user", "the phone", documentation addresses such as
`192.0.2.0/24` and `example.org`).

**Python** (services, shell, settings, tools): `ruff format` and `ruff check`
with the component's `ruff.toml` (line length 100, rules E, F, W, I, B, UP;
`%` formatting is the house style). Tests use the standard library's
`unittest`, under the component's `tests/`, and run on the host without a
phone or root (stand-ins for the system, like `fake_mm.py` and
`fake_lpac.py`). The phone runs Debian's Python (3.13): no dependencies from
pip on the phone, only Debian packages.

**Shell:** bash or POSIX sh with `set -eu` (bash: `set -euo pipefail`),
shellcheck clean. In `scripts/`, `.shellcheckrc` allows single-quoted
commands that expand on the phone (SC2016). Secrets come from stdin, never
from the command line.

**C and C++** (touch): `clang-format` with `touch/.clang-format`; host tests
build with `-Wall -Wextra -Werror`.

**Rust** (duo-wc, Nibfair): `cargo fmt --check` (duo-wc's `rustfmt.toml`) and
`cargo clippy --all-targets -- -D warnings`; `Cargo.lock` committed; builds
with `--locked`. duo-wc meets this today (`build-aarch64.sh check`). Nibfair
does not yet (**TODO:** format and clippy pass, then make its CI job
blocking).

**Kernel patches:** upstream style, so that a patch can be sent as it is.

- Work in `src/linux` on the `duo2` branch; export the whole series with
  `git -C src/linux format-patch -q --no-signature -o kernel/patches v7.3.4..duo2`
  (delete the old files first), and update [kernel/patches/SERIES.md](kernel/patches/SERIES.md).
- `scripts/checkpatch.pl --strict` clean, apart from the documented
  exceptions in SERIES.md.
- Subjects as upstream writes them (`drm/msm/dpu: Keep ...`,
  `arm64: dts: qcom: sm8350-microsoft-surface-duo2: Enable ...`). A
  device-local workaround or diagnostic that should never go upstream starts
  its subject with `BRING-UP:`.
- Add your own `Signed-off-by:` (the Developer Certificate of Origin). Only
  you can certify it; an AI tool must never add it for you.
- A patch written with an AI assistant carries `Assisted-by: AGENT:MODEL`,
  as the kernel's `Documentation/process/coding-assistants.rst` asks.
- Other people's patches keep their authorship and are listed in SERIES.md
  with their source.

**Documentation:** English (user-interface translations are the only other
language in the tree). Subsystem notes are in [docs/hardware/](docs/hardware/).
A hardware status says only what was tested on a
real phone: **Works**, **Partial** (say what is not tested), **In progress**,
**Not tested**, **No**. Never round a status up.

## Commits and pull requests

- One topic per commit, one topic per pull request. A refactor and a fix
  are two pull requests.
- Commit subject: `component: what changed`, in the style of the history
  (`duo-wc: close a gone input device off the loop`, `homecrypt: ...`,
  `Docs: ...`). The body says why, and what was measured or tested.
- Kernel patches follow the kernel's own rules (above).
- Keep generated files, build output and anything from `out/`, `src/`,
  `firmware/` or `dumps/` out of the commit.
- Update the component's README, [CHANGELOG.md](CHANGELOG.md) (under
  "Unreleased") and, for hardware, the status table in the README in the
  same pull request.

CI runs the lint and host tests of the components a pull request touches
([.github/workflows/ci.yml](.github/workflows/ci.yml)). It cannot test on a
phone: that part is yours, and the pull request template asks how you did
it.

## AI use: disclose it

AI coding tools are welcome here; OpenDuo itself is developed with an AI
coding assistant (Claude), and its kernel patches carry `Assisted-by:`
lines. What we ask is openness and ownership:

- **Say in the pull request whether AI was used, which tool and which model**
  (for example "Claude Code, Claude Opus", "GitHub Copilot", "none"), and for
  what (writing code, tests, docs, review, research).
- **You are responsible for every line**, as if you had typed it. Read and
  understand it before you submit it. "The AI wrote it" is not an answer to
  a review question.
- Kernel patches: the `Assisted-by:` trailer (above). Other commits may
  carry a trailer such as `Co-Authored-By:` or `Assisted-by:`; the pull
  request's disclosure is what counts.
- An agent working in this repository follows [AGENTS.md](AGENTS.md),
  including its device-safety rules.

## Self-review checklist

Before you ask for review, check each point (the pull request template has
the same list):

- [ ] The pull request has one topic, and its description says what and why.
- [ ] Host tests pass for every component it touches (commands above).
- [ ] `scripts/lint-services <component>` is clean (or, for Rust,
      `build-aarch64.sh check`; for kernel patches, `checkpatch.pl --strict`).
- [ ] Tested on a real Surface Duo 2, and the description says how: the
      OpenDuo build (Settings > About, or `/etc/duo2-release`), the kernel
      release (`uname -r`), what you did and what you saw. Or it says plainly
      that it was not tested on a phone, and why.
- [ ] `scripts/check-personal-data` is clean on the staged changes; no
      identifiers in logs, screenshots or test data.
- [ ] No vendor files, firmware, blobs or build output in the diff.
- [ ] Docs updated: the component's README, CHANGELOG.md, the hardware
      status if it changed.
- [ ] AI use disclosed in the description.

## Device safety

Some mistakes on this phone cannot be undone by anyone: the modem's
calibration and the phone's identity live in a few partitions, and there is
no way to buy them back. Whether you work by hand or through an agent:

- **Never write the modem's partitions or settings:** `modemst1`,
  `modemst2`, `fsg`, `fsc`, `persist`, NV items, EFS files, carrier
  configuration (PDC) loads. Reading is fine. (The modem itself keeps its
  file system up to date through `rmtfs`; that is normal.)
- **Never write any partition by hand** (`dd` to `/dev/block/*` or
  `/dev/disk/*`, `fastboot flash`) except as [docs/INSTALL.md](docs/INSTALL.md)
  and [docs/RESTORE.md](docs/RESTORE.md) describe, with backups made first.
- **No I2C probing** (`i2cdetect`, `i2cget`, `i2cset` and the like): the
  buses carry the battery gauges, PMICs, camera modules and the pen charger,
  and a stray write can damage hardware.
- **No PMIC or GPIO writes from user space** (`gpioset`, writes into
  `debugfs` regmap files, `devmem`). Hardware is driven by drivers, through
  patches you can review.
- **Kernels only through `scripts/duo2-try`**, which boots a new kernel once
  with boot counting, and `scripts/duo2-promote` once it has proved itself.
  Never edit the boot entries or `loader.conf` on the ESP by hand.
- **Cap heavy jobs on the phone** (benchmarks, builds, long tests) with a
  memory limit, for example `systemd-run --user --scope -p MemoryMax=500M
  ...`: the phone has no swap, and running it out of memory stops calls and
  can lose data.
- The phone may be someone's daily phone: put back every setting you change,
  and do not leave it asleep, locked out or off the network when someone
  else depends on reaching it.

## Personal data and vendor files

- Never commit device identifiers (IMEI, IMSI, ICCID, EID, serial numbers,
  MAC and Bluetooth addresses), phone numbers, network names, addresses of
  your home network, host names, `/home/<name>` paths, keys or tokens, or
  locations. `scripts/check-personal-data` finds the usual ones
  (`--all` for the whole tree); a line that only looks personal can carry
  `personal-data: ok`. Examples use `192.0.2.0/24`, `198.51.100.0/24`,
  `203.0.113.0/24`, `example.org` and `00:11:22:33:44:55`.
- Bug reports and logs you attach: mask them (Settings > Developer >
  Collect a bug report does), and look through them before posting.
- **No vendor files:** no firmware, no libraries or binaries from Microsoft's
  or Qualcomm's images, no camera tables, no sensor registry, no dumps of the
  phone's partitions, not even "just for a test". The repository carries
  recipes (scripts) that extract them from the user's own images.
- Screenshots: no notifications, network names, operator names, places,
  contacts or account names in them.

## Licensing of contributions

Your contribution is licensed under the license of the files it changes:
GPL-3.0-or-later for most of the repository, MIT for `compositor/duo-wc` and
`apps/nibfair`, Apache-2.0
for `touch/`, GPL-2.0 for `kernel/` (see "Licenses" in the
[README](README.md)). A new source file should start with an
`SPDX-License-Identifier` line. Copying code from elsewhere needs a compatible license and the
original copyright notice; say where it came from in the commit.
