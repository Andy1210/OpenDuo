# duo2-os root filesystem: Debian trixie arm64, lean (bring-up set).
# Built under qemu-user (binfmt), exported with `docker export`, then
# scripts/build-images.sh turns it into the ext4 + ESP images.
FROM --platform=linux/arm64 debian:trixie
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        systemd systemd-sysv systemd-resolved systemd-timesyncd udev dbus kmod \
        iproute2 iputils-ping iw wpasupplicant wireless-regdb bluez upower \
        openssh-server sudo less nano ca-certificates locales \
        qrtr-tools tqftpserv rmtfs hexagonrpcd iio-sensor-proxy \
        libprotobuf-c1 polkitd python3 \
        evtest usbutils pciutils e2fsprogs dosfstools parted libinput-tools \
        systemd-boot-efi systemd-boot-tools \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# Sensors: hexagonrpcd is Debian's, but the phone runs our patched build and
# iio-sensor-proxy 3.9 with SSC support from /usr/local (scripts/build-sensors.sh,
# installed below by install-components.sh), with the stock half built from the
# phone's own images (scripts/build-sensors-runtime.sh, a post-install step:
# scripts/install-sensors.sh; until then the units' conditions skip them). libssc
# needs libprotobuf-c1, iio-sensor-proxy's claims polkitd, duo-posed python3.
# The kernel's own PD mapper is used (CONFIG_QCOM_PD_MAPPER), so no userspace
# protection-domain-mapper: the two would fight over the same QRTR service.
# The desktop, duo-shell's side (shell/duo-shell/install.sh): PyGObject on
# GTK4 + layer shell, the on-screen keyboards (phosh-osk-stub with its word
# data, libpresage-data for English and hunspell-hu for Hungarian; squeekboard
# as the fallback), the GTK portal for dark libadwaita apps,
# pywayland for duo-lock; the accessibility bus (at-spi2-core: GTK 4.20 in
# Flatpak runtimes crashed without one, 2026-11-10); the wallpaper: GdkPixbuf's introspection, its JPEG XL
# loader and gnome-backgrounds (the built-in pictures, CC BY-SA 3.0, see
# shell/duo-shell/README.md). No recommends: squeekboard would pull in feedbackd.
# Audio (docs/hardware/audio.md): PipeWire + WirePlumber as the user's systemd
# units (the user lingers, below), the PulseAudio and ALSA plugs, BlueZ audio
# (A2DP, HFP), the UCM profiles (ours for the Duo 2 is in the overlay), rtkit,
# alsa-utils / pactl for the command line (the alsa-restore units are masked
# below: UCM sets the mixer, PipeWire keeps the volume). Browsers: Chromium
# and Firefox ESR, both native Wayland (overlay: /etc/chromium.d/duo2,
# /etc/firefox-esr/duo2.js), and KDE's Angelfish (QtWebEngine, the Chromium
# engine, with a phone UI; without Plasma: qt6-wayland, the SQLite driver for
# its history, Kirigami's desktop style and the KDE platform theme for dark
# Breeze colours: shell/duo-apps "Browsers").
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-gi python3-gi-cairo python3-cairo python3-cffi-backend python3-pywayland \
        gir1.2-gtk-4.0 gir1.2-gtk4layershell-1.0 libgtk4-layer-shell0 gir1.2-graphene-1.0 \
        gir1.2-pango-1.0 gir1.2-gdkpixbuf-2.0 libjxl-gdk-pixbuf gnome-backgrounds squeekboard phosh-osk-stub libpresage-data hunspell-hu dconf-gsettings-backend dconf-cli adwaita-icon-theme \
        dbus-daemon dbus-user-session at-spi2-core libglib2.0-bin librsvg2-common libnotify-bin wayland-protocols \
        xdg-desktop-portal xdg-desktop-portal-gtk xdg-desktop-portal-wlr grim gnome-text-editor wayland-utils \
        alsa-utils alsa-ucm-conf pipewire pipewire-pulse pipewire-alsa wireplumber \
        libspa-0.2-bluetooth pulseaudio-utils rtkit \
        chromium firefox-esr \
        angelfish qt6-wayland libqt6sql6-sqlite qml6-module-org-kde-desktop plasma-integration \
        kde-style-breeze \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# The preinstalled apps (shell/duo-apps/install.sh, no recommends): Clocks,
