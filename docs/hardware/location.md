# Location

Native apps (GNOME Weather, Clocks, Maps, Flatpaks through the portal) get
their position from geoclue; Android apps (Waydroid) from a bridge. No
coordinates or cell IDs are written to logs by the code.

| Where | Path | Status |
|---|---|---|
| native apps | geoclue -> ModemManager: the modem's GNSS (`gps-nmea`), the cell (`3gpp-lac-ci` -> BeaconDB), IP estimate | works; no real outdoor fix measured |
| Android apps | geoclue -> `duo2-android-location` -> the Android test providers (`gps`, `network`) over binder | written, not yet run on the phone |
| A-GPS (XTRA) | `duo2-gnss-assist`: downloads from the server the modem names, injected through ModemManager | written, timer off by default |
| "location in use" | an icon in the shell's bar and the quick menu tile ("In use"), from geoclue's `InUse` | written |

## Facts the code relies on

- The GNSS engine is the modem's (Qualcomm, QMI LOC through ModemManager).
  geoclue switches it on (`gps-nmea`) only when an app asks for exact
  location, and off when none does; if geoclue dies,
  `geoclue.service.d/duo2.conf`'s `ExecStopPost` switches it off.
- Cell location uses BeaconDB without a key and uploads nothing; before a
  cell fix the IP-based estimate (~25 km) is what geoclue returns. WiFi-based
  location is off (`wpa_supplicant@wlan0` runs without D-Bus).
- The modem reports `3gpp-lac-ci, gps-raw, gps-nmea, agps-msa, agps-msb` and
  supports `xtra` assistance data. The GNSS update interval is 30 s by
  default; geoclue sets it to the client's `TimeThreshold` (ours is 0).
- The shell is geoclue's agent: it asks for sandboxed apps, and Debian-packaged
  apps can be disabled one by one in Settings > Privacy.
- The Android bridge (`android/duo2-android-location`) takes a logind sleep
  inhibitor while an Android app uses the position, so the phone does not
  suspend during navigation.

`scripts/duo2-android-location-test` watches the bridge; `duo2-location-test`
shows the update interval of the native path.
