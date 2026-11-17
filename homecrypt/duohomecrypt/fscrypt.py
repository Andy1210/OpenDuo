"""The kernel's fscrypt and ext4 interfaces, through ioctl (homecrypt/README.md).
No tool of its own is needed (neither Debian's fscrypt nor fscryptctl): the
helpers call these directly, as root.

    set_policy(dir_fd, identifier, contents, filenames, flags)  an empty dir
    get_policy(fd)                       -> Policy or None (not encrypted)
    add_key(fs_fd, raw)                  -> the 16-byte identifier
    remove_key(fs_fd, identifier)        -> the kernel's status flags
    key_status(fs_fd, identifier)        -> "absent" | "present" | "incompletely removed"
    set_ext4_features(fs_fd, encrypt=True, stable_inodes=False)
                                         the superblock's features on a mounted
                                         ext4 (EXT4_IOC_SET_TUNE_SB_PARAM, 7.3)

Numbers and layouts: include/uapi/linux/fscrypt.h and ext4.h of the kernel.
"""

import errno
import fcntl
import os
import struct

# -- numbers ----------------------------------------------------------------------------


def _ioc(direction, nr, size, kind=0x66):  # "f"
    return (direction << 30) | (size << 16) | (kind << 8) | nr


_IOW, _IOR, _IOWR = 1, 2, 3

POLICY_V2 = 2
IDENTIFIER_SIZE = 16
KEY_SPEC_TYPE_IDENTIFIER = 2
POLICY_V2_SIZE = 24  # version, modes, flags, log2 data unit, reserved[3], identifier[16]
KEY_SPEC_SIZE = 40  # type, reserved, union[32]
ADD_KEY_SIZE = KEY_SPEC_SIZE + 4 + 4 + 4 + 28
REMOVE_KEY_SIZE = KEY_SPEC_SIZE + 4 + 20
KEY_STATUS_SIZE = KEY_SPEC_SIZE + 24 + 4 + 4 + 4 + 52
TUNE_SB_SIZE = 232

FS_IOC_SET_ENCRYPTION_POLICY = _ioc(_IOR, 19, 12)  # sized as the v1 struct
FS_IOC_GET_ENCRYPTION_POLICY_EX = _ioc(_IOWR, 22, 9)
FS_IOC_ADD_ENCRYPTION_KEY = _ioc(_IOWR, 23, ADD_KEY_SIZE)
FS_IOC_REMOVE_ENCRYPTION_KEY = _ioc(_IOWR, 24, REMOVE_KEY_SIZE)
FS_IOC_GET_ENCRYPTION_KEY_STATUS = _ioc(_IOWR, 26, KEY_STATUS_SIZE)
EXT4_IOC_GET_TUNE_SB_PARAM = _ioc(_IOR, 45, TUNE_SB_SIZE)
EXT4_IOC_SET_TUNE_SB_PARAM = _ioc(_IOW, 46, TUNE_SB_SIZE)

MODES = {"AES-256-XTS": 1, "AES-256-CTS": 4, "AES-256-HCTR2": 10}
MODE_NAMES = {v: k for k, v in MODES.items()}
FLAGS = {"PAD_4": 0x00, "PAD_8": 0x01, "PAD_16": 0x02, "PAD_32": 0x03, "IV_INO_LBLK_64": 0x08}
KEY_STATUS = {1: "absent", 2: "present", 3: "incompletely removed"}
REMOVAL_FILES_BUSY = 0x1
REMOVAL_OTHER_USERS = 0x2

EXT4_FEATURE_COMPAT_STABLE_INODES = 0x0800
EXT4_FEATURE_INCOMPAT_ENCRYPT = 0x10000
EXT4_TUNE_FL_EDIT_FEATURES = 0x00004000


class Policy:
    def __init__(self, version, contents, filenames, flags, log2_data_unit, identifier):
        self.version = version
        self.contents = contents
        self.filenames = filenames
        self.flags = flags
        self.log2_data_unit = log2_data_unit
        self.identifier = identifier

    def __repr__(self):
        return "Policy(v%d %s/%s flags=%#x id=%s)" % (
            self.version,
            MODE_NAMES.get(self.contents, self.contents),
            MODE_NAMES.get(self.filenames, self.filenames),
            self.flags,
            self.identifier.hex(),
        )


def flags_value(names):
    value = 0
    for n in names:
        value |= FLAGS[n]
    return value


def pack_policy_v2(identifier, contents, filenames, flags):
    if len(identifier) != IDENTIFIER_SIZE:
        raise ValueError("an identifier is 16 bytes")
    return struct.pack(
        "<BBBBB3x16s", POLICY_V2, MODES[contents], MODES[filenames], flags, 0, identifier
    )


def unpack_policy(buf):
    """FS_IOC_GET_ENCRYPTION_POLICY_EX's answer (size, then the policy)."""
    (size,) = struct.unpack_from("<Q", buf, 0)
    version = buf[8]
    if version != POLICY_V2 or size < POLICY_V2_SIZE:
        return Policy(version, None, None, None, None, b"")
    v, c, f, flags, log2, ident = struct.unpack_from("<BBBBB3x16s", buf, 8)
    return Policy(v, c, f, flags, log2, ident)