# Calculator, Calendar (+ evolution-data-server), Weather, Files (Nautilus;
# Portfolio too), Image Viewer (Loupe), Document Viewer (Papers), music
# (Amberol), video (Celluloid: mpv decodes into its own memory; GStreamer's
# players stutter at 1080p here, shell/duo-apps/README.md), the GStreamer
# codecs and GTK4's media backend (Clocks' alarm sound), the GNOME font
# (Cantarell, the schema default) and colour emoji; the app installer: GNOME
# Software with PackageKit (gnome-software-plugin-deb) and Flatpak, the apt
# AppStream data (appstream + the icon hooks). Duo Settings
# (shell/duo-settings) needs libadwaita's introspection (gir1.2-adw-1).
# Hardware video decoding (docs/hardware/video.md): mpv (Celluloid and mpv
# share /etc/mpv from the overlay), ffmpeg (its ffprobe tells the overlay's
# /etc/mpv/duo2-hwdec.lua what the video core can decode), the GStreamer V4L2
# decoders (plugins-good) and the tools to look at them (v4l-utils,
# gst-launch-1.0). The cameras (docs/hardware/camera.md): libcamera's simple pipeline
# with its software ISP (libcamera-ipa), cam (libcamera-tools), and the
# PipeWire and GStreamer sources; the sensors' register tables are a
# post-install step (scripts/build-camera-tables.sh --install).
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-cantarell fonts-noto-color-emoji appstream apt-config-icons apt-config-icons-hidpi \
        gnome-clocks gnome-calculator gnome-calendar evolution-data-server gnome-weather \
        nautilus portfolio-filemanager loupe papers amberol celluloid \
        mpv ffmpeg gstreamer1.0-plugins-good gstreamer1.0-tools v4l-utils \
        gstreamer1.0-libav gstreamer1.0-plugins-bad gstreamer1.0-pipewire libgtk-4-media-gstreamer \
        sound-theme-freedesktop \
        gnome-software gnome-software-plugin-deb gnome-software-plugin-flatpak flatpak \
        packagekit packagekit-tools \
        gir1.2-adw-1 \
        libcamera-tools libcamera-ipa libspa-0.2-libcamera gstreamer1.0-libcamera \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# The camera stack over trixie's (camera/build-debs.sh, docs/hardware/camera.md):
# libcamera 0.7.2 (trixie's 0.4.0 tints every picture green and has no helper
# for the sensors) and pipewire 1.4.2 rebuilt unchanged against it; of
# pipewire, the packages installed above (they need each other's version).
COPY camera-debs/ /tmp/camera-debs/
RUN cd /tmp/camera-debs && want= \
    && for f in *.deb; do p=${f%%_*}; case $p in \
        libcamera0.7|libcamera-ipa|libcamera-tools|libcamera-v4l2|gstreamer1.0-libcamera) want="$want ./$f" ;; \
        *) if dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed'; then want="$want ./$f"; fi ;; \
       esac; done \
    && apt-get update && apt-get install -y --no-install-recommends $want \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/camera-debs
# Telephony (telephony/README.md): ModemManager
# (the modem over QRTR and IPA: the qcom-soc plugin) and its introspection for
# duo2-wwan; GNOME Calls with callaudiod and feedbackd (Calls rings through
# feedbackd), Contacts, Chatty for SMS, mmsd-tng for MMS. No recommends (qmicli,
# which the modem hook uses, comes with libqmi-utils below).
RUN apt-get update && apt-get install -y --no-install-recommends \
        modemmanager gir1.2-modemmanager-1.0 gnome-calls callaudiod feedbackd gnome-contacts \
        chatty mmsd-tng mobile-broadband-provider-info \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# duo-wc's runtime (compositor/duo-wc/install.sh: seatd, foot, libdisplay-info2,
