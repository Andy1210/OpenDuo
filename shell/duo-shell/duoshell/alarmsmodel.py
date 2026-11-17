"""Tapping a clock (the status bar's, the home screen's) opens GNOME Clocks
on its Alarms page (pure, tests/test_alarmsmodel.py; Shell.open_alarms does
it).

Clocks 48 has no command line option, D-Bus method or exported action for a
page: its window's page switch ("set-page", Alt+2) is a widget action that
is not on the bus. What it has is its state: a window takes its page from
GSettings (org.gnome.clocks.state.window `panel-id`) when the window is
made, and writes the page it shows back there when it closes. So:

- Clocks not running, or running in the background without a window (the
  session starts it that way when alarms are set): `panel-id` = alarm, then
  Clocks is launched like any app (its desktop file: D-Bus activation, the
  activation token, the panel tapped); its new window opens on Alarms;
- a Clocks window is up: it is asked to close (duo-wc's close_window, as
  its X in the overview; Clocks stays running), and once it is gone, the
  same as above: the window comes back on Alarms, on the panel tapped;
- an alarm is ringing (Clocks' urgent notification is there): its window
  only comes to the front, so the ringing alarm is never closed.
"""

APP_ID = "org.gnome.clocks"
DESKTOP_ID = "org.gnome.clocks.desktop"
STATE_SCHEMA = "org.gnome.clocks.state.window"
PAGE_KEY = "panel-id"
PAGE = "alarm"
CLOSE_WAIT_S = 3.0  # how long a window may take to close
SETTLE_S = 0.3  # then Clocks' own write of its page lands first


def clocks_windows(windows):
    return [w for w in windows or [] if w.get("app_id") == APP_ID]


def plan(windows, alarm_ringing):
    """'launch' (set the page, start), 'close' (close the window first, then
    launch) or 'front' (bring the ringing alarm's window forward)."""
    if not clocks_windows(windows):
        return "launch"
    return "front" if alarm_ringing else "close"