def key_spec(identifier):
    if len(identifier) != IDENTIFIER_SIZE:
        raise ValueError("an identifier is 16 bytes")
    return struct.pack("<II16s16x", KEY_SPEC_TYPE_IDENTIFIER, 0, identifier)


# -- calls ------------------------------------------------------------------------------


def set_policy(dir_fd, identifier, contents="AES-256-XTS", filenames="AES-256-HCTR2", flags=None):
    """Encrypt the (empty) directory `dir_fd` with the key `identifier`;
    the key must have been added (add_key) first."""
    if flags is None:
        flags = FLAGS["PAD_32"]
    buf = bytearray(pack_policy_v2(identifier, contents, filenames, flags))
    fcntl.ioctl(dir_fd, FS_IOC_SET_ENCRYPTION_POLICY, buf, True)


def get_policy(fd):
    """The policy of an encrypted file or directory, None if it is not one."""
    buf = bytearray(struct.pack("<Q", POLICY_V2_SIZE) + bytes(POLICY_V2_SIZE))
    try:
        fcntl.ioctl(fd, FS_IOC_GET_ENCRYPTION_POLICY_EX, buf, True)
    except OSError as e:
        if e.errno in (errno.ENODATA, errno.ENOENT):
            return None
        raise
    return unpack_policy(bytes(buf))


def add_key(fs_fd, raw):
    """Give the kernel a raw v2 key for the filesystem `fs_fd` is on; its
    identifier back (the kernel computes it from the key)."""
    if not 16 <= len(raw) <= 64:
        raise ValueError("a raw key is 16 to 64 bytes")
    buf = bytearray(ADD_KEY_SIZE + len(raw))
    struct.pack_into("<I", buf, 0, KEY_SPEC_TYPE_IDENTIFIER)
    struct.pack_into("<III", buf, KEY_SPEC_SIZE, len(raw), 0, 0)
    buf[ADD_KEY_SIZE:] = raw
    try:
        fcntl.ioctl(fs_fd, FS_IOC_ADD_ENCRYPTION_KEY, buf, True)
        return bytes(buf[8 : 8 + IDENTIFIER_SIZE])
    finally:
        for i in range(len(buf)):
            buf[i] = 0


def remove_key(fs_fd, identifier):
    """Take the key away: the kernel's removal status flags back (FILES_BUSY:
    files still open stay readable until closed)."""
    buf = bytearray(key_spec(identifier) + bytes(REMOVE_KEY_SIZE - KEY_SPEC_SIZE))
    fcntl.ioctl(fs_fd, FS_IOC_REMOVE_ENCRYPTION_KEY, buf, True)
    (flags,) = struct.unpack_from("<I", buf, KEY_SPEC_SIZE)
    return flags


def key_status(fs_fd, identifier):
    buf = bytearray(key_spec(identifier) + bytes(KEY_STATUS_SIZE - KEY_SPEC_SIZE))
    fcntl.ioctl(fs_fd, FS_IOC_GET_ENCRYPTION_KEY_STATUS, buf, True)
    (status,) = struct.unpack_from("<I", buf, KEY_SPEC_SIZE + 24)
    return KEY_STATUS.get(status, "unknown")


def pack_tune_features(compat=0, incompat=0):
    """EXT4_IOC_SET_TUNE_SB_PARAM's argument that sets these features."""
    buf = bytearray(TUNE_SB_SIZE)
    struct.pack_into("<I", buf, 0, EXT4_TUNE_FL_EDIT_FEATURES)
    # set_feature_{compat,incompat,ro_compat}_mask at 76, 80, 84
    struct.pack_into("<III", buf, 76, compat, incompat, 0)
    return buf


def ext4_features(fs_fd):
    """(compat, incompat, ro_compat) of a mounted ext4 (7.3's tune ioctl)."""
    buf = bytearray(TUNE_SB_SIZE)
    fcntl.ioctl(fs_fd, EXT4_IOC_GET_TUNE_SB_PARAM, buf, True)
    return struct.unpack_from("<III", buf, 64)


def set_ext4_features(fs_fd, encrypt=True, stable_inodes=False):
    """Turn the `encrypt` (and `stable_inodes`) feature on in a mounted
    ext4, through the kernel (not by writing the superblock behind its
    back, as tune2fs would on a mounted filesystem)."""
    buf = pack_tune_features(
        EXT4_FEATURE_COMPAT_STABLE_INODES if stable_inodes else 0,
        EXT4_FEATURE_INCOMPAT_ENCRYPT if encrypt else 0,
    )
    fcntl.ioctl(fs_fd, EXT4_IOC_SET_TUNE_SB_PARAM, buf, False)


def fs_supports_encryption(sysfs="/sys/fs/ext4/features/encryption"):
    """The running kernel's ext4 can encrypt (CONFIG_FS_ENCRYPTION)."""
    return os.path.exists(sysfs)


def inline_crypto(dev_queue="/sys/block/sda/queue/crypto"):
    """The modes the block device's inline encryption hardware offers
    (blk-crypto's sysfs): e.g. {"AES-256-XTS"}; empty without one."""
    try:
        return set(os.listdir(os.path.join(dev_queue, "modes")))
    except OSError:
        return set()