# xwayland for X11 apps: Krita forces Qt's xcb platform)
# and the bring-up tools, installed on the phone WITH their recommends (so the
# same here): GPU (kmscube, mesa-utils), input (evemu, wev), i2c, GPIO, network
# throughput, the QMI/MBIM proxies (libqmi-utils' recommends). The Vulkan
# driver (turnip) by name: libvulkan1 comes in above with GTK4, without the
# recommends that brought it onto the phone.
RUN apt-get update && apt-get install -y \
        seatd libseat1 libdisplay-info2 foot xwayland \
        kmscube mesa-utils mesa-utils-bin libgl1-mesa-dri libegl-mesa0 mesa-vulkan-drivers \
        evemu-tools wev i2c-tools gpiod iperf3 libqmi-utils \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# Profiling (perf: the power and GPU work used it on the phone; no recommends,
# as it was installed there).
RUN apt-get update && apt-get install -y --no-install-recommends linux-perf \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# Location (docs/hardware/location.md): geoclue by name (the phone has it, the shell is
# its agent; until now it came in as a dependency). Updates and backup
# (docs/UPDATES.md, maint/): rsync for duo2-backup, dpkg-repack for
# duo2-update's way back. No recommends.
RUN apt-get update && apt-get install -y --no-install-recommends geoclue-2.0 rsync dpkg-repack \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# The mobile hotspot (hotspot/README.md): hostapd for the access point, run by
# duo2-hotspotd with a config in /run (Debian's own units stay off). No
# recommends.
RUN apt-get update && apt-get install -y --no-install-recommends hostapd \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
COPY overlay/ /
# Android apps (docs/hardware/android.md): Waydroid from
# repo.waydro.id (the overlay brings the source, its key and the pin that lets
# only Waydroid and its binder libraries come from there), LXC, dnsmasq for the
# container's DHCP + DNS, nftables (the container's firewall), AppArmor (its
# confinement). No recommends. No Android image: `waydroid init` after
# installation (the quick start). LXC's own lxcbr0 network and container
# autostart are not used.
RUN apt-get update && apt-get install -y --no-install-recommends \
        waydroid lxc dnsmasq-base nftables apparmor \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && systemctl disable lxc-net.service lxc.service
# The boot splash (rootfs/plymouth, configured by install-components.sh):
# Plymouth with its DRM renderer; its initramfs-tools dependency never
# writes an initramfs here (update_initramfs=no: the kernel and the initrd are
# ours, on the ESP).
RUN apt-get update && apt-get install -y --no-install-recommends plymouth plymouth-label \
    && sed -i 's/^update_initramfs=.*/update_initramfs=no/' /etc/initramfs-tools/update-initramfs.conf \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
