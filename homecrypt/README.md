# homecrypt: Encrypt my data

Settings > Lock screen > **Encrypt my data** encrypts your home folder (your
files, settings, messages, browser profiles, and Android's data, which
lives under `~/.local/share/waydroid`) with a key that only your PIN opens.
It is optional and off by default; it needs a lock screen PIN.

## What you get

- After a restart the phone starts as usual: **calls ring and can be
  answered, alarms ring, and the lock screen works** before you enter the
  PIN. Everything else of yours stays encrypted until the first unlock.
- At the first unlock after a restart the screen goes dark for a few
  seconds while your session restarts on your decrypted data. If a call is
  up at that moment, this waits until it ends.
- Later unlocks are as fast as before (~0.1-0.3 s for the PIN check). The
  data stays decrypted until the phone restarts or switches off.

What waits for the first unlock:

- **Messages.** An SMS received before it stays with the modem service
  (ModemManager, in memory) and appears in Chats after the unlock; there is
  no notification before. A restart before the unlock may lose such an SMS.
  MMS are fetched after the unlock.
- **Android apps** start after the unlock.
- **Call history**: calls answered or missed before the unlock are not in
  the Calls history.
- Your settings: before the first unlock the phone uses default settings
  (ringtone, volumes, wallpaper). Alarms are the exception: a copy of their
  times is kept unencrypted for this (below).

## Before you switch it on

- **If you forget the PIN, your data is gone.** Only the recovery key can
  open it then. Make one when you switch encryption on (the default): 36
  digits, shown once. Write it down and keep it away from the phone. On the
  lock screen you type it on the PIN keypad instead of the PIN, and in
  Settings it is accepted wherever the current PIN is asked for; then set a
  new PIN.
- **Use a long PIN.** The phone has no security chip this system can use,
  so nothing slows down guessing once someone has copied the phone's
  storage: a 4- or 6-digit PIN can be found with ordinary tools. A PIN of 8
  digits or more makes that much harder; 12 digits (the most) is best.
  Encryption still protects your data from anyone who just picks up the
  phone.
- **Free space and time.** Switching on copies your home once, at the next
  restart, with a progress bar on the boot screen: it needs as much free
  space as your files take (plus 5 %), and a few minutes per few GB. Keep
  the phone charging: below 30 % without a charger it waits for a later
  restart. If anything goes wrong, your data stays as it was.
- **Files deleted before** encryption may still be readable from the
  storage chips by experts, and so may the unencrypted copy of your home
  that the switch-on removes (the free space is trimmed afterwards, which
  usually, not certainly, erases it). For full protection switch
  encryption on with a fresh installation.
- Your backups (`duo2-backup`) are taken from the decrypted home: protect
  the backup target yourself.

## Changing and switching off

- **Changing the PIN** re-encrypts only the key, not your data: instant.
- **Removing the PIN** is refused while encryption is on: switch encryption
  off first. A PIN-less encryption would have to keep its key on the phone
  unprotected, which would look like encryption and protect nothing.
- **Switching off** decrypts your home at the next restart (the same copy,
  the other way; the same free space and time). Until that restart,
  Cancel keeps it encrypted (the PIN or the recovery key). Cancelling a
  switch-on that waits for its restart needs the PIN itself.
- **If a switch-on fails** (not enough space, an error while copying), your
  home stays as it was and Settings says why; the PIN keeps working (its
  check stays with the unused key file until you try again or remove the
  PIN).
- **New key** makes a new recovery key; the old one stops working.

## How it works

