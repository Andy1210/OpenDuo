"""Build OpenDuo's .deb files from staged inputs (packaging/build-packages.sh
runs this in its builder container; docs/UPDATES.md).

  python3 -m openduo_pkg.build components  --stage DIR --out DIR --version V --epoch E [--only PKG...]
  python3 -m openduo_pkg.build release     --stamp FILE --changelog FILE --os-release FILE --out DIR --version V --epoch E
  python3 -m openduo_pkg.build keyring     --key FILE.asc --url URL --out DIR --version V --epoch E
  python3 -m openduo_pkg.build kernel      --release R --kversion KV --modules DIR --boot DIR --out DIR --epoch E
  python3 -m openduo_pkg.build meta        --out DIR --version V --kversion KV --release R --epoch E

Every file gets the commit's time (SOURCE_DATE_EPOCH) and root:root, the
archives are built with dpkg-deb from sorted trees: the same inputs give the
same .deb.
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGING = os.path.dirname(HERE)
TEMPLATES = os.path.join(PACKAGING, "templates")
MANIFEST = os.path.join(PACKAGING, "packages.toml")
DEVICE = "surface-duo2"
NAME = re.compile(r"^[a-z0-9][a-z0-9+.-]+$")
DEFAULT_MAINTAINER = "OpenDuo <noreply@example.org>"
HOMEPAGE_DEFAULT = ""
COMPONENT_DIR = "/usr/lib/openduo/components"
KERNEL_PKG = "openduo-kernel-" + DEVICE
DUO2_DTBS = re.compile(r"^sm8350-microsoft-surface-duo2.*\.dtb$")
KERNEL_DEFAULTS = (
    "# The kernel package's defaults (duo2-boot uses them when /etc/duo2/boot.conf\n"
    "# and the known-good duo2.conf say nothing): scripts/build-esp.sh's duo2.conf.\n"
    'DTB="sm8350-microsoft-surface-duo2-bringup-j.dtb"\n'
    'INITRD="/duo2/initrd-boot.img"\n'
    'OPTIONS="fw_devlink.sync_state=timeout mem_sleep_default=s2idle panic=30 console=tty0 quiet '
    "duo2.boot=root rw fw_devlink=permissive arm-smmu.disable_bypass=0 printk.devkmsg=on "
    'hung_task_panic=1 softlockup_panic=1 nmi_watchdog=panic fbcon=map:1 vt.global_cursor_default=0"\n'
)
LICENSES = {
    "GPL-3.0-or-later": "/usr/share/common-licenses/GPL-3",
    "GPL-2.0-only": "/usr/share/common-licenses/GPL-2",
    "Apache-2.0": "/usr/share/common-licenses/Apache-2.0",
    # Debian ships no common copy of the MIT license: its text goes into the file itself.
    "MIT": None,
}

MIT_TEXT = """Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


# -- the manifest ------------------------------------------------------------------------------


def load_manifest(path=MANIFEST):
    with open(path, "rb") as f:
        data = tomllib.load(f)
    pkgs = data.get("package", {})
    for name, spec in pkgs.items():
        problems = check_spec(name, spec)
        if problems:
            raise ValueError("%s: %s" % (name, "; ".join(problems)))
    return pkgs


def check_spec(name, spec):
    p = []
    if not NAME.match(name) or not name.startswith("openduo-"):
        p.append("a package name starts with openduo- and is lower case")
    if spec.get("device") and not name.endswith("-" + DEVICE):
        p.append("a device package's name ends in -%s" % DEVICE)
    if not spec.get("device") and DEVICE in name:
        p.append("device = true for a %s package" % DEVICE)
    if spec.get("arch") not in ("all", "arm64"):
        p.append("arch is all or arm64")
    for key in ("component", "summary", "description"):
        if not spec.get(key):
            p.append("no %s" % key)
    for key in ("depends", "recommends", "enable", "restart"):
        if not all(
            isinstance(x, str) and re.match(r"^[A-Za-z0-9@+._:-]+$", x) for x in spec.get(key, [])
        ):
            p.append("%s: plain names only" % key)
    if spec.get("license", "GPL-3.0-or-later") not in LICENSES:
        p.append("unknown license")
    return p


# -- control files -----------------------------------------------------------------------------


def description(summary, long_text):
    lines = [summary.strip()]
    for para in long_text.strip().split("\n\n"):
        for line in para.splitlines():
            lines.append(" " + line.strip())
        lines.append(" .")
    if lines[-1] == " .":
        lines.pop()
    return "\n".join(lines)


