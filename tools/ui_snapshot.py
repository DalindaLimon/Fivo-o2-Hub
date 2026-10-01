#!/usr/bin/env python3
"""
Render every page of the running app to PNG files — for README screenshots
and for eyeballing UI changes. Needs a connected mouse.

Run it against a headless compositor so nothing appears on your desktop:

    kwin_wayland --virtual --width 1300 --height 860 --socket o2snap &   # or: weston --backend=headless
    WAYLAND_DISPLAY=o2snap GDK_BACKEND=wayland python3 tools/ui_snapshot.py docs/screenshots

(Running it on your normal session also works; a window flashes up briefly.)
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Gio, GLib, Graphene, Gtk  # noqa: E402

import app  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "screenshots")
SIZE = (1240, 820)
PAGES = [(0, "sensitivity"), (1, "assignments"), (2, "lighting"), (3, "macros"),
         (4, "profiles"), (5, "device")]
LOAD_WAIT_MS = 4000   # device discovery + reading 6 flash sectors
SETTLE_MS = 1500      # let a page lay out and render before capturing


def snapshot(win, path):
    """Render the window's last frame to a PNG. Returns False if there's no frame yet."""
    w, h = win.get_width(), win.get_height()
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snap, w, h)
    node = snap.to_node()
    if node is None:
        return False
    tex = win.get_native().get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h))
    tex.save_to_png(path)
    return True


class SnapshotApp(app.O2HubApp):
    """The real app, plus a timer script that walks the pages and captures each."""

    def __init__(self):
        super().__init__()
        # Run as a separate instance even if the user has the app open.
        self.set_flags(Gio.ApplicationFlags.NON_UNIQUE)

    def do_activate(self):
        """Open the window, then schedule page switches and captures."""
        os.makedirs(OUT, exist_ok=True)
        win = app.MainWindow(self)
        win.set_default_size(*SIZE)
        win.present()
        t = LOAD_WAIT_MS
        for index, name in PAGES:
            GLib.timeout_add(t, lambda i=index: (win.select_page(i), False)[1])
            t += SETTLE_MS
            GLib.timeout_add(t, lambda n=name: (self._capture(win, n), False)[1])
            t += 200
        GLib.timeout_add(t + 300, lambda: (self.quit(), False)[1])

    def _capture(self, win, name):
        """Save one page, reporting progress."""
        path = os.path.join(OUT, f"{name}.png")
        print(("wrote " if snapshot(win, path) else "no frame for ") + path)


if __name__ == "__main__":
    SnapshotApp().run([])
