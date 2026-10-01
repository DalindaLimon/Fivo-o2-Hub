#!/usr/bin/env python3
"""
Diagnostic helper for the 'Device or resource busy' error when starting
the Macro Engine.

That error means some OTHER process already holds an exclusive grab
(EVIOCGRAB) on the mouse's input device -- only one process can hold
that at a time, and it isn't the same thing as normal read permissions.

This script:
  1. Lists every input device that looks like a mouse, so you can see
     exactly which /dev/input/eventX the app is trying to grab.
  2. Prints which processes currently have that device node open, so
     you can spot the conflicting one (a second copy of this app, a
     VM/looking-glass passthrough tool, Barrier/Synergy, an existing
     remapper like input-remapper, etc).

Usage:
    python3 list_input_devices.py
"""
import subprocess
import sys

import evdev
from evdev import ecodes, InputDevice


def main():
    print("Scanning /dev/input/event* for mouse-like devices...\n")
    found_any = False
    for path in evdev.list_devices():
        try:
            dev = InputDevice(path)
        except OSError as e:
            print(f"{path}: could not open ({e})")
            continue

        caps = dev.capabilities()
        is_mouse = ecodes.EV_KEY in caps and ecodes.BTN_LEFT in caps[ecodes.EV_KEY]
        if not is_mouse:
            continue

        found_any = True
        print(f"{path}")
        print(f"  name: {dev.name}")
        print(f"  phys: {dev.phys}")

        try:
            out = subprocess.run(["fuser", "-v", path], capture_output=True, text=True, timeout=5)
            details = (out.stdout + out.stderr).strip()
            if details:
                print("  currently open by:")
                for line in details.splitlines():
                    print(f"    {line}")
            else:
                print("  currently open by: (nothing -- fuser saw no holders, or needs sudo)")
        except FileNotFoundError:
            print("  ('fuser' not found -- install psmisc, or use: sudo lsof " + path + ")")
        except subprocess.TimeoutExpired:
            print("  (fuser timed out)")

        print()

    if not found_any:
        print("No mouse-like devices found. Is the mouse plugged in?")
        sys.exit(1)

    print("If a device you expect to use shows a holder other than your desktop's")
    print("input stack (Xorg/Wayland compositor) or nothing at all, that other")
    print("process is what's grabbing it -- close it, then try the Macro Engine")
    print("switch again. Re-running this script with sudo may reveal more detail:")
    print("    sudo python3 list_input_devices.py")


if __name__ == "__main__":
    main()
