"""
Control the background macro service, a systemd *user* unit running
o2hubd.py, which plays software macros while the app window is closed.

Two separate choices, both owned by the user:

* running      — start()/stop(): for the current login session only. The app
                 starts the service automatically when a macro is assigned,
                 or when it opens and macros are already assigned.
* autostart    — set_autostart(): also start it at every login. This is
                 strictly opt-in (install.sh --autostart, or the switch on
                 the Device page); nothing enables it implicitly.
"""

import os
import subprocess
import sys

import appinfo

UNIT_NAME = appinfo.SERVICE_UNIT
UNIT_DIR = os.path.expanduser("~/.config/systemd/user")
UNIT_PATH = os.path.join(UNIT_DIR, UNIT_NAME)


def unit_text():
    """The unit file contents, pointing at this checkout's o2hubd.py."""
    return f"""[Unit]
Description={appinfo.APP_NAME} macro service (software macros for Logitech mice)

[Service]
ExecStart={sys.executable} {os.path.join(appinfo.HERE, "o2hubd.py")}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""


def _systemctl(*args):
    """Run `systemctl --user ARGS`; returns (returncode, combined output)."""
    try:
        r = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return r.returncode, (r.stdout + r.stderr).strip()


def available():
    """True if a systemd user manager is reachable."""
    return _systemctl("--version")[0] == 0


def migrate_legacy():
    """Stop, disable and delete units left by older versions of this project."""
    changed = False
    for old in appinfo.LEGACY_SERVICE_UNITS:
        path = os.path.join(UNIT_DIR, old)
        if os.path.exists(path):
            _systemctl("disable", "--now", old)
            os.remove(path)
            changed = True
    if changed:
        _systemctl("daemon-reload")


def install():
    """Write/refresh the unit file (the checkout may have moved). Doesn't enable it."""
    migrate_legacy()
    os.makedirs(UNIT_DIR, exist_ok=True)
    text = unit_text()
    try:
        with open(UNIT_PATH) as f:
            if f.read() == text:
                return
    except OSError:
        pass
    with open(UNIT_PATH, "w") as f:
        f.write(text)
    _systemctl("daemon-reload")


def is_active():
    """True if the service is running right now."""
    return _systemctl("is-active", UNIT_NAME)[1] == "active"


def is_autostart():
    """True if the user opted in to starting the service at login."""
    return _systemctl("is-enabled", UNIT_NAME)[1] == "enabled"


def start():
    """Start the service for this session (does not touch autostart)."""
    install()
    rc, out = _systemctl("start", UNIT_NAME)
    if rc != 0:
        raise RuntimeError(out or "systemctl start failed")


def stop():
    """Stop the service for this session (does not touch autostart)."""
    rc, out = _systemctl("stop", UNIT_NAME)
    if rc != 0 and os.path.exists(UNIT_PATH):
        raise RuntimeError(out or "systemctl stop failed")


def set_autostart(enabled):
    """Opt in to / out of starting the service at login."""
    install()
    rc, out = _systemctl("enable" if enabled else "disable", UNIT_NAME)
    if rc != 0:
        raise RuntimeError(out or "systemctl enable/disable failed")


def restart():
    """Restart if running (used after updates)."""
    _systemctl("try-restart", UNIT_NAME)


def recent_log(lines=15):
    """Last N lines of the service's journal, as plain text."""
    try:
        r = subprocess.run(["journalctl", "--user", "-u", UNIT_NAME, "-n", str(lines),
                            "--no-pager", "-o", "cat"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""
