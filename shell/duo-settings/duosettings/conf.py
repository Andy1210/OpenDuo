"""key = value config files (duo-wc.conf, duo-posed.conf, lock.conf): read
and change them without disturbing comments or other keys. Pure."""


def parse(text):
    conf = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            key, value = line.split("=", 1)
            conf[key.strip()] = value.strip()
    return conf


def read(path):
    try:
        with open(path) as f:
            return parse(f.read())
    except OSError:
        return {}


def set_key(text, key, value):
    """`text` with `key = value`: the first active line for the key is
    replaced, else a commented-out one, else a line is appended."""
    lines = text.splitlines()
    out, done = [], False
    for line in lines:
        bare = line.strip()
        if (
            not done
            and "=" in bare
            and not bare.startswith("#")
            and bare.split("=", 1)[0].strip() == key
        ):
            out.append("%s = %s" % (key, value))
            done = True
        else:
            out.append(line)
    if not done:
        for i, line in enumerate(out):
            bare = line.strip().lstrip("#").strip()
            if "=" in bare and bare.split("=", 1)[0].strip() == key:
                out[i] = "%s = %s" % (key, value)
                done = True
                break
    if not done:
        out.append("%s = %s" % (key, value))
    return "\n".join(out) + "\n"


def is_on(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("on", "yes", "true", "1")
