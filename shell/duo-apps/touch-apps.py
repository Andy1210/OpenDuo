#!/usr/bin/env python3
"""List the apps an AppStream catalogue marks as fit for a phone.

    touch-apps.py Components-arm64.yml.gz     Debian's DEP-11 (apt)
    touch-apps.py appstream.xml.gz            a Flatpak remote's catalogue

An app qualifies when it declares touch input (`control: touch` under
requires / recommends / supports) AND a small display (`display_length` with
a `<=` relation, or a plain number, of at most 420 logical px, or the old
names "small" / "xsmall"). Prints one line per app: the id, the package (or
bundle) and the name. The curated list (works-on-duo.txt) starts from this
and keeps only what was tried on the phone, plus apps that work but do not
say so (browsers).
"""

import gzip
import sys
import xml.etree.ElementTree as ET

SMALL = 420


def small_length(rel, value):
    value = str(value).strip()
    if value in ("small", "xsmall"):
        return True
    try:
        px = int(value)
    except ValueError:
        return False
    return rel in ("le", "lt", "", None) and px <= SMALL


def from_dep11(path):
    import yaml

    with gzip.open(path, "rt") as f:
        for doc in yaml.safe_load_all(f):
            if not doc or doc.get("Type") not in ("desktop-application", "web-application"):
                continue
            touch = small = False
            for kind in ("Requires", "Recommends", "Supports"):
                for item in doc.get(kind) or []:
                    if item.get("control") == "touch":
                        touch = True
                    dl = item.get("display_length")
                    if isinstance(dl, dict):
                        v = dl.get("value", dl.get("px"))
                        if small_length(dl.get("compare", "le"), v):
                            small = True
                    elif dl is not None and small_length("le", dl):
                        small = True
            if touch and small:
                name = doc.get("Name", {}).get("C", "")
                yield doc["ID"], doc.get("Package", ""), name


def from_xml(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as f:
        root = ET.parse(f).getroot()
    for c in root.iter("component"):
        if c.get("type") not in ("desktop-application", "desktop", "web-application"):
            continue
        touch = small = False
        for kind in ("requires", "recommends", "supports"):
            for sec in c.findall(kind):
                for ctl in sec.findall("control"):
                    if (ctl.text or "").strip() == "touch":
                        touch = True
                for dl in sec.findall("display_length"):
                    if small_length(dl.get("compare", "le"), dl.text or ""):
                        small = True
        if touch and small:
            bundle = c.findtext("bundle") or ""
            yield c.findtext("id"), bundle, c.findtext("name") or ""


def main():
    path = sys.argv[1]
    rows = from_dep11(path) if ".yml" in path else from_xml(path)
    for app_id, pkg, name in sorted(set(rows)):
        print(f"{app_id}\t{pkg}\t{name}")


if __name__ == "__main__":
    main()
