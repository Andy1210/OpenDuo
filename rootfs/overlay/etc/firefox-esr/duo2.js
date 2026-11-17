// duo2-os: Firefox ESR on duo-wc. Read with Debian's firefox-esr.js (every
// .js file in /etc/firefox-esr is). Firefox runs on Wayland by itself
// (WAYLAND_DISPLAY set, no X server); scrolling and pinch-zoom with fingers
// are its own (APZ).

// Web pages get touch events (the default, "auto", may decide there is no
// touchscreen).
pref("dom.w3c_touch_events.enabled", 1);
// No "make Firefox your default browser" bar.
pref("browser.shell.checkDefaultBrowser", false);
