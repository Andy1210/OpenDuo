# Calculator has its own keypad: without this its focused display field
# brought up the on-screen keyboard over the keypad. GTK's simple input
# method speaks no text-input-v3, so squeekboard stays down; a plugged
# keyboard still types. install.sh writes the override with this.
s|^Exec=gnome-calculator|Exec=env GTK_IM_MODULE=gtk-im-context-simple gnome-calculator|
