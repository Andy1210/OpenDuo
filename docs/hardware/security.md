# Security model: who may do what

On the phone every app, and the compositor itself, runs as the same user (the
GUI user), so the user ID decides nothing. The rules below are what protect
the phone from its own apps; the code that depends on them points here.

## Accounts

- **Root belongs to the administrator** (`duo2admin`): ssh only, key only
  (`AuthenticationMethods publickey`,
  `rootfs/overlay/etc/ssh/sshd_config.d/duo2.conf`), passwordless sudo
  (`/etc/sudoers.d/duo2admin`), no GUI session, no lingering, and no user
  units (`ConditionUser=!duo2admin` in the user-unit drop-ins, except
  `systemd-exit.service`).
- **The GUI user has no sudo, no password and is not in the `input` group**
  (raw `/dev/input/event*` would let any app eavesdrop on and inject touches,
  including on the lock screen's PIN pad; the compositor gets the devices
  from seatd). Root actions from the GUI go through narrow helpers
  (`duo-shell-helper`, `duo-settings-helper`, `duo-lock-helper`: their own
  sudoers lines, fixed commands) and PackageKit's polkit rule.
- The GUI user's ssh keys live in a root-owned file
  (`/etc/ssh/authorized_keys.d/USER`): a key file in the home directory could
  be written by any app, which could then open a session without the QRTR
  filter.
- Install files with `install -m 0644/0755` (or `tar --mode=go-w`): a working
  copy made under umask 002 installs group-writable files otherwise.

## The compositor's control sockets (`compositor/duo-wc/src/trust.rs`)

- `/run/duo-wc/duo-wc.sock` (0600, the GUI user's): any app may only read
  (`get_outputs`, `get_state`, `subscribe`, the `expect_window` start hint).
  Everything else (unlock, screenshot, power, ambient, poses, rotation,
  other apps' windows, gesture and energy settings) is `not allowed`.
- `/run/duo-wc/admin.sock`: decided by the caller's credentials
  (`SO_PEERCRED`), not the file mode. Root and `admin_users` may do
  everything; `sensor_users` (`duo-posed`) the posture/pose/rotation/power
  requests; `confirm_users` (`polkitd`) only `confirm`; anyone else is
  refused at once.
- **Confirm:** what only the session can do but another party asks for
  (restarting the display from the Settings helper, a polkit package removal)
  is put to the user by the shell (`confirm_request`), and runs only on a
  yes that came over the shell's trusted connection.
- **Trust factory:** the session gets a SOCK_SEQPACKET socketpair from
  duo-wc (fd 3, `DUO_WC_TRUST_FD`) that returns a fresh trusted `control` or
  `wayland` connection per request (SCM_RIGHTS). It has no path and cannot
  be reopened through `/proc/PID/fd`. The session starts the shell and the
  on-screen keyboard with it; the shell requests its own connections and the
  lock screen's from it and does not pass the factory to the apps it starts.
- **Guarded Wayland globals:** `zwp_virtual_keyboard_manager_v1`,
  `zwp_input_method_manager_v2`, `ext_session_lock_manager_v1`,
  `ext_foreign_toplevel_list_v1` and `zwlr_layer_shell_v1` are visible only to
  trusted clients. `text-input-v3` stays public, so the on-screen keyboard
  types into every app.

## The lock

- The lock survives a compositor restart: duo-wc keeps it in systemd's file
  descriptor store (memfd, `FileDescriptorStorePreserve=yes`), not in a file an
  app could delete.
- The PIN belongs to root (`/var/lib/duo2-lock/pin`, 0600, PBKDF2). The only
  door is `duo-lock-helper` (`verify`, `set`, `clear`, `status`, `policy`):
  the lock screen hands it the typed PIN and never sees the hash; changing
  the PIN and weakening the lock need the current PIN; after 5 wrong PINs
  there is a 30 s wait, doubling to 15 minutes, counted by root. A readable
  hash would let any app brute-force a 4-6 digit PIN offline.
- Restarting the display and removing packages need the user's yes (above).

## ptrace

Same-user processes could attach to each other, so the kernel has Yama
(`CONFIG_SECURITY_YAMA=y`, `kernel.yama.ptrace_scope = 1` from
`60-duo2-ptrace.conf`), and duo-wc, the shell and the lock screen make
themselves non-dumpable at start (`PR_SET_DUMPABLE 0`); the shell and lock
run with `python3 -sE` (an app could write `~/.local/lib/python3*`).
`dmesg` is restricted for the apps too (`61-duo2-dmesg.conf`).

## QRTR is for system services only

Any app could open an `AF_QIPCRTR` socket and talk QMI to the modem
(calls, SMS) or to the sensor core, bypassing ModemManager. The fix needs no
kernel patch: `RestrictAddressFamilies=~AF_QIPCRTR` on `duo-wc.service` and
on `user@.service` (`rootfs/overlay/etc/systemd/system/*/20-duo2-no-qrtr.conf`).
The seccomp filter is inherited by every child, through sudo and setuid
execs, and cannot be removed; the call fails with `EAFNOSUPPORT`. System
services (ModemManager, rmtfs, tqftpserv, iio-sensor-proxy, `duo-posed`,
`duo2-wwan`, the Waydroid container manager) are started by PID 1 and are
unfiltered. The one sudo helper that needs QRTR is `duo2-esim` (lpac speaks
QMI over QRTR to the eUICC): it starts lpac as its own transient service with
`systemd-run --pipe --wait` (`NoNewPrivileges`, `ProtectSystem=full`,
`ProtectHome`, `PrivateTmp`, only UNIX/INET/NETLINK/QRTR sockets). Inside the
Waydroid container QRTR is `EAFNOSUPPORT` anyway (mainline `qrtr_create`
answers only in `init_net`).

`scripts/install-no-qrtr.sh` installs this on a running phone and
`scripts/duo2-qrtr-check` verifies it (read-only).

## What remains (the model)

Apps reach the modem only through ModemManager (the polkit rule lets the GUI
user's apps place calls and send SMS) and the sensors only through
iio-sensor-proxy. Any app can run the sudo helpers, so the helpers offer
nothing that weakens the phone without the user's yes or the PIN. Processes
of one user can signal each other: an app can kill the shell (it restarts
within about a second, but a lid close in that gap does not lock), and
the `confirm` prompt lets an app pester the user (the answer is always the
user's). Because the shell is non-dumpable, xdg-desktop-portal cannot
identify it (`Unable to open /proc/PID/root`) and GTK reads gsettings
instead, with no visible effect.

## Administration from a development machine

`scripts/lib/remote.sh` (reads `local.env`) wraps this model: `duo2_ssh` runs
as the administrator, `duo2_user` as the GUI user (user manager, D-Bus),
`duo2_session` inside the graphical session, and `duoctl exec-trusted
--wayland -- CMD` starts a trusted Wayland client. Installers take the GUI
user from `DUO2_USER`, not from `id -un`.
