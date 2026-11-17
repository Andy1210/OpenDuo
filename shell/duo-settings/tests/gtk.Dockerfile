# GTK 4 + libadwaita as on the phone (Debian trixie), for the page tests
# that need a display: tests/run-gtk.sh runs them under a broadway display.
FROM debian:trixie
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3-gi python3-gi-cairo python3-cairo gir1.2-gtk-4.0 gir1.2-adw-1 libgtk-4-bin \
        adwaita-icon-theme fonts-cantarell gsettings-desktop-schemas && \
    rm -rf /var/lib/apt/lists/*
