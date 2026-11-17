"""The home's encryption key and what protects it (homecrypt/README.md). Pure:
no files, no kernel; the root helpers keep the key file.

The home is encrypted (fscrypt v2) with one random 64-byte master key. That
key is never stored as it is: the key file keeps it wrapped, once per
protector, and a protector is either the lock screen's PIN or the recovery
key (a long random number the user writes down):

    secret  = scrypt(PIN or recovery key, salt, N, r, p) -> 64 bytes
    pad     = HMAC-SHA512(secret[:32], "openduo home key v1 pad")
    wrapped = master key XOR pad
    tag     = HMAC-SHA256(secret[32:], header || salt || params || wrapped)

A fresh salt for every wrap, so a pad is used once. A wrong PIN gives a
wrong tag (checked in constant time); nothing is unwrapped without the
right one. scrypt (Python's hashlib, OpenSSL) is memory-hard: N = 2**15,
r = 8 (32 MiB) takes 80-95 ms on the Snapdragon 888's big cores and ~280 ms
on a small one (measured 2026-11-16), the owner's "about 100-200 ms": the
PIN is not slowed down on purpose. Without a TEE, the wrapped key on the
flash is all an attacker needs to try PINs offline, so a short PIN only
keeps casual finders out; the README says so, and the Settings page too.

The recovery key: RECOVERY_DIGITS random decimal digits (about 120 bits),
written in groups of six, typed on the lock screen's PIN pad like a PIN
when the PIN is forgotten.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets

MASTER_KEY_SIZE = 64
SALT_SIZE = 16
TAG_SIZE = 32
KDF = {"n": 1 << 15, "r": 8, "p": 1}
KDF_LIMITS = {"n": (1 << 12, 1 << 20), "r": (1, 32), "p": (1, 16)}
PAD_INFO = b"openduo home key v1 pad"
TAG_INFO = b"openduo home key v1"
RECOVERY_DIGITS = 36
RECOVERY_GROUP = 6
KINDS = ("pin", "recovery")
FORMAT = 1

# Contents / names modes and flags of the policy (fscrypt.py has the
# kernel's numbers); recorded in the key file, so that a later version
# knows how this home was encrypted.
POLICY_DEFAULT = {"contents": "AES-256-XTS", "filenames": "AES-256-HCTR2", "flags": ["PAD_32"]}


class BadKeyFile(ValueError):
    pass


def _b64(data):
    return base64.b64encode(data).decode()


def _unb64(text, size=None):
    try:
        data = base64.b64decode(text.encode(), validate=True)
    except (ValueError, AttributeError) as e:
        raise BadKeyFile("bad base64") from e
    if size is not None and len(data) != size:
        raise BadKeyFile("wrong length")
    return data


def new_master_key():
    return secrets.token_bytes(MASTER_KEY_SIZE)


def key_identifier(master):
    """The 16-byte identifier the kernel gives a raw v2 key (fs/crypto/
    keyring.c): HKDF-SHA512 with an all-zero salt, info "fscrypt\\0" and
    context 1, the first 16 bytes. Known before the key is added, so a key
    file can be written before the filesystem may take keys."""
    prk = hmac.new(bytes(64), master, hashlib.sha512).digest()
    return hmac.new(prk, b"fscrypt\x00" + b"\x01" + b"\x01", hashlib.sha512).digest()[:16]


def kdf_ok(params):
    """The scrypt parameters a key file may carry (a file with huge ones
    would make every unlock take minutes or exhaust memory)."""
    try:
        for k, (lo, hi) in KDF_LIMITS.items():
            v = params[k]
            if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
                return False
        n = params["n"]
        return n & (n - 1) == 0 and 128 * params["r"] * n <= 256 << 20
    except (KeyError, TypeError):
        return False


def _secret(text, salt, params):
    n, r, p = params["n"], params["r"], params["p"]
    return hashlib.scrypt(
        text.encode(), salt=salt, n=n, r=r, p=p, dklen=64, maxmem=128 * r * n + (16 << 20)
    )


def _header(kind, params):
    return json.dumps(
        {"v": FORMAT, "kind": kind, "kdf": "scrypt", **params}, sort_keys=True
    ).encode()


def _pad(secret):
    return hmac.new(secret[:32], PAD_INFO, hashlib.sha512).digest()


def _tag(secret, kind, params, salt, wrapped):
    return hmac.new(
        secret[32:], TAG_INFO + _header(kind, params) + salt + wrapped, hashlib.sha256
    ).digest()


def wrap(master, kind, text, params=None, salt=None):
    """A protector (a dict for the key file) that opens `master` with `text`
    (the PIN or the recovery key)."""
    if len(master) != MASTER_KEY_SIZE or kind not in KINDS:
        raise ValueError("bad key or kind")
    params = dict(params or KDF)
    if not kdf_ok(params):
        raise ValueError("bad scrypt parameters")
    salt = salt if salt is not None else secrets.token_bytes(SALT_SIZE)
    secret = _secret(text, salt, params)
    wrapped = bytes(a ^ b for a, b in zip(master, _pad(secret)))
    return {
        "kdf": "scrypt",
        **params,
        "salt": _b64(salt),
        "wrapped": _b64(wrapped),
        "tag": _b64(_tag(secret, kind, params, salt, wrapped)),
    }


def unwrap(protector, kind, text):
    """The master key, or None when `text` is not the right one."""
    try:
        params = {k: protector[k] for k in ("n", "r", "p")}
        if protector.get("kdf") != "scrypt" or not kdf_ok(params):
            return None
        salt = _unb64(protector["salt"], SALT_SIZE)
        wrapped = _unb64(protector["wrapped"], MASTER_KEY_SIZE)
        tag = _unb64(protector["tag"], TAG_SIZE)
    except (KeyError, TypeError, BadKeyFile):
        return None
    secret = _secret(text, salt, params)
    if not hmac.compare_digest(tag, _tag(secret, kind, params, salt, wrapped)):
        return None
    return bytes(a ^ b for a, b in zip(wrapped, _pad(secret)))


# -- the recovery key ---------------------------------------------------------------------


def new_recovery_key():
    """RECOVERY_DIGITS random digits (no leading-zero bias: secrets.randbelow)."""
    return "".join(str(secrets.randbelow(10)) for _ in range(RECOVERY_DIGITS))


def format_recovery_key(digits):
    """'123456-234567-...': groups of RECOVERY_GROUP, to write down."""
    return "-".join(digits[i : i + RECOVERY_GROUP] for i in range(0, len(digits), RECOVERY_GROUP))


def parse_recovery_key(text):
    """The digits of a recovery key as typed (spaces and dashes allowed), or
    None when it is not one."""
    digits = "".join(c for c in (text or "") if c not in " -\t")
    if len(digits) == RECOVERY_DIGITS and digits.isascii() and digits.isdigit():
        return digits
    return None


# -- the key file -------------------------------------------------------------------------


def new_key_file(user, home, identifier, policy=None):
    """The key file's content before any protector is added."""
    return {
        "format": FORMAT,
        "user": user,
        "home": home,
        "identifier": identifier.hex(),
        "policy": dict(policy or POLICY_DEFAULT),
        "protectors": {},
    }