def control_text(fields):
    """Ordered fields -> a DEBIAN/control file. Empty values are left out;
    a value with lines is written as is (its continuation lines indented)."""
    out = []
    for k, v in fields:
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, list):
            v = ", ".join(v)
        if v.startswith("\n"):  # a field that starts on its continuation lines
            out.append("%s:%s" % (k, v))
        else:
            out.append("%s: %s" % (k, v))
    return "\n".join(out) + "\n"


def render(template, values):
    with open(os.path.join(TEMPLATES, template)) as f:
        text = f.read()
    for k, v in values.items():
        text = text.replace("@%s@" % k, v)
    left = re.findall(r"@[A-Z_]+@", text)
    if left:
        raise ValueError("%s: unfilled %s" % (template, ", ".join(sorted(set(left)))))
    return text


def copyright_text(pkg, license_id):
    head = (
        "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n"
        "Upstream-Name: OpenDuo (%s)\n\n"
        "Files: *\nCopyright: The OpenDuo authors\nLicense: %s\n" % (pkg, license_id)
    )
    if LICENSES[license_id] is None:
        body = "".join(" %s\n" % (line or ".") for line in MIT_TEXT.splitlines())
        return head + body
    return head + " On Debian systems the full text is in %s.\n" % LICENSES[license_id]


# -- the archive -------------------------------------------------------------------------------


def clean_tree(root, epoch):
    """Modes without group / other write, the commit's time on everything."""
    for d, dirs, files in os.walk(root, topdown=False):
        for n in files + dirs:
            p = os.path.join(d, n)
            if not os.path.islink(p):
                os.chmod(p, os.lstat(p).st_mode & 0o7755)
            os.utime(p, (epoch, epoch), follow_symlinks=False)
    os.chmod(root, 0o755)
    os.utime(root, (epoch, epoch))


def md5sums(root):
    lines = []
    for d, dirs, files in os.walk(root):
        dirs.sort()
        if os.path.relpath(d, root).split(os.sep)[0] == "DEBIAN":
            continue
        for n in sorted(files):
            p = os.path.join(d, n)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            with open(p, "rb") as f:
                lines.append(
                    "%s  %s" % (hashlib.md5(f.read()).hexdigest(), os.path.relpath(p, root))
                )
    return "\n".join(sorted(lines, key=lambda s: s[34:])) + ("\n" if lines else "")


def installed_size(root):
    total = 0
    for d, dirs, files in os.walk(root):
        if os.path.relpath(d, root).split(os.sep)[0] == "DEBIAN":
            continue
        total += len(dirs)
        for n in files:
            p = os.path.join(d, n)
            total += 1 if os.path.islink(p) else (os.lstat(p).st_size + 1023) // 1024
    return max(total, 1)


def build_deb(root, fields, outdir, epoch, scripts=None, conffiles=None, triggers=None):
    """Write DEBIAN/ into root and build root into outdir; the .deb's path."""
    deb = os.path.join(root, "DEBIAN")
    os.makedirs(deb, exist_ok=True)
    for name, text in (scripts or {}).items():
        with open(os.path.join(deb, name), "w") as f:
            f.write(text)
        os.chmod(os.path.join(deb, name), 0o755)
    if conffiles:
        with open(os.path.join(deb, "conffiles"), "w") as f:
            f.write("".join(c + "\n" for c in conffiles))
    if triggers:
        with open(os.path.join(deb, "triggers"), "w") as f:
            f.write("".join(t + "\n" for t in triggers))
    sums = md5sums(root)
    if sums:
        with open(os.path.join(deb, "md5sums"), "w") as f:
            f.write(sums)
    fields = list(fields)
    i = [k for k, _v in fields].index("Maintainer")
    fields.insert(i + 1, ("Installed-Size", str(installed_size(root))))
    with open(os.path.join(deb, "control"), "w") as f:
        f.write(control_text(fields))
    clean_tree(root, epoch)
    for n in os.listdir(deb):
        os.chmod(os.path.join(deb, n), 0o755 if n in (scripts or {}) else 0o644)
    d = dict(fields)
    out = os.path.join(
        outdir, "%s_%s_%s.deb" % (d["Package"], d["Version"].split(":")[-1], d["Architecture"])
    )
    os.makedirs(outdir, exist_ok=True)
    env = dict(os.environ, SOURCE_DATE_EPOCH=str(epoch))
    subprocess.run(
        ["dpkg-deb", "--root-owner-group", "-Zxz", "-z6", "--build", root, out],
        check=True,
        env=env,
        stdout=subprocess.DEVNULL,
    )
    return out


