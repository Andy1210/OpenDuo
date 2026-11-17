# GTK 4 + gtk4-layer-shell's typelib as on the phone (Debian trixie), for the
# tests that lay the shell's widgets out: tests/run-gtk.sh runs them under a
# broadway display (no layer surface is made; the import needs the typelib).
FROM debian:trixie
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3-gi python3-gi-cairo python3-cairo gir1.2-gtk-4.0 gir1.2-gtk4layershell-1.0 \
        gir1.2-graphene-1.0 libgtk-4-bin librsvg2-common adwaita-icon-theme fonts-cantarell \
        gsettings-desktop-schemas && \
    rm -rf /var/lib/apt/lists/*
