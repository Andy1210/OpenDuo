#!/usr/bin/python3 -I
"""Throughput of a plain and an encrypted directory on the phone's own
filesystem, as root (homecrypt/README.md; the rollout's measuring step):

    speedtest.py [--mb N] [--keep] [--no-drop] DIR

DIR (e.g. /var/tmp/openduo-speed, on the root's ext4, which needs the
`encrypt` feature) gets `plain/` and `encrypted/` (a throwaway random key,
never stored); in each a file of N MiB (default 1024) is written with
fsync and read back with O_DIRECT (dd), and its SHA-256 compared. The
kernel's log says which implementation fscrypt used ("using blk-crypto
(native)" = the inline crypto engine, after `mount -o remount,inlinecrypt
/`; "blk-crypto-fallback" = the CPU). Everything is removed at the end
(--keep leaves the encrypted file and prints its key's identifier and
the key, base64, for a read-back after a restart: the ICE's output must
read the same through the fallback).
"""

import base64
import hashlib
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
for _p in ("/usr/local/lib/duo2-homecrypt", os.path.dirname(HERE)):
    if os.path.isdir(os.path.join(_p, "duohomecrypt")):
        sys.path.insert(0, _p)
        break

from duohomecrypt import fscrypt, keys  # noqa: E402


def dd(argv):
    p = subprocess.run(["dd"] + argv, capture_output=True, text=True)
    m = re.search(r"([\d.,]+) ([kMG]?B)/s", p.stderr)
    if p.returncode != 0 or not m:
        raise SystemExit("dd %s: %s" % (" ".join(argv), p.stderr.strip()))
    value = float(m.group(1).replace(",", "."))
    return value * {"kB": 1e-3, "MB": 1, "GB": 1e3, "B": 1e-6}[m.group(2)]


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


DROP = True


def drop_caches():
    os.sync()
    if not DROP:  # (a test on a shared machine: its page cache stays)
        return
    with open("/proc/sys/vm/drop_caches", "w") as f:
        f.write("3\n")


def measure(directory, mb):
    path = os.path.join(directory, "speed.bin")
    w = dd(["if=/dev/urandom", "of=" + path, "bs=4M", "count=%d" % (mb // 4), "conv=fsync"])
    before = sha(path)
    drop_caches()
    r = dd(["if=" + path, "of=/dev/null", "bs=4M", "iflag=direct"])
    drop_caches()
    return w, r, sha(path) == before


def main(argv):
    global DROP
    mb, keep = 1024, False
    args = list(argv)
    if "--no-drop" in args:
        args.remove("--no-drop")
        DROP = False
    if "--keep" in args:
        args.remove("--keep")
        keep = True
    if len(args) >= 2 and args[0] == "--mb":
        mb = int(args[1])
        args = args[2:]
    if len(args) != 1 or os.geteuid() != 0:
        sys.stderr.write(__doc__)
        return 2
    top = os.path.abspath(args[0])
    os.makedirs(os.path.join(top, "plain"), exist_ok=True)
    enc = os.path.join(top, "encrypted")
    master = keys.new_master_key()
    ident = None
    fd = os.open(top, os.O_RDONLY | os.O_DIRECTORY)
    try:
        ident = fscrypt.add_key(fd, master)
        os.mkdir(enc, 0o700)
        dfd = os.open(enc, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fscrypt.set_policy(
                dfd,
                ident,
                "AES-256-XTS",
                "AES-256-HCTR2",
                fscrypt.flags_value(["PAD_32", "IV_INO_LBLK_64"]),
            )
        finally:
            os.close(dfd)
        print("inline crypto modes of sda: %s" % (sorted(fscrypt.inline_crypto()) or "none"))
        for name, d in (("plain", os.path.join(top, "plain")), ("encrypted", enc)):
            w, r, same = measure(d, mb)
            print("%-9s write+fsync %7.0f MB/s, O_DIRECT read %7.0f MB/s, read back %s"
                  % (name, w, r, "the same" if same else "DIFFERENT"))  # fmt: skip
        log = subprocess.run(["dmesg"], capture_output=True, text=True).stdout
        for line in log.splitlines():
            if "fscrypt:" in line and "using" in line:
                print("kernel:", line.split("]", 1)[-1].strip())
        if keep:
            print("kept %s/speed.bin; key %s, identifier %s"
                  % (enc, base64.b64encode(master).decode(), ident.hex()))  # fmt: skip
            return 0
    finally:
        if not keep:
            shutil.rmtree(top, ignore_errors=True)
            if ident is not None:
                try:
                    fscrypt.remove_key(fd, ident)
                except OSError:
                    pass
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