def doc(root, pkg, license_id="GPL-3.0-or-later", extra=None):
    d = os.path.join(root, "usr/share/doc", pkg)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "copyright"), "w") as f:
        f.write(copyright_text(pkg, license_id))
    for name, src in (extra or {}).items():
        shutil.copyfile(src, os.path.join(d, name))


def base_fields(pkg, version, arch, args, section="misc", priority="optional"):
    return [
        ("Package", pkg),
        ("Version", version),
        ("Architecture", arch),
        ("Maintainer", args.maintainer),
        ("Section", section),
        ("Priority", priority),
    ]


def tail_fields(summary, long_text, args):
    out = []
    if args.homepage:
        out.append(("Homepage", args.homepage))
    out.append(("Description", description(summary, long_text)))
    return out


# -- the packages ------------------------------------------------------------------------------


def component_scripts(pkg, spec):
    env = " ".join("%s=%s" % (k, v) for k, v in sorted(spec.get("env", {}).items()))
    values = {
        "PKG": pkg,
        "COMPONENT": spec["component"],
        "USER_LINE": "U=$(gui_user)" if spec.get("user") else ": no GUI user needed",
        "ARGS": '"$U"' if spec.get("user") else "",
        "ENV": ("env %s " % env) if env else "",
        "ENABLE": " ".join(spec.get("enable", [])) or "$(:)",
        "RESTART": " ".join(spec.get("restart", [])) or "$(:)",
        "REBOOT": "yes" if spec.get("reboot") else "no",
    }
    return {"postinst": render("component.postinst", values)}


def build_component(pkg, spec, stage, outdir, args, work):
    src = os.path.join(stage, spec["component"])
    if not os.path.isfile(os.path.join(src, "install-files.sh")):
        raise SystemExit("%s: %s is not staged (no install-files.sh)" % (pkg, src))
    root = os.path.join(work, pkg)
    shutil.rmtree(root, ignore_errors=True)
    dst = os.path.join(root, COMPONENT_DIR.lstrip("/"), spec["component"])
    shutil.copytree(src, dst, symlinks=True)
    doc(root, pkg, spec.get("license", "GPL-3.0-or-later"))
    fields = base_fields(pkg, args.version, spec["arch"], args)
    fields += [("Depends", spec.get("depends", [])), ("Recommends", spec.get("recommends", []))]
    fields += tail_fields(spec["summary"], spec["description"], args)
    return build_deb(root, fields, outdir, args.epoch, scripts=component_scripts(pkg, spec))


def cmd_components(args):
    pkgs = load_manifest(args.manifest)
    only = set(args.only or [])
    unknown = only - set(pkgs)
    if unknown:
        raise SystemExit("unknown packages: %s" % ", ".join(sorted(unknown)))
    for pkg, spec in sorted(pkgs.items()):
        if only and pkg not in only:
            continue
        print(build_component(pkg, spec, args.stage, args.out, args, args.work))


def changes_field(changelog_md, sections=3):
    """The newest CHANGELOG.md sections as a control field's value."""
    sys.path.insert(0, os.path.join(PACKAGING, "..", "maint"))
    from duo2maint import updates as up

    secs = up.changelog_sections(changelog_md, limit=sections)
    text = "\n\n".join(
        "## [%s]%s\n%s" % (s["version"], " - " + s["date"] if s["date"] else "", s["text"])
        for s in secs
    )
    return up.encode_field(text)


def cmd_release(args):
    pkg = "openduo-release"
    root = os.path.join(args.work, pkg)
    shutil.rmtree(root, ignore_errors=True)
    d = os.path.join(root, "usr/lib/openduo/release")
    os.makedirs(d)
    shutil.copyfile(args.stamp, os.path.join(d, "duo2-release"))
    shutil.copyfile(args.os_release, os.path.join(d, "install-os-release.sh"))
    doc(root, pkg, extra={"CHANGELOG.md": args.changelog})
    with open(args.changelog) as f:
        md = f.read()
    fields = base_fields(pkg, args.version, "all", args, section="admin")
    fields += [("Depends", ["base-files", "dpkg"])]
    fields += [("OpenDuo-Changes", "\n" + changes_field(md))]
    fields += tail_fields(
        "OpenDuo's version and build stamp",
        "/etc/duo2-release (Settings > About, bug reports) and /etc/os-release\n"
        "(NAME=OpenDuo, ID_LIKE=debian). Its OpenDuo-Changes field carries the\n"
        "newest CHANGELOG.md sections for Settings > Updates.",
        args,
    )
    print(
        build_deb(
            root,
            fields,
            args.out,
            args.epoch,
            scripts={"postinst": render("release.postinst", {"PKG": pkg})},
        )
    )