def parse_key_file(text):
    """The key file's JSON -> dict, checked (BadKeyFile)."""
    try:
        kf = json.loads(text)
    except ValueError as e:
        raise BadKeyFile("not JSON") from e
    if not isinstance(kf, dict) or kf.get("format") != FORMAT:
        raise BadKeyFile("unknown format")
    for field in ("user", "home", "identifier"):
        if not isinstance(kf.get(field), str):
            raise BadKeyFile("no %s" % field)
    try:
        if len(bytes.fromhex(kf["identifier"])) != 16:
            raise BadKeyFile("bad identifier")
    except ValueError as e:
        raise BadKeyFile("bad identifier") from e
    if not isinstance(kf.get("protectors"), dict) or not isinstance(kf.get("policy"), dict):
        raise BadKeyFile("no protectors")
    for kind in kf["protectors"]:
        if kind not in KINDS:
            raise BadKeyFile("unknown protector %r" % kind)
    return kf


def format_key_file(kf):
    return json.dumps(kf, indent=1, sort_keys=True) + "\n"


def open_with(kf, text):
    """(master key, the protector that opened it) with a PIN or a recovery
    key, or (None, None). A text that has the recovery key's shape is tried
    as the recovery key only; anything else as the PIN."""
    recovery = parse_recovery_key(text)
    order = ("recovery",) if recovery is not None else ("pin",)
    for kind in order:
        p = kf["protectors"].get(kind)
        if p is None:
            continue
        key = unwrap(p, kind, recovery if kind == "recovery" else text)
        if key is not None:
            return key, kind
    return None, None


def protect(kf, master, kind, text, params=None):
    """kf with the `kind` protector (re)made for `text`."""
    kf = dict(kf)
    kf["protectors"] = dict(kf["protectors"])
    kf["protectors"][kind] = wrap(master, kind, text, params)
    return kf


def without(kf, kind):
    kf = dict(kf)
    kf["protectors"] = {k: v for k, v in kf["protectors"].items() if k != kind}
    return kf


def wipe(buf):
    """Overwrite a bytearray holding a key (Python's bytes cannot be: keys
    live in bytearrays where they are kept for longer)."""
    if isinstance(buf, bytearray):
        for i in range(len(buf)):
            buf[i] = 0


def pending_key_text(master):
    """The raw key as the root-only pending file keeps it until the boot job
    or the first unlock has given it to the kernel."""
    return _b64(master) + "\n"


def parse_pending_key(text):
    try:
        return _unb64((text or "").strip(), MASTER_KEY_SIZE)
    except BadKeyFile:
        return None


def random_bytes(n):
    return os.urandom(n)
