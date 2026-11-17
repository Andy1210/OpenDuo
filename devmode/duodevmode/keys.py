"""SSH public keys for the developer account: what may be added, how a key
is named (OpenSSH's SHA256 fingerprint), and authorized_keys edits. Pure:
the file is read and written by the caller.

A key added from Settings is one public key line: a key type of KEY_TYPES,
its base64 blob (checked: the blob names the same type; an RSA key has at
least 2048 bits), an optional comment. No options (`command=`, `from=`...):
a pasted line with options is refused, not trimmed. Lines already in the
file are kept as they are, whatever they hold.
"""

import base64
import binascii
import hashlib
import re
import struct

KEY_TYPES = (
    "ssh-ed25519",
    "sk-ssh-ed25519@openssh.com",  # personal-data: ok (a key type)
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ecdsa-sha2-nistp256@openssh.com",  # personal-data: ok (a key type)
    "ssh-rsa",
)
RSA_MIN_BITS = 2048
MAX_INPUT = 16 * 1024  # bytes of pasted text or a key file
MAX_KEYS = 32  # in the file, after an add
COMMENT_MAX = 100
B64 = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


class BadKey(ValueError):
    """A line that is not an acceptable public key; str() says why."""


class Key:
    def __init__(self, kind, blob, comment=""):
        self.kind = kind
        self.blob = blob  # bytes
        self.comment = comment

    @property
    def fingerprint(self):
        return fingerprint(self.blob)

    def line(self):
        b64 = base64.b64encode(self.blob).decode("ascii")
        return " ".join(x for x in (self.kind, b64, self.comment) if x)

    def as_dict(self):
        return {"type": self.kind, "fingerprint": self.fingerprint, "comment": self.comment}


def fingerprint(blob):
    """OpenSSH's fingerprint of a key blob: SHA256:<base64, no padding>."""
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def _string(blob, off):
    if off + 4 > len(blob):
        raise BadKey("the key is cut short")
    (n,) = struct.unpack(">I", blob[off : off + 4])
    if off + 4 + n > len(blob):
        raise BadKey("the key is cut short")
    return blob[off + 4 : off + 4 + n], off + 4 + n


def rsa_bits(blob):
    """The modulus size of an ssh-rsa blob (string type, mpint e, mpint n)."""
    _t, off = _string(blob, 0)
    _e, off = _string(blob, off)
    n, _off = _string(blob, off)
    n = n.lstrip(b"\0")
    return (len(n) - 1) * 8 + n[0].bit_length() if n else 0


def clean_comment(text):
    """A comment as shown and stored: printable ASCII, one space between
    words, at most COMMENT_MAX characters."""
    text = "".join(c if 32 < ord(c) < 127 else " " for c in text)
    return " ".join(text.split())[:COMMENT_MAX]


def parse_key(line):
    """One public key line (no options) -> Key; BadKey if not acceptable."""
    words = line.strip().split(None, 2)
    if not words:
        raise BadKey("empty")
    if words[0] not in KEY_TYPES:
        if any(words[0].startswith(p) for p in ("command=", "from=", "no-", "permit", "restrict")):
            raise BadKey("a key with options is not accepted")
        raise BadKey("not a public key of a known type (%s)" % ", ".join(KEY_TYPES[:3]))
    if len(words) < 2 or not B64.match(words[1]):
        raise BadKey("the key data is missing or not base64")
    try:
        blob = base64.b64decode(words[1], validate=True)
    except (binascii.Error, ValueError) as e:
        raise BadKey("the key data is not base64") from e
    kind, _off = _string(blob, 0)
    if kind != words[0].encode():
        raise BadKey("the key data is not a %s key" % words[0])
    if words[0] == "ssh-rsa" and rsa_bits(blob) < RSA_MIN_BITS:
        raise BadKey("an RSA key needs at least %d bits" % RSA_MIN_BITS)
    return Key(words[0], blob, clean_comment(words[2]) if len(words) > 2 else "")


def parse_input(text):
    """Pasted text or a key file -> ([Key], [problem]): every line that is not
    blank or a comment must be a key."""
    if len(text.encode("utf-8", "replace")) > MAX_INPUT:
        return [], ["more than %d KiB: not a public key" % (MAX_INPUT // 1024)]
    keys, problems = [], []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            keys.append(parse_key(line))
        except BadKey as e:
            problems.append("line %d: %s" % (n, e))
    if not keys and not problems:
        problems.append("no public key in it")
    return keys, problems


def key_of_line(line):
    """The key in an authorized_keys line, options or not (None if there is none)."""
    words = line.split()
    for i, w in enumerate(words):
        if w in KEY_TYPES and i + 1 < len(words):
            try:
                return parse_key(" ".join(words[i:]))
            except BadKey:
                # an existing key the add rules would refuse (a short RSA key): still listed
                try:
                    blob = base64.b64decode(words[i + 1], validate=True)
                except (binascii.Error, ValueError):
                    return None
                return Key(w, blob, clean_comment(" ".join(words[i + 2 :])))
    return None


def listed(text):
    """The keys in an authorized_keys file's text."""
    out = []
    for line in (text or "").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            k = key_of_line(line)
            if k is not None:
                out.append(k)
    return out


def with_added(text, keys):
    """(new text, added keys): the keys not there yet appended; BadKey
    when the file would hold more than MAX_KEYS."""
    have = {k.fingerprint for k in listed(text)}
    new = []
    for k in keys:
        if k.fingerprint not in have:
            have.add(k.fingerprint)
            new.append(k)
    if len(have) > MAX_KEYS:
        raise BadKey("at most %d keys" % MAX_KEYS)
    if not new:
        return text, []
    body = text or ""
    if body and not body.endswith("\n"):
        body += "\n"
    return body + "".join(k.line() + "\n" for k in new), new


def without(text, fp):
    """(new text, how many lines went): every line whose key has fingerprint fp removed."""
    keep, gone = [], 0
    for line in (text or "").splitlines(keepends=True):
        k = key_of_line(line) if line.strip() and not line.lstrip().startswith("#") else None
        if k is not None and k.fingerprint == fp:
            gone += 1
            continue
        keep.append(line)
    return "".join(keep), gone


FP = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")


def is_fingerprint(text):
    return bool(FP.match(text or ""))
