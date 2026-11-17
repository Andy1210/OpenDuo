"""The screenshot gate's command line (pure).

xdg-desktop-portal-wlr's Screenshot runs `grim` by name, three ways:

    grim /tmp/out.png                          (a screenshot)
    grim -g "$(slurp)" -- /tmp/out.png         (interactive: a region)
    grim -g "$(slurp -p)" -t ppm -             (PickColor: one pixel)

Its PATH puts the gate first; the gate asks the user (the chooser), then
runs the real grim with a capture connection. There is no slurp (it needs
the layer shell): `-g` comes empty and is ignored; a colour pick (a ppm to
standard output) is refused, since there is no way to point at a pixel.
"""

REAL_GRIM = "/usr/bin/grim"

# grim 1.4's options that take a value.
VALUED = {"-s", "-g", "-t", "-q", "-l", "-o"}


class Refused(Exception):
    """A request the gate does not run."""


def parse(argv):
    """(options as {flag: value or True}, path) from grim's arguments."""
    opts = {}
    path = None
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--":
            if args:
                path = args.pop(0)
            break
        if arg in VALUED:
            if not args:
                raise Refused("%s needs a value" % arg)
            opts[arg] = args.pop(0)
        elif arg in ("-c", "-h"):
            opts[arg] = True
        elif arg.startswith("-") and arg != "-":
            raise Refused("unknown option %s" % arg)
        else:
            path = arg
            break
    if args:
        raise Refused("more than one output file")
    return opts, path


def real_command(argv, answer, scale=None):
    """The real grim's arguments for the user's answer (an output name, or
    `all`): the region dropped, the chosen output set, the panels' own scale
    (else grim takes the integer wl_output scale and enlarges the picture)."""
    opts, path = parse(argv)
    if opts.get("-h"):
        raise Refused("help")
    if opts.get("-t") == "ppm" and path in (None, "-"):
        raise Refused("colour picking is not available")
    if path is None:
        raise Refused("no output file")
    cmd = [REAL_GRIM]
    if "-s" in opts:
        cmd += ["-s", opts["-s"]]
    elif scale:
        cmd += ["-s", "%g" % scale]
    for flag in ("-t", "-q", "-l"):
        if flag in opts:
            cmd += [flag, opts[flag]]
    if opts.get("-c"):
        cmd.append("-c")
    if answer != "all":
        cmd += ["-o", answer]
    cmd += ["--", path]
    return cmd