The home is a **fscrypt** directory (ext4's own file encryption, v2
policy): file contents **AES-256-XTS**, file names **AES-256-HCTR2**
(AES-256-CTS on a kernel without HCTR2), 32-byte name padding, and the
`IV_INO_LBLK_64` flag, which lets the UFS Inline Crypto Engine encrypt with
one key slot (it needs the ext4 `stable_inodes` feature: the filesystem can
then grow but never shrink). The contents go through the kernel's
blk-crypto: on the **UFS Inline Crypto Engine** when the root is mounted
with `-o inlinecrypt`, otherwise **blk-crypto-fallback** on the CPU's AES
instructions. The kernel describes SM8350's engine (ICE v3.2.0, kernel
patch 0136) and the phone's secure firmware takes raw keys for it.
Measured on the phone (`tests/speedtest.py`, 1 GiB, O_DIRECT): an
encrypted file reads at 1500 MB/s through the engine (a plain one: 1400-1500),
429 MB/s through the fallback; what the engine writes is standard XTS (it
reads back the same through the fallback after a restart), so a boot without
`inlinecrypt` reads everything. The option belongs in the root's line of
`/etc/fstab`: the initramfs mounts the root with plain `-o rw`, and systemd
remounts it with the fstab options before any key is used; `rootflags=` on
the kernel command line is not read. A kernel without inline encryption
only logs "inline encryption not supported" for it and mounts.

**The key.** One random 64-byte master key. It is stored only wrapped,
once per protector (PIN, recovery key), in `/var/lib/duo2-lock/home.key`
(root, 0600):

    secret  = scrypt(PIN, salt, N=2^15, r=8, p=1) -> 64 bytes   (32 MiB)
    wrapped = key XOR HMAC-SHA512(secret[:32], "openduo home key v1 pad")
    tag     = HMAC-SHA256(secret[32:], header || salt || params || wrapped)

