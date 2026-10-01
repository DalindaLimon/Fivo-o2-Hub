#!/usr/bin/env python3
"""Check everything Fivo o2Hub needs, and talk to the mouse. Read-only."""
import grp
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import appinfo  # noqa: E402

ok = True


def report(passed, msg, fix=""):
    """Print an [OK]/[FAIL] line (with a fix hint on failure) and track overall success."""
    global ok
    print(f"[{'OK' if passed else 'FAIL'}] {msg}" + (f"\n       fix: {fix}" if not passed and fix else ""))
    ok &= passed


print(f"=== {appinfo.APP_NAME} diagnostics ===\n")

print("--- Dependencies ---")
try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    report(True, f"GTK4 + libadwaita {Adw.get_major_version()}.{Adw.get_minor_version()}")
except Exception as e:  # noqa: BLE001
    report(False, f"GTK4/libadwaita: {e}", "sudo pacman -S python-gobject gtk4 libadwaita")
import importlib.util  # noqa: E402

if importlib.util.find_spec("evdev"):
    report(True, "python-evdev")
else:
    report(False, "python-evdev missing (macros)", "sudo pacman -S python-evdev")

print("\n--- Mouse (HID++) ---")
import hidpp  # noqa: E402

found = hidpp.find_devices()
if not found:
    bad = hidpp.unreadable_logitech_nodes()
    report(False, "no HID++ device reachable" + (f" — no permission for {', '.join(bad)}" if bad else ""),
           "./install.sh, then replug the receiver" if bad else "plug in / wake up the mouse")
for f in found:
    dev = hidpp.HidppDevice(f["path"])
    try:
        name = dev.get_name()
        bat = dev.get_battery()
        feats = [f"0x{fid:04X}" for _i, fid, _t, _v in dev.list_features()]
        report(True, f"{name} at {f['path']} ({'wireless' if f['wireless'] else 'USB'})")
        print(f"       battery: {bat}")
        print(f"       onboard profiles: {'yes' if '0x8100' in feats else 'NO'}; "
              f"DPI: {'yes' if '0x2201' in feats else 'no'}; LEDs: {'yes' if '0x8070' in feats else 'no'}")
        if "0x8100" in feats:
            import onboard
            ob = onboard.OnboardProfiles(dev)
            print(f"       active profile: {ob.active_index()}; enabled: "
                  f"{[p.index for p in ob.profiles if p.enabled]}")
    except hidpp.HidppError as e:
        report(False, f"{f['path']}: {e}")
    finally:
        dev.close()

print("\n--- Macro service ---")
report("input" in [grp.getgrgid(g).gr_name for g in os.getgroups()],
       "user in 'input' group", "sudo usermod -aG input $USER, then log out/in")
report(os.access("/dev/uinput", os.W_OK), "/dev/uinput writable", "./install.sh (udev rule)")
import service  # noqa: E402

if service.available():
    print(f"       service: {'running' if service.is_active() else 'stopped'}, "
          f"{'starts at login' if service.is_autostart() else "doesn't start at login"}")

print("\n" + ("Everything looks fine." if ok else "Fix the [FAIL] items above."))
