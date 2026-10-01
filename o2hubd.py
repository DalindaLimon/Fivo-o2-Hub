#!/usr/bin/env python3
"""
Fivo o2Hub background service: runs the software macro engine.

Normally started by systemd as a user service (see service.py), but can be
run by hand:

    python3 o2hubd.py            # run in the foreground, Ctrl+C to stop
    python3 o2hubd.py --hint G502

It re-reads ~/.config/fivo-o2hub/macros.json whenever the app saves it.
"""

import argparse
import signal
import sys

import macros
from macro_engine import MacroEngine


def main():
    """Parse arguments, load macros, and run the engine until SIGTERM/SIGINT, reloading macros.json on change."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--hint", default="G502", help="substring of the mouse's input-device name")
    args = ap.parse_args()

    def log(msg):
        print(msg, flush=True)

    engine = MacroEngine(log=log, name_hint=args.hint)
    state = {"mtime": None}

    def reload_if_changed():
        m = macros.mtime()
        if m != state["mtime"]:
            state["mtime"] = m
            ms = macros.load_macros()
            engine.configure(ms)
            active = [f"{x.name} → {x.trigger_key}" for x in ms if x.trigger_key and x.steps]
            log(f"Loaded {len(ms)} macro(s); active: {', '.join(active) or 'none'}")

    def on_signal(_sig, _frm):
        engine._stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    reload_if_changed()
    log("Macro engine running")
    engine.run(on_tick=reload_if_changed)
    log("Macro engine stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
