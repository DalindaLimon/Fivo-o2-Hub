#!/usr/bin/env python3
"""
"Notched scrolling": stop tiny scroll-wheel wobbles from scrolling the page.

Why: on Linux the kernel puts Logitech wheels in high-resolution mode (the
G502 reports every 1/8 of a notch). A fast flick of the mouse rocks the
wheel slightly inside its detent, and those 1/8-notch events scroll the
page. Windows keeps the wheel in standard mode, where only whole notches
count.

Fix: a libinput quirk that hides the high-resolution wheel events from
libinput (which every Wayland compositor and the X11 libinput driver use).
libinput then scrolls from the kernel's per-notch events, which only fire
after half a notch of movement in one direction, with the count reset on
every direction change, so wobble never scrolls. Apps still receive normal
scroll events; only sub-notch smooth scrolling is lost (as on Windows).

The quirk is a marked block in /etc/libinput/local-overrides.quirks (the
file libinput reserves for local admin overrides; other content in it is
preserved). libinput reads quirks when the compositor starts, so changes
apply after logging out and back in.

As a script (needs root; the app runs it through pkexec):

    sudo python3 scroll_quirk.py --enable
    sudo python3 scroll_quirk.py --disable
    python3 scroll_quirk.py --status
"""

import os
import subprocess
import sys
import tempfile

import appinfo

OVERRIDES = "/etc/libinput/local-overrides.quirks"
BEGIN = f"# >>> {appinfo.APP_SLUG} notched scrolling >>>"
END = f"# <<< {appinfo.APP_SLUG} notched scrolling <<<"

# Name globs of mice the quirk applies to (input-device names, any connection).
MODELS = ["*G502*"]


def quirk_block():
    """The text inserted into the overrides file, including its markers."""
    sections = []
    for i, glob in enumerate(MODELS):
        sections.append(
            f"[{appinfo.APP_NAME} notched scrolling {i + 1}]\n"
            "MatchUdevType=mouse\n"
            "MatchVendor=0x046D\n"
            f"MatchName={glob}\n"
            "AttrEventCode=-REL_WHEEL_HI_RES;-REL_HWHEEL_HI_RES;\n")
    return (f"{BEGIN}\n# Managed by {appinfo.APP_NAME}; toggle it in the app "
            f"(Sensitivity > Scroll wheel) rather than editing by hand.\n"
            + "\n".join(sections) + f"{END}\n")


def strip_block(text):
    """`text` with our marked block (if any) removed; other content untouched."""
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        if line.strip() == BEGIN:
            skipping = True
            if out and not out[-1].strip():
                out.pop()  # the blank separator apply_block put before the block
            continue
        if skipping:
            if line.strip() == END:
                skipping = False
            continue
        out.append(line)
    return "".join(out)


def apply_block(text, enabled):
    """Return the overrides-file contents with the block added or removed."""
    base = strip_block(text)
    if not enabled:
        return base
    if base and not base.endswith("\n"):
        base += "\n"
    return base + ("\n" if base.strip() else "") + quirk_block()


def is_enabled():
    """True if the quirk block is currently installed."""
    try:
        with open(OVERRIDES, encoding="utf-8") as f:
            return BEGIN in f.read()
    except OSError:
        return False


def write(enabled):
    """Install or remove the block (must run as root). Atomic replace."""
    try:
        with open(OVERRIDES, encoding="utf-8") as f:
            current = f.read()
    except FileNotFoundError:
        current = ""
    new = apply_block(current, enabled)
    if new == current:
        return
    if not new.strip():
        if os.path.exists(OVERRIDES):
            os.remove(OVERRIDES)  # only our block was in it
        return
    os.makedirs(os.path.dirname(OVERRIDES), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OVERRIDES), prefix=".local-overrides.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(new)
    os.chmod(tmp, 0o644)
    os.replace(tmp, OVERRIDES)


def set_enabled(enabled):
    """From the (unprivileged) app: run this script as root via pkexec.
    Raises RuntimeError with a readable message on failure or cancel."""
    cmd = ["pkexec", sys.executable, os.path.abspath(__file__),
           "--enable" if enabled else "--disable"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except FileNotFoundError as e:
        raise RuntimeError("pkexec isn't installed (package: polkit). Run instead:\n"
                           f"  sudo python3 {os.path.abspath(__file__)} "
                           f"{'--enable' if enabled else '--disable'}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("Timed out waiting for authentication.") from e
    if r.returncode in (126, 127):
        raise RuntimeError("Authentication was cancelled or refused.")
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or f"helper failed ({r.returncode})")


def main(argv):
    """CLI: --enable / --disable (root) or --status."""
    if "--status" in argv:
        print("enabled" if is_enabled() else "disabled")
        return 0
    if "--enable" in argv or "--disable" in argv:
        if os.geteuid() != 0:
            print("This needs root: sudo python3 scroll_quirk.py --enable|--disable", file=sys.stderr)
            return 1
        write("--enable" in argv)
        print("Notched scrolling", "enabled" if "--enable" in argv else "disabled",
              "— log out and back in to apply.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