COPY modules/ /lib/modules/
COPY firmware/ /lib/firmware/
# Users (docs/hardware/security.md): DUO2_USER is the GUI user (duo-wc and
# every app run as it): no sudo, no `input` group (raw input devices: an app
# could read and inject touches, the lock's PIN pad too); seatd gives duo-wc
# the devices. The feedbackd group opens the vibration motor to feedbackd in
# its session (overlay: udev 70-duo2-vibra.rules). duo2admin is the
# administrator: sudo without a password (overlay: /etc/sudoers.d/duo2admin),
# no password, no GUI session; Settings > Developer manages its ssh keys. Both
# start with the build's authorized_keys (scripts/build-images.sh: empty unless
# AUTHORIZED_KEYS names a file). duo-posed gets its own system user
# (install-components.sh). The GUI user's ssh keys are root's file
# (/etc/ssh/authorized_keys.d/USER, overlay sshd_config.d/duo2.conf): an app
# could write its own ~/.ssh and log in outside the session's QRTR filter.
# sshd is enabled only with DEVELOPER_MODE=on (an image for a developer's
# own phone); otherwise Settings > Developer switches it on. DUO2_USER has no
# default here: scripts/build-images.sh always passes it (its default is in
# scripts/lib/defaults.sh).
ARG DUO2_USER
ARG DEVELOPER_MODE=off
COPY authorized_keys /tmp/authorized_keys
# duo2-os's own components (staged by scripts/build-images.sh): duo-wc,
# duo-shell, the app integration (duo-apps), the free half of the sensor
# stack, duo-posed, duo-brightness.
COPY components/ /tmp/components/
COPY install-components.sh /tmp/install-components.sh
RUN test -n "$DUO2_USER" \
    && for g in video input render plugdev netdev; do getent group $g >/dev/null || groupadd -r $g; done \
    && useradd -m -s /bin/bash -G video,render,plugdev,netdev,audio,feedbackd "$DUO2_USER" \
    && install -d /var/lib/systemd/linger && touch "/var/lib/systemd/linger/$DUO2_USER" \
    && passwd -l "$DUO2_USER" \
    && install -d -m 755 /etc/ssh/authorized_keys.d \
    && install -m 644 /tmp/authorized_keys "/etc/ssh/authorized_keys.d/$DUO2_USER" \
    && useradd -m -s /bin/bash -U -c "duo2-os admin (ssh only, no GUI session)" duo2admin \
    && usermod -p '*' duo2admin \
    && install -d -m 700 -o duo2admin -g duo2admin /home/duo2admin/.ssh \
    && install -m 600 -o duo2admin -g duo2admin /tmp/authorized_keys /home/duo2admin/.ssh/authorized_keys \
    && chmod 0440 /etc/sudoers.d/duo2admin && visudo -cq \
    && rm /tmp/authorized_keys \
    && locale-gen \
    && DEVELOPER_MODE="$DEVELOPER_MODE" bash /tmp/install-components.sh /tmp/components "$DUO2_USER" \
    && rm -rf /tmp/components /tmp/install-components.sh \
    && systemctl disable wpa_supplicant.service \
    && systemctl enable duo2-growfs-once.service \
    && systemctl enable systemd-networkd systemd-resolved systemd-timesyncd \
        duo2-usb-gadget serial-getty@ttyGS0 duo2-factory-addr duo-firewall \
    && if [ "$DEVELOPER_MODE" = on ]; then systemctl enable ssh; \
       else systemctl disable ssh ssh.socket 2>/dev/null || systemctl disable ssh; fi \
    && systemctl enable seatd duo-wc duo2-touchd duo2-pend hexagonrpcd duo-posed duo-brightness \
    && systemctl set-default graphical.target \
    && systemctl disable rmtfs.service \
    && systemctl mask alsa-restore.service alsa-state.service systemd-bless-boot.service getty@tty1.service \
    && ln -sf /dev/null /etc/udev/rules.d/90-alsa-restore.rules \
    && for k in /lib/modules/*; do [ ! -d "$k" ] || depmod -a "$(basename "$k")"; done \
    && rm -f /etc/ssh/ssh_host_* \
    && printf '[Unit]\nConditionPathExists=!/etc/ssh/ssh_host_ed25519_key\n[Service]\nType=oneshot\nExecStart=/usr/bin/ssh-keygen -A\n[Install]\nWantedBy=multi-user.target\n' > /etc/systemd/system/duo2-ssh-hostkeys.service \
    && mkdir -p /etc/systemd/system/ssh.service.d \
    && printf '[Unit]\nAfter=duo2-ssh-hostkeys.service\nWants=duo2-ssh-hostkeys.service\n' > /etc/systemd/system/ssh.service.d/hostkeys.conf \
    && systemctl enable duo2-ssh-hostkeys \
    && : > /etc/machine-id
COPY duo2-release /etc/duo2-release
COPY install-os-release.sh /tmp/install-os-release.sh
RUN sh /tmp/install-os-release.sh && rm /tmp/install-os-release.sh