PINNED = [
    "libcamera0.7",
    "libcamera-ipa",
    "libcamera-tools",
    "libcamera-v4l2",
    "gstreamer1.0-libcamera",
    "pipewire",
    "pipewire-*",
    "libpipewire-*",
    "libspa-0.2-*",
    "gstreamer1.0-pipewire",
]


def sources_text(url):
    return (
        "# OpenDuo's own packages (docs/UPDATES.md); Debian's repositories stay for the base.\n"
        "# The testing channel is a second file beside this one (Settings > Updates,\n"
        "# developer mode: openduo-testing.sources).\n"
        "Types: deb\nURIs: %s\nSuites: stable\nComponents: main\nArchitectures: arm64\n"
        "Signed-By: /usr/share/keyrings/openduo-archive-keyring.asc\n" % url
    )


def preferences_text():
    return (
        "# OpenDuo builds of Debian packages win over Debian's own (docs/UPDATES.md):\n"
        "# libcamera 0.7.2 (trixie's 0.4.0 has no helpers for these sensors) and\n"
        "# pipewire rebuilt against it. A Debian security fix to them means a\n"
        "# rebuild (camera/build-debs.sh; duo2-update check says so).\n"
        "Package: %s\nPin: release o=OpenDuo\nPin-Priority: 990\n" % " ".join(PINNED)
    )


def cmd_keyring(args):
    pkg = "openduo-archive-keyring"
    if not re.match(r"^(https://|file:/)[^\s]+$", args.url or ""):
        raise SystemExit("keyring: --url must be the repository's https URL (file:/ for a test)")
    root = os.path.join(args.work, pkg)
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(os.path.join(root, "usr/share/keyrings"))
    shutil.copyfile(args.key, os.path.join(root, "usr/share/keyrings/openduo-archive-keyring.asc"))
    os.makedirs(os.path.join(root, "etc/apt/sources.list.d"))
    os.makedirs(os.path.join(root, "etc/apt/preferences.d"))
    with open(os.path.join(root, "etc/apt/sources.list.d/openduo.sources"), "w") as f:
        f.write(sources_text(args.url.rstrip("/")))
    with open(os.path.join(root, "etc/apt/preferences.d/openduo"), "w") as f:
        f.write(preferences_text())
    doc(root, pkg)
    fields = base_fields(pkg, args.version, "all", args, section="admin", priority="important")
    fields += [("Depends", ["apt (>= 2.6)"])]
    fields += tail_fields(
        "OpenDuo's repository key, its sources and pins",
        "The OpenDuo archive signing key, the deb822 sources file (signed-by) and the\n"
        "pins that keep OpenDuo's rebuilds of Debian packages.",
        args,
    )
    print(
        build_deb(
            root,
            fields,
            args.out,
            args.epoch,
            conffiles=["/etc/apt/sources.list.d/openduo.sources", "/etc/apt/preferences.d/openduo"],
        )
    )


