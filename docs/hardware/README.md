# Hardware notes

Short notes on the subsystems of the Surface Duo 2 that the code in this
repository depends on: what works, the quirks the code works around, and how
to debug. They record what was measured on a real phone; where something was
not tested, the note says so. Status tables are in the top-level
[README](../../README.md).

| Note | Covers |
|---|---|
| [audio.md](audio.md) | speakers, microphones, UCM, 24-bit PCM, call audio |
| [boot.md](boot.md) | seamless boot, early display from the initramfs, read-only ESP |
| [camera.md](camera.md) | the four sensors, libcamera, orientation and colour |
| [display.md](display.md) | panels, 90 Hz, external display over USB-C |
| [location.md](location.md) | geoclue, the modem's GNSS, the Android bridge |
| [power.md](power.md) | automatic sleep, wake sources, WoWLAN, charge hold |
| [security.md](security.md) | who may do what: the one-user model, trust, QRTR |
| [touch.md](touch.md) | the digitizer, `duo2-touchd`, panel edges, the pen |
| [video.md](video.md) | hardware video decoding, browsers, YouTube |
| [android.md](android.md) | Android apps in Waydroid |

Updates and the package repository: [../UPDATES.md](../UPDATES.md).