with a fresh salt at every wrap (`duohomecrypt/keys.py`). The KDF is not
slowed down on purpose (the owner's choice): 80-95 ms on the Snapdragon
888's big cores, ~280 ms on a small one. The PIN check of the lock screen
is the unwrap itself: while the home is encrypted there is no other copy of
the PIN (the lock's `pin` file says only `homekey`, and the old PBKDF2
hash, faster to try offline, is overwritten). Root's back-off after wrong
PINs counts recovery-key tries too.

**Who does what.** No new door: the lock screen's root helper
`duo-lock-helper` (shell/duo-shell) takes the PIN on stdin as before and
calls `duohomecrypt/lockside.py`; Settings asks it with `home enable
recovery|norecovery`, `home disable`, `home cancel`, `home recovery-key`,
`home status`. Everything that touches the filesystem runs as root under
systemd, never in a process of the user:

- `duo2-home-boot.service` (`duo2-home-crypt boot`, before
  `systemd-user-sessions`, so before the user's services and duo-wc):
  - a pending change is made: a copy of the home next to it
    (`/home/.USER.openduo-new`, encrypted or plain) with rsync, the two
    directories swapped in one step (`renameat2(RENAME_EXCHANGE)`), the old
    one deleted, `fstrim`. A restart in the middle resumes it: the state
    file and the directories themselves say how far it got. A failure
    before the swap leaves the home as it was (the state file's detail says
    why); one after it finishes the clean-up at the next start. The raw key
    waits in `/var/lib/duo2-lock/home.pending` (root, 0600) only until the
    change is done (it is overwritten, then deleted);
  - an encrypted home whose key is not in the kernel gets an **empty tmpfs
    mounted over it**, the user's: the user manager, PipeWire, Calls, the
    shell, the lock screen and Clocks start on that, as for a new user,
    and nothing they write survives the unlock. `/run/duo2-home/locked` and
    `/run/duo2-home/encrypted` say so.
- The first right PIN (`duo-lock-helper verify`) on a locked home writes
  the key to `/run/duo2-home/unlock.key` (root, 0600, a tmpfs) and starts
  `duo2-home-settle.service` (`duo2-home-crypt settle`): once no call is up
  (ModemManager, `mmcli`), it stops `duo-wc.service` and the user's
  `user@UID.service`, unmounts the tmpfs, gives the kernel the key
  (`FS_IOC_ADD_ENCRYPTION_KEY`), and starts both again.
- Not before the first unlock (`ConditionPathExists=!/run/duo2-home/locked`
  drop-ins): Chats' background service (it would take the SMS from
  ModemManager into a database on the tmpfs), mmsd-tng, the Android
  session.
- **Alarms before the first unlock.** `duo2-alarms-de` (a user service,
  while the home is encrypted and unlocked) copies `org.gnome.clocks
  alarms` into `/var/lib/duo2-de/alarms`, a compiled dconf database (the
  user's, 0600); the boot job puts `/etc/dconf/profile/user` in place
  (`user-db:user`, then `file-db:` that file). Before the first unlock the
  user's database is empty and the copy answers, so Clocks starts and rings
  and the automatic sleep wakes the phone for it; after the unlock the
  user's own value wins. Only the alarms' times, names and repeat days are
  kept unencrypted. An alarm that rang before the unlock may show once more
  after it.
- `duo2-backup` does nothing while the home is locked.

The filesystem needs the ext4 `encrypt` (and `stable_inodes`) feature: an
image made by `scripts/build-images.sh` has both; an older installation gets
them when encryption is switched on, through the kernel's
`EXT4_IOC_SET_TUNE_SB_PARAM` (7.3+: the superblock of the mounted root is
changed by the kernel itself, not written behind its back as `tune2fs` on a
mounted filesystem would). The kernel needs `CONFIG_FS_ENCRYPTION` and
`CONFIG_CRYPTO_HCTR2` (kernel/config/duo2.config). **Every kernel the phone
may boot must have them once a home is encrypted**: one without (an old
known-good kernel, a try kernel built before them) starts with the home
locked and unreadable (nothing is lost, the files stay encrypted until a
kernel with them starts).

| File | On the phone |
|---|---|
| `duo2-home-crypt` | `/usr/local/sbin/duo2-home-crypt` (`boot`, `settle`, `status`; root, run by systemd; `features [DIR]` by hand: the ext4 features through the kernel, e.g. before a measurement) |
| `duohomecrypt/` (`keys`, `fscrypt`, `store`, `migrate`, `lockside`) | `/usr/local/lib/duo2-homecrypt/duohomecrypt/` (duo-lock-helper imports it) |
| `duo2-alarms-de` | `/usr/local/lib/duo2-homecrypt/` |
| `systemd/duo2-home-boot.service` | `/etc/systemd/system`, enabled |
| `systemd/duo2-home-settle.service` | `/etc/systemd/system`, started by the PIN check |
| `systemd/user/50-duo2-home-locked.conf` | `/etc/systemd/user/{duo-chatty-daemon,mmsd-tng,duo2-waydroid-session}.service.d/` |
| `systemd/user/duo2-alarms-de.service` | `/etc/systemd/user`, enabled globally |
| `install-files.sh` | puts them in place (the image and `install.sh`); `/var/lib/duo2-lock/home.state` says `plain` |

State files: `/var/lib/duo2-lock/home.state` (0644, no secret: `state` =
plain, encrypt-pending, encrypting, encrypted, decrypt-pending, decrypting;
`progress`, `detail`, `recovery`), `home.key` and `home.pending` (root,
0600), `/run/duo2-home/{encrypted,locked,unlock.key}`.

Tests: `python3 -m unittest discover -s tests` (the key wrapping, the
recovery key, the key identifier the kernel computes, the ioctls' numbers
and layouts, the state file, the migration's parts, the lock side against
a temporary store); `tests/dryrun.sh` runs it all end to end as root in a
throwaway privileged container on a loop-mounted ext4 with the kernel's
fscrypt (enable, the encryption at start, a restart and the tmpfs, the
first unlock, a new PIN, the recovery key, a copy that fails both ways, a
clean-up that fails after the swap, the decryption), and compares every
file, link, mode, owner, xattr and hard link. `tests/speedtest.py DIR`
(root, on the phone) writes and reads a plain and an encrypted directory
with a throwaway key and says which implementation fscrypt used: after
`mount -o remount,inlinecrypt /` the inline crypto engine, if the kernel
has it; `--keep` leaves the encrypted file and prints its key, to read it
back through the CPU path after a restart (the engine's output must be
standard XTS). duo-lock-helper's side:
`shell/duo-shell/tests/test_lockstore.py` (`HomeEncrypted`); the page:
`shell/duo-settings/tests/gtk_lock_page.py`.