def cmd_kernel(args):
    rel = args.release
    pkg = "%s-%s" % (KERNEL_PKG, rel)
    if not NAME.match(pkg):
        raise SystemExit("kernel: %s is not a package name" % pkg)
    root = os.path.join(args.work, pkg)
    shutil.rmtree(root, ignore_errors=True)
    mods = os.path.join(args.modules, rel)
    if not os.path.isdir(mods):
        raise SystemExit("kernel: no modules in %s" % mods)
    shutil.copytree(
        mods,
        os.path.join(root, "usr/lib/modules", rel),
        symlinks=True,
        ignore=shutil.ignore_patterns(
            "build",
            "source",
            "modules.alias*",
            "modules.dep*",
            "modules.symbols*",
            "modules.devname",
            "modules.softdep",
            "modules.weakdep",
            "modules.builtin.alias.bin",
            "modules.builtin.bin",
        ),
    )
    k = os.path.join(root, "usr/lib/openduo/kernel", rel)
    os.makedirs(os.path.join(k, "dtbs/qcom"))
    shutil.copyfile(os.path.join(args.boot, "Image"), os.path.join(k, "Image"))
    dtbs = sorted(n for n in os.listdir(os.path.join(args.boot, "dts/qcom")) if DUO2_DTBS.match(n))
    if not dtbs:
        raise SystemExit("kernel: no Surface Duo 2 DTB in %s" % args.boot)
    for n in dtbs:
        shutil.copyfile(os.path.join(args.boot, "dts/qcom", n), os.path.join(k, "dtbs/qcom", n))
    if args.config:
        shutil.copyfile(args.config, os.path.join(k, "config"))
    with open(os.path.join(k, "boot.env"), "w") as f:
        f.write(KERNEL_DEFAULTS)
    doc(root, pkg, "GPL-2.0-only")
    fields = base_fields(pkg, args.kversion, "arm64", args, section="kernel")
    fields += [("Depends", ["kmod", "openduo-maint"]), ("Provides", ["openduo-kernel"])]
    fields += tail_fields(
        "OpenDuo's Linux %s for the Surface Duo 2" % rel,
        "The kernel Image, the Surface Duo 2 device trees and the modules of the build\n"
        "%s. Installing it puts it on the ESP with its own boot entry, tried\n"
        "three times before the previous kernel comes back (duo2-boot)." % rel,
        args,
    )
    scripts = {
        n: render("kernel." + n, {"PKG": pkg, "RELEASE": rel})
        for n in ("postinst", "prerm", "postrm")
    }
    print(build_deb(root, fields, args.out, args.epoch, scripts=scripts))


def cmd_meta(args):
    pkgs = load_manifest(args.manifest)
    if args.release:
        root = os.path.join(args.work, KERNEL_PKG)
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root)
        doc(root, KERNEL_PKG, "GPL-2.0-only")
        fields = base_fields(KERNEL_PKG, args.kversion, "arm64", args, section="kernel")
        fields += [("Depends", ["%s-%s (= %s)" % (KERNEL_PKG, args.release, args.kversion)])]
        fields += tail_fields(
            "OpenDuo's kernel for the Surface Duo 2 (always the newest)",
            "Depends on the newest kernel build; older ones stay until\n"
            "duo2-updates removes them (the previous good one is kept).",
            args,
        )
        print(build_deb(root, fields, args.out, args.epoch))
    pkg = "openduo-" + DEVICE
    root = os.path.join(args.work, pkg)
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    doc(root, pkg)
    deps = ["%s (>= %s)" % (n, args.version) for n in sorted(pkgs)] + [
        "openduo-release (>= %s)" % args.version,
        "openduo-archive-keyring",
        KERNEL_PKG,
    ]
    fields = base_fields(pkg, args.version, "arm64", args, section="metapackages")
    fields += [("Depends", deps)]
    fields += tail_fields(
        "OpenDuo for the Surface Duo 2: the whole system",
        "Every OpenDuo package for the Surface Duo 2, its kernel and the\nrepository's key.",
        args,
    )
    print(build_deb(root, fields, args.out, args.epoch))


def main(argv):
    ap = argparse.ArgumentParser(prog="openduo_pkg.build")
    ap.add_argument("what", choices=["components", "release", "keyring", "kernel", "meta"])
    ap.add_argument("--manifest", default=MANIFEST)
    ap.add_argument("--stage")
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", default=None)
    ap.add_argument("--version")
    ap.add_argument("--kversion")
    ap.add_argument("--epoch", type=int, required=True)
    ap.add_argument(
        "--maintainer", default=os.environ.get("OPENDUO_MAINTAINER") or DEFAULT_MAINTAINER
    )
    ap.add_argument("--homepage", default=os.environ.get("OPENDUO_HOMEPAGE") or HOMEPAGE_DEFAULT)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--stamp")
    ap.add_argument("--changelog")
    ap.add_argument("--os-release")
    ap.add_argument("--key")
    ap.add_argument("--url")
    ap.add_argument("--release")
    ap.add_argument("--modules")
    ap.add_argument("--boot")
    ap.add_argument("--config")
    args = ap.parse_args(argv)
    args.work = args.work or os.path.join(args.out, ".work")
    for v in (args.version, args.kversion):
        if v and not re.match(r"^[0-9][0-9A-Za-z.+~]*$", v):
            raise SystemExit("not a package version: %r" % v)
    {
        "components": cmd_components,
        "release": cmd_release,
        "keyring": cmd_keyring,
        "kernel": cmd_kernel,
        "meta": cmd_meta,
    }[args.what](args)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
