# Updates

OpenDuo updates itself over the air, without reflashing: its own parts come as
Debian packages from OpenDuo's signed apt repository, the rest of the system
from Debian's own repositories. Every install takes a snapshot first, and a new
kernel boots with automatic rollback: if it does not come up healthy, the phone
starts the previous kernel by itself.

- [For users: Settings > Updates](#for-users-settings--updates)
- [What an update may touch](#what-an-update-may-touch)
- [The packages](#the-packages)
- [The kernel and its way back](#the-kernel-and-its-way-back)
- [The repository](#the-repository)
- [Releases and CI](#releases-and-ci)
- [Building and testing locally](#building-and-testing-locally)
- [Command line tools](#command-line-tools)
- [Snapshots and the ways back](#snapshots-and-the-ways-back)
- [Backups](#backups)
- [Moving a phone installed before the packages](#moving-a-phone-installed-before-the-packages)
- [Files](#files)
- [Open points](#open-points)

## For users: Settings > Updates

The page shows whether the phone is up to date, what an update would bring and
what is going on:

- **Check now** reads OpenDuo's and Debian's package lists and works out the
  update. The page lists OpenDuo's packages one by one, Debian's by name (with
  the number of security updates), and **What's new**: the
  [CHANGELOG](../CHANGELOG.md) sections of the OpenDuo build on offer.
- **Install** asks you in the shell first, then takes a snapshot of the system
  and installs. It runs only on the charger or with the battery at 50 % or more.
  The install runs in the background as a system service: closing Settings does
  not stop it.
- **Restart to finish**: the compositor, the shell and a new kernel take effect
  with the next start. Nothing restarts the phone by itself (except a new kernel
  that failed its checks, see below).
- **Tell me when updates are ready** (on): the phone looks once a day, on WiFi,
  and sends a notification. **Look on mobile data too** (off) lets the daily
  look use mobile data.
- **Install at night** (off): between 01:00 and 05:00, on WiFi and on the
  charger, with a snapshot first. The phone does not restart: the page and a
  notification ask you to.
- **Testing channel** (developer mode only): newer builds before they reach
  everyone. Switching back to stable keeps what testing installed until stable
  has a newer version.
- **System kernel**: while a new kernel is being checked, the page says so; if
  it did not pass, it says that the phone went back to the previous one.

## What an update may touch

The phone's own pieces are guarded against Debian's packages (`duo2-update`,
below). The state of a typical phone and what is done about each:

| What | On the phone | Risk | What we do |
|---|---|---|---|
| kernel | OpenDuo's own (`openduo-kernel-surface-duo2-*`, or one put there by hand) | a Debian kernel would run its hooks, fill `/boot`, confuse the boot entries | `linux-image-*`, `linux-headers-*`, `flash-kernel`, `grub*`, `shim*`, `u-boot*` pinned to −1 (`/etc/apt/preferences.d/duo2-never`), refused by `duo2-update` |
| systemd-boot | only `systemd-boot-efi` and `-tools` | the `systemd-boot` package's postinst runs `bootctl update`, its unit writes the ESP at boot | `systemd-boot` pinned to −1; the ESP is mounted read-only |
| ESP (`/boot/efi`) | automounted read-only: kernels, DTBs, initramfs images, entries | only `duo2-boot` (packaged kernels) and `scripts/duo2-try` / `duo2-promote` write it | `duo2-update` compares every file's sha256 before and after; the packaged kernels' own files are expected to change |
| firmware (`/usr/lib/firmware`) | the phone's own files, owned by no package | a Debian `firmware-*` package would overwrite them silently | `firmware-*` pinned to −1; downloaded packages are looked into; sha256 before and after |
| modules (`/usr/lib/modules`) | the running kernel's | — | sha256 before and after (a packaged kernel may take over its own release's files) |
| `initramfs-tools` | `update_initramfs=no` | a trigger would build an initramfs | the conffile is kept (`--force-confold`) and checked afterwards |
| mesa, ModemManager, libqmi, systemd | Debian's | GPU, modem, PID 1 behave differently | allowed; `duo2-update` says what to try afterwards |
| OpenDuo's rebuilds of Debian packages | libcamera 0.7.2 (`~duo2.N`), pipewire (`+duo2.N`) | a Debian security update to the same source would be missed | pinned to OpenDuo's repository (990); `duo2-update check` says when Debian has a newer source (rebuild with `camera/build-debs.sh`) and refuses to replace ours with Debian's |
| Waydroid | from repo.waydro.id (pinned) | local patches | re-applied after every dpkg run (`80duo2-waydroid`), checked afterwards |
| recommended packages | `APT::Install-Recommends "1"` (Debian's default) | an upgrade would pull in new recommends | updates run without recommends |

## The packages

OpenDuo's own parts are Debian packages built from this repository
(`packaging/packages.toml`):

| Package | What |
|---|---|
| `openduo-duo-wc` | the dual-screen compositor (arm64) |
| `openduo-duo-shell` | status bars, app grid, quick menu, lock screen |
| `openduo-duo-apps` | how the preinstalled apps fit the phone |
| `openduo-duo-settings` | Duo Settings |
| `openduo-duo-screencast` | screen sharing and screenshots through the portal |
| `openduo-nibfair` | a pen game (arm64) |
| `openduo-touch-surface-duo2` | `duo2-touchd` (built with the Android NDK), the touch and pen units, rules, calibration, tools; **not** the touch runtime |
| `openduo-power-surface-duo2` | automatic sleep, charge hold, pen charger, RTC clock, torch |
| `openduo-battery-surface-duo2` | the battery usage statistics |
| `openduo-telephony-surface-duo2` | modem hook, mobile data, call audio, SMS, eSIM (`lpac`, arm64) |
| `openduo-sensors-surface-duo2` | the free half of the sensor stack, duo-posed, duo-brightness (arm64) |
| `openduo-hotspot` | the mobile hotspot and USB tethering |
| `openduo-android` | Waydroid's hardening and session (no Android image) |
| `openduo-maint` | updates, snapshots, backups, the kernels' boot entries |
| `openduo-devmode` | developer mode |
| `openduo-firewall` | the default-deny firewall |
| `openduo-homecrypt` | the optional encryption of the user's data |
| `openduo-plymouth-theme-surface-duo2` | the boot splash on both panels |
| `openduo-release` | `/etc/duo2-release` and `/etc/os-release`; its `OpenDuo-Changes` field carries the CHANGELOG's newest sections for Settings > Updates |
| `openduo-archive-keyring` | the repository's key, its sources and pins |
| `openduo-kernel-surface-duo2-<release>` | one kernel build: Image, the Surface Duo 2 device trees, the modules |
| `openduo-kernel-surface-duo2` | always depends on the newest kernel build |
| `openduo-surface-duo2` | the whole system for the Surface Duo 2 |

Packages only for the Surface Duo 2 end in `-surface-duo2`, so that a Surface
Duo (1) port can add its own beside the shared ones.

**One list of files.** A component package carries its component exactly as
`scripts/lib/stage-components.sh` stages it for the image, in
`/usr/lib/openduo/components/<component>/`, and its postinst runs that
component's own `install-files.sh`, the same script the image build
(`rootfs/install-components.sh`) and the network installers run. What a
component installs, keeps (a config file is kept when the phone has one; the
shipped one is `*.dist`), enables or migrates is decided there and nowhere
else. The postinst then reloads systemd, enables on the first install the units
the image enables beside the installer, restarts the daemons whose restart
nobody notices (`try-restart` on an upgrade), and asks for a restart of the
phone (`/run/reboot-required`) for the parts the session runs (compositor,
shell). The GUI user comes from `/etc/duo2/gui-user` (the image writes it), else
from `duo-wc.service`'s `User=`.

**The touch runtime** (Microsoft's heat processor and the stock libraries it
needs) may not be redistributed and is never in a package: every owner builds it
from their own phone (`scripts/build-touch-runtime.sh`,
`scripts/install-touch-runtime.sh`). The touch package puts the newly built
daemon into that runtime when the phone has one. The same holds for the sensors'
stock half and the factory addresses.

**Removing** a component package leaves its installed files in place (they are
the system); components are not meant to be removed one by one.

**Versions.** `VERSION` with its `-` as `~` (a pre-release sorts before the
release), then the commit's UTC time and short hash: `0.1.0-dev` at commit
`5b008a3` of 2026-11-17 00:15:03 UTC is `0.1.0~dev+20261117001503.g5b008a3`, the
release `0.1.0` is `0.1.0+<time>.g<hash>`. The time comes first, so a later
commit is a newer package; it is the commit's time, not the build's, so the same
commit always builds the same version and the same bytes (`SOURCE_DATE_EPOCH`,
files owned by root, `dpkg-deb` in the builder container `packaging/Dockerfile`).
A kernel has its own: `7.3.4-duo2-g3ba7db140` committed 2026-11-15 10:17:28 UTC is
`7.3.4+20261115101728.g3ba7db140`, so a kernel update does not wait for an
OpenDuo release. A tree with uncommitted changes builds `.dirty` versions,
which are never published.

## The kernel and its way back

A kernel package carries `/usr/lib/openduo/kernel/<release>/` (Image, the
`sm8350-microsoft-surface-duo2*.dtb` device trees, the config, its defaults)
and `/usr/lib/modules/<release>/`. Every build has its own release string, so
kernels never share a module directory.

**Install.** The postinst indexes the modules and runs `duo2-boot add`: the
Image and the DTB go to `/boot/efi/openduo/<release>/` (the ESP remounted
read-write for it), and the systemd-boot entry
`/loader/entries/openduo-<release>+3.conf`:

```
title   OpenDuo 0.1.0-dev (7.3.5-duo2-g...)
sort-key duo2
version 10.<n>
linux   /openduo/<release>/Image
initrd  /duo2/initrd-boot.img
devicetree /openduo/<release>/sm8350-microsoft-surface-duo2-bringup-j.dtb
options <the known-good entry's options> openduo.entry=openduo-<release>
```

The options, the initramfs and the DTB name are the known-good entry's
(`duo2.conf`), unless `/etc/duo2/boot.conf` says otherwise; the initramfs does
not depend on the kernel. `n` counts the kernels installed on the phone, so the
newest one sorts first.

**Boot counting.** systemd-boot counts a try down before it boots the entry,
by renaming the file on the FAT (`+3` → `+2-1` → …): this needs no EFI
variable, which this phone's UEFI does not keep. An entry with no tries left is
"bad" and sorted last, so systemd-boot boots the next one by itself: the
previous good kernel. The order:

1. a `scripts/duo2-try` entry (version 99, one try, for development);
2. the newest packaged kernel while it has tries left;
3. the previous good packaged kernel;
4. `duo2.conf` (version 1, the hand-managed known-good kernel);
5. `rescue.conf` (sort-key `rescue`, the rescue initramfs over USB);
6. bad entries.

**Health checks.** On a boot from an entry that is still being tried,
`duo2-boot-health.service` (ordered before `boot-complete.target` and required
by it, after `multi-user.target` so that the boot itself never waits for it)
gives the kernel up to 5 minutes of awake time for:

- the compositor (`duo-wc.service` active: the panels show the session);
- touch (`duo2-touchd.service` active, or skipped because the phone has no
  touch runtime yet);
- the network: WiFi connected or the modem registered, or nothing to connect
  to (radios switched off, no saved WiFi network and no usable SIM).

When they pass, `boot-complete.target` is reached and
`duo2-boot-bless.service` renames the entry to `openduo-<release>.conf`: it is
the known-good kernel from then on, and packaged kernels older than the previous
one leave the ESP (`duo2-boot prune`; their packages are purged at the next
install). This is `systemd-bless-boot`'s job, done by our own unit: Debian ships
`systemd-bless-boot` in the `systemd-boot` package, which is pinned away, and it
would also bless a `duo2-try` entry (the image masks it for that reason).

If the compositor or touch fail (a unit that systemd is restarting counts as
not up yet, not as failed), or there is neither a WiFi device nor a modem,
the phone is not usable: the entry is marked bad at once (`+0-N`) and the phone
restarts into the previous kernel. If only the network is missing although one
is expected, nothing is blessed and the counter stays: the next start tries the
kernel again, and after its third try systemd-boot boots the previous one. The
rules are pure and tested (`maint/duo2maint/health.py`); the numbers are in
`/etc/duo2/boot.conf` (`tries`, `keep`, `health_timeout`, `reboot_on_failure`).

**Never touched:** `duo2.conf` (only read, for its options), `rescue.conf`, the
rescue initramfs `/duo2/initrd.img`, and `duo2-try` entries. A kernel package
refuses to be removed while its kernel runs. `duo2-update` refuses to run on a
`duo2-try` boot, and Settings refuses to install while a new kernel is still
being checked.

**Development.** `scripts/duo2-try` and `scripts/duo2-promote` stay the way to
try a kernel built on a developer's machine; on a phone with packaged kernels,
`duo2-promote` says that the newest good packaged kernel boots before
`duo2.conf`. A kernel meant for the phone is installed as a package
(`packaging/build-packages.sh`, then `sudo apt install ./openduo-kernel-*.deb`):
it gets its three tries and is blessed by the checks.

## The repository

A static apt repository made with reprepro (`packaging/repo.sh`), published on
GitHub Pages under `apt/` (any web server will do):

```
apt/
  dists/stable/{InRelease,Release,Release.gpg,main/binary-arm64/Packages*}
  dists/testing/...
  pool/main/...                  the packages (OpenDuo's and the camera rebuilds)
  openduo-archive-keyring.asc    the public key
  conf/, db/                     reprepro's state (harmless to publish)
```

**Suites.** `testing` gets every release and pre-release, `stable` the
releases (`packaging/repo.sh BASE promote` copies testing into stable). Both
say `Origin: OpenDuo`.

**On the phone** (`openduo-archive-keyring`):

```
# /etc/apt/sources.list.d/openduo.sources
Types: deb
URIs: https://<owner>.github.io/OpenDuo/apt
Suites: stable
Components: main
Architectures: arm64
Signed-By: /usr/share/keyrings/openduo-archive-keyring.asc
```

The testing channel adds `openduo-testing.sources` beside it (Settings >
Updates writes and removes it). Debian's own repositories stay for the base
system. `/etc/apt/preferences.d/openduo` pins OpenDuo's rebuilds of Debian
packages (libcamera 0.7.2, pipewire) at priority 990, so they win over Debian's
even when Debian's version number is higher.

To add the repository by hand (a phone from before the packages): fetch
`openduo-archive-keyring_*.deb` from the repository's `pool/main/o/` (or a
release's files on GitHub), check its key's fingerprint, and
`sudo apt install ./openduo-archive-keyring_*.deb`.

**Signing.** An ed25519 key: the primary key certifies (no expiry), a signing
subkey signs the repository (3 years). `packaging/signing/new-key.sh DIR` makes
one; DIR (0700) gets `gnupg/` (the keyring), `primary-secret.asc` (keep it
offline: it makes new subkeys and revokes), `ci-signing-subkey.asc` (the
signing subkey alone, for CI), `revocation.asc` and `FINGERPRINT`; the public key
goes to `packaging/keyring/openduo-archive-keyring.asc`. The current key:

```
BD74 373E FEAD C539 E637  1D7D ECA8 EB90 AA92 39CD   OpenDuo Archive Signing Key
     signing subkey ed25519/1302BA11FD318F23, expires 2029-11-15
```

Where the secret parts live:

- **The signing subkey** is a GitHub Actions secret of the repository's
  `release` environment (below), so only a release run can use it, and nobody
  can read it back, the maintainer included.
- **The primary key and the revocation certificate** are kept offline by the
  maintainer, protected by a passphrase. They are never in GitHub: a secret
  there cannot be read back, so it is no backup, and with the primary key out
  of reach a stolen subkey can still be revoked and replaced without users
  changing their keyring. Before the
subkey expires: `gpg --quick-set-expire <fingerprint> 3y '*'` with the primary
key, then publish the public key again (a new `openduo-archive-keyring`).

## Releases and CI

`.github/workflows/release.yml` builds and publishes on a version tag:

- tag `vX.Y.Z` (it must match `VERSION`): to `testing` and `stable`;
- tag `vX.Y.Z-rc1` and the like: to `testing` only;
- a manual run: to the suite it names.

It builds everything in containers, as on a developer's machine: the kernel
from Linux v7.3.4 with `kernel/patches/` (applied with fixed committer data, so
the same patches give the same release string), duo-wc and Nibfair, the
sensors' free half (under qemu), lpac, duo2-touchd with the Android NDK (its
archive's sha256 checked), then the unit tests, the packages, and the repository
on the `gh-pages` branch; a tag's packages are also attached to its GitHub
release. The camera rebuilds (hours under qemu) are only rebuilt on request
(`build_camera`): the repository keeps the ones published before.

What the GitHub repository needs:

- an environment named `release` (Settings > Environments), with the
  maintainer as a required reviewer and deployments allowed only from tags
  matching `v*` and the `main` branch (a manual run starts from a branch);
- in that environment, the secret below; the rest go under Settings >
  Secrets and variables > Actions.

| Name | Kind | What |
|---|---|---|
| `OPENDUO_SIGNING_KEY` | secret of the `release` environment | the content of `ci-signing-subkey.asc` |
| `OPENDUO_APT_URL` | variable, optional | the repository's URL (default `https://<owner>.github.io/<repository>/apt`) |
| `OPENDUO_MAINTAINER` | variable, optional | the packages' `Maintainer` field |

and GitHub Pages set to deploy from the `gh-pages` branch.

## Building and testing locally

```sh
packaging/build-packages.sh                 # every package into out/packages/debs/
KOUT=out/linux packaging/build-packages.sh  # with the kernel build in out/linux
packaging/repo.sh out/apt add testing out/packages/debs/*.deb
packaging/repo.sh out/apt promote           # testing into stable
packaging/signing/new-key.sh out/signing    # a new key (once)
```

`build-packages.sh` reads `OPENDUO_APT_URL` (the keyring package is only built
with it), `OPENDUO_MAINTAINER` and the kernel's location (`KOUT`, `KSRC`, and
`KROOT` for a worktree) from the environment or `local.env`, and refuses a tree
with uncommitted changes (`ALLOW_DIRTY=1` builds `.dirty` versions). The built
parts are taken as they are: `scripts/lib/stage-components.sh` names each one
and the script that builds it.

**Tests:**

```sh
(cd maint && python3 -m unittest discover -s tests)        # boot entries, health rules, update settings
(cd packaging && python3 -m unittest discover -s tests)    # versions, manifest rules, maintainer scripts
(cd shell/duo-settings && python3 -m unittest tests.test_updates)
shell/duo-settings/tests/run-gtk.sh tests/gtk_updates_page.py   # the page under GTK
packaging/tests/container-test.sh                           # install and upgrade, see below
```

`packaging/tests/container-test.sh` builds this commit's packages and a next
version of them (a scratch commit, and a second kernel package), puts both into
a test repository signed with a throwaway key (this version in stable, the next
one in testing), and in a Debian trixie arm64 root under qemu
(`packaging/tests/Dockerfile.base`, with a stand-in ESP) installs
`openduo-surface-duo2` from the signed repository and checks the files and
units each package lists (`check` in `packages.toml`), the sudoers rules, the
built programs' libraries, the kernel's ESP files and entry, that `duo2.conf`
and `rescue.conf` stay untouched; blesses the entry; then switches to the
testing channel, upgrades, and checks that the new files landed, the owner's
settings and a unit the owner disabled stayed, the new kernel is tried first
with the previous one kept, a failed kernel goes last, and a purged kernel
leaves the ESP. The container has no systemd running, so the boot health
check itself is tested against the systemd the unit tests run under
(`maint/tests/test_boot_health_systemd.py`, skipped without one).

**A test repository on the LAN.** To try a build on a real phone before it is
published, serve a repository from the build machine:

1. Build the packages with the LAN address as the repository's URL, so that
   the keyring package's `openduo.sources` points there:
   `OPENDUO_APT_URL=https://<build machine's address>:<port> packaging/build-packages.sh`
   (the builder takes https:// only).
2. `packaging/repo.sh out/ota-repo add testing out/packages/debs/*.deb`, then
   `promote`. It is signed with the archive's signing subkey
   (`out/signing/gnupg`), as a release is.
3. Serve `out/ota-repo/` over HTTPS on the LAN address, only while you test,
   with a certificate from a throwaway CA. Python's `http.server` works when it
   speaks HTTP/1.1 and closes TLS with close_notify; without that, apt's OpenSSL
   reports "unexpected eof".
4. On the phone: the CA in `/etc/apt/openduo-lan-ca.crt` and
   `Acquire::https::<address>::CaInfo "/etc/apt/openduo-lan-ca.crt";` in
   `/etc/apt/apt.conf.d/60openduo-lan`, so the CA is trusted for that one host
   only. Then `sudo apt install ./openduo-archive-keyring_*.deb` and
   `sudo apt update`.

How this differs from the public repository: the URL (the LAN address instead
of `https://<owner>.github.io/OpenDuo/apt`) and the two extra files, which are
removed afterwards. The key, the suites and the pins are the same.

When the test is over, do not leave the LAN source enabled: with the server
gone, every `apt-get update` (the daily check, Settings > Updates) reports it.
Put `Enabled: no` into `/etc/apt/sources.list.d/openduo.sources` and
`openduo-testing.sources` (if there is one) and remove the two extra files;
Debian's updates keep coming through the page. That edits the keyring
package's conffile, so moving to the public repository later means
installing the public `openduo-archive-keyring` with
`sudo apt install -o Dpkg::Options::=--force-confnew ./openduo-archive-keyring_*.deb`
(without it dpkg keeps the edited, disabled file), and then switching the
testing channel off and on again in Settings if it was on (its file is
derived from `openduo.sources`).

## Command line tools

All of them are root's (the administrator runs them with sudo; the GUI user may
run only `duo2-updates`' Settings commands).

**`duo2-update`** (Debian's and OpenDuo's packages, with a look first and a way
back):

- `check [--no-refresh] [--download] [--json]`: the simulated `apt-get
  dist-upgrade` (without recommends), security updates marked; what it refuses
  (a forbidden package, any removal unless allowed, one of our rebuilds replaced
  by Debian's, an unclean dpkg, an unowned file in a protected place
  overwritten); what to know (our rebuilds Debian has a newer source for, local
  edits an upgrade replaces); what to try out afterwards. Exit 0 nothing, 10
  updates, 3 refused.
- `run [--yes] [--allow-removals] [--no-repack] [--force]`: preconditions (not a
  `duo2-try` boot, the ESP read-only, 2 GiB free, battery above 30 % or
  charging, no other apt), a snapshot with the packages about to change repacked
  (`dpkg-repack`: the way back works offline), `apt-get dist-upgrade` with our
  config files kept, then the checks (the ESP, the firmware and the running
  kernel's modules unchanged, `dpkg --audit`, the Waydroid patch,
  `update_initramfs=no`, no Debian kernel in `/boot`, our rebuilds still ours, no
  new failed unit, what still runs old code).
- `rollback [SNAPSHOT] [--yes] [--remove-new]`: the packages back to a
  snapshot's versions from its repacked packages.
- `status`.

**`duo2-updates`** (Settings > Updates' one door; one JSON line per answer):
`status`, `check`, `install` (the user's yes in the shell),
`set auto|notify|mobile-data on|off`, `set channel stable|testing` (testing in
developer mode only). The work runs in `duo2-updates-check.service` and
`duo2-updates-install.service`; the daily look is `duo2-updates-check.timer`,
the nightly install `duo2-updates-auto.timer` (it does nothing until switched
on). Settings: `/etc/duo2/updates.conf`; state: `/var/lib/duo2-updates/`,
`/run/duo2-updates/`; logs: `/var/log/duo2-updates/`.

**`duo2-boot`** (the packaged kernels' entries): `add`, `remove`, `status
[--json]`, `health [--wait]`, `bless`, `bad [RELEASE]`, `prune`, `obsolete`.

**`duo2-snapshot`**, **`duo2-backup`**: below.

## Snapshots and the ways back

The root file system is ext4 (no file system snapshots), so a snapshot is a
directory, `/var/backups/duo2/snapshots/<time>[-label]/` (root only, 0700): the
package list with versions, the manually installed and held packages, `/etc`,
`/usr/local`, `/opt`, `/var/lib/duo2*` and the Bluetooth pairings as tarballs,
the ESP as a tarball (for repairs by hand only), the sha256 of the ESP, the
firmware and the running kernel's modules, and the packages repacked before an
update. About 300 MB; 3 are kept. **It contains secrets** (WiFi passwords, ssh
host keys, the lock PIN's hash, Bluetooth keys): only root reads it.

`duo2-snapshot create [--label L] [--repack PKG...] [--keep N]`, `list`, `show
NAME`, `diff NAME` (the changes to `/etc`), `restore NAME etc|usr-local|opt|var-lib
[--yes]` (a `pre-restore` snapshot first; it overwrites, it does not delete newer
files), `prune`.

The ways back, layer by layer:

1. **A package is wrong:** `sudo duo2-update rollback`, or one package from the
   snapshot's `debs/`.
2. **A setting is wrong:** `sudo duo2-snapshot diff NAME`, then `restore NAME etc`.
3. **A packaged kernel is wrong:** it falls back by itself (boot counting); by
   hand: `sudo duo2-boot bad`, or the previous entry in the boot menu. A
   hand-built kernel: `scripts/duo2-promote --rollback`.
4. **The system does not come up:** the rescue entry (`rescue.conf`, a shell
   over USB), or fastboot and the headless recovery; the snapshot can be
   unpacked by hand from there.
5. **Everything is lost:** a fresh image (`scripts/build-images.sh`), the
   post-install steps, then the user's data and settings from a backup.

## Backups

`duo2-backup` copies the GUI user's home (without caches: `maint/backup.filter`),
the Android apps' data (`INCLUDE_ANDROID`) and the system's settings
(`INCLUDE_SYSTEM`: `/etc`, `/var/lib/duo2*`, BlueZ, the package lists) to a NAS
or a PC with rsync over ssh. Not copied: the Waydroid images (downloadable
again), `/usr/local` and the system (rebuilt from the recipe). The target gets
`current/` (the latest full copy) and `deleted/<date>/` (what a run replaced or
deleted).

**The phone pushes** (recommended): root sends with a key only root can read
(`/etc/duo2/backup/`), and on the target that key may write into one directory
and read nothing (`rrsync -wo`). At night, on the charger and WiFi
(`duo2-backup.timer`, 03:30, wakes the phone and keeps it awake), **not
enabled**:

1. `sudo duo2-backup init` → `/etc/duo2/backup.conf` (root, 0600) and the key;
   it prints the target's `authorized_keys` line.
2. On the target: a user for the backups and the printed line in its
   `~/.ssh/authorized_keys`: `command="rrsync -wo /the/backup/dir",restrict ssh-ed25519 …`
   (rrsync comes with rsync 3.2.4+).
3. On the phone: `TARGET=user@host:` in `/etc/duo2/backup.conf`,
   `sudo duo2-backup trust-host` (compare the fingerprint on the target),
   `sudo duo2-backup run --now --dry-run`, then `run --now`.
4. When it works: `sudo systemctl enable --now duo2-backup.timer`;
   `sudo duo2-backup status`.

**The PC pulls:** `scripts/duo2-backup-pull TARGET [--dry-run]` logs in as the
administrator, runs `sudo duo2-backup stage` on the phone and reads with
`sudo rsync`; no key on the phone, but the phone must be awake and reachable.

**Restore** (a new phone or a reinstall): the home back from
`current/home/<user>/` (then `chown -R` without the Android data, and
`sudo duo2-backup restore-owners` with the list from `staged/`), the settings
from `staged/system/config.tar.gz` picked by hand (WiFi, BlueZ, the lock, the
touch calibration), the packages from `manual.txt`.

## Moving a phone installed before the packages

A phone installed from an image (or kept current with the network installers)
has every component's files in place, owned by no package. The packages take
them over: their postinst runs the same `install-files.sh`, which keeps the
phone's config files, and dpkg overwrites files no package owns without a word.
The steps, with the phone on the charger and an ssh session open:

1. Install the new `maint` first (`maint/install.sh` from the build machine, or
   the `openduo-maint` package): the new `duo2-update`, `duo2-boot`,
   `duo2-updates`, the units.
2. `sudo duo2-snapshot create --label before-packages`.
3. Install the keyring package, `sudo apt update`, then
   `sudo apt install --no-install-recommends openduo-surface-duo2` (or one
   package at a time, the kernel last). The kernel package may be the running
   build (it takes over that release's modules) or a newer one. One package at
   a time, install the metapackage `openduo-kernel-surface-duo2` too: an update
   is a dist-upgrade, which brings a new kernel build only as the new
   dependency of that metapackage.
4. Restart. On a new kernel: `sudo duo2-boot status` (trying → good within
   minutes), `journalctl -b -u duo2-boot-health`.
5. If anything is off: `sudo duo2-update rollback`; the kernel falls back by
   itself; the snapshot of step 2 has everything else.

## Files

| Source | On the phone |
|---|---|
| `maint/duo2-update`, `duo2-snapshot`, `duo2-backup`, `duo2-boot`, `duo2-updates`, `duo2maint/` | `/usr/local/lib/duo2-maint/`, links in `/usr/local/sbin/` |
| `maint/apt/preferences.d/duo2-never` | `/etc/apt/preferences.d/` |
| `maint/boot.conf` | `/etc/duo2/boot.conf` (kept when present), `boot.conf.dist` |
| `maint/systemd/duo2-boot-{health,bless}.service` | `/etc/systemd/system/` (enabled) |
| `maint/systemd/duo2-updates-*` | `/etc/systemd/system/` (both timers enabled; the nightly install does nothing until switched on) |
| `maint/systemd/duo2-backup.{service,timer}` | `/etc/systemd/system/` (not enabled) |
| `maint/backup.filter`, `backup.conf.example` | `/etc/duo2/backup.filter`, `/usr/local/share/duo2-maint/` |
| `shell/duo-settings/duosettings/updates*.py` | Settings > Updates |
| `scripts/lib/stage-components.sh` | (build) every component staged for the image and the packages |
| `packaging/` | (build) the package builder, the repository script, the key tool, the tests |
| `.github/workflows/release.yml` | (CI) the release workflow |

## Open points

- The first phone (2026-11-17) went over from its hand-installed files to the
  packages with the steps above, from a LAN test repository. The public repository is not published yet.
- The image does not install from the packages yet: `scripts/build-images.sh`
  still runs the same installers directly (the same files, owned by no
  package). Building the image from the repository is the next step.
- The image's overlay (`rootfs/overlay`: fstab, the USB gadget, the factory
  addresses, udev rules, the audio profiles) is in no package yet, except the
  touch files; an `openduo-base-surface-duo2` would carry it.
- OpenDuo's own files live in `/usr/local`, where Debian packages normally put
  nothing; moving them to `/usr/lib/openduo` is a separate change.
- The camera rebuilds need a rebuild when Debian updates their sources
  (`duo2-update check` says so).
