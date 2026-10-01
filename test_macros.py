#!/usr/bin/env python3
"""
Automated macro-engine test. Doesn't touch your saved macros or the mouse's
memory. It:
  1. attaches the engine to the G502 (grab + mirror device),
  2. fires fake trigger presses for each repeat mode,
  3. reads the engine's virtual output device and checks the exact events.

The mouse keeps working during the test (through the mirror device).
"""
import sys
import threading
import time

import evdev
from evdev import ecodes

from macro_engine import MacroEngine, OUTPUT_NAME, MIRROR_PHYS
from macros import Macro, MacroStep, StepType, RepeatMode

A, B = "KEY_A", "KEY_B"


def steps(key, wait=30):
    """Steps for a key tap: down, wait `wait` ms, up."""
    return [MacroStep(StepType.KEY_DOWN, key=key), MacroStep(StepType.WAIT, ms=wait),
            MacroStep(StepType.KEY_UP, key=key)]


macros = [
    Macro(name="once", repeat_mode=RepeatMode.SEQUENCE, trigger_key="KEY_F13", steps=steps(A)),
    Macro(name="held", repeat_mode=RepeatMode.CONTINUOUS, trigger_key="KEY_F14", steps=steps(B, 20)),
    Macro(name="toggle", repeat_mode=RepeatMode.TOGGLE, trigger_key="KEY_F15", steps=steps(A, 20)),
]

failures = 0


def check(name, cond, detail=""):
    """Record and print one test result."""
    global failures
    print(f"[{'OK' if cond else 'FAIL'}] {name} {detail}")
    failures += 0 if cond else 1


def find(pred):
    """First input device matching `pred`, or None."""
    for p in evdev.list_devices():
        d = evdev.InputDevice(p)
        if pred(d):
            return d
        d.close()
    return None


logs = []
engine = MacroEngine(log=lambda m: (logs.append(m), print("   engine:", m)))
engine.configure(macros)
engine.start()
time.sleep(1.5)

check("attached to mouse", bool(engine.attached_devices), str(engine.attached_devices))
mirror = find(lambda d: d.phys == MIRROR_PHYS)
check("mirror device exists", mirror is not None, mirror.name if mirror else "")
out = find(lambda d: d.name == OUTPUT_NAME)
check("macro output device exists", out is not None)
if out is None:
    engine.stop()
    sys.exit(1)

events = []
stop_reader = threading.Event()


def reader():
    """Thread: collect key events from the engine's output device until stopped."""
    import select
    while not stop_reader.is_set():
        r, _, _ = select.select([out.fd], [], [], 0.1)
        if r:
            try:
                for e in out.read():
                    if e.type == ecodes.EV_KEY:
                        events.append((e.code, e.value))
            except BlockingIOError:
                pass
            except OSError:
                return  # output device closed by engine.stop()


out.grab()  # keep the test keystrokes away from your focused window
threading.Thread(target=reader, daemon=True).start()
time.sleep(0.3)


def presses(code):
    """Number of key-down events recorded for `code`."""
    return sum(1 for c, v in events if c == code and v == 1)


# sequence: one press -> exactly one A down/up
events.clear()
engine.simulate_trigger("KEY_F13", 1)
engine.simulate_trigger("KEY_F13", 0)
time.sleep(0.3)
check("sequence plays once", events == [(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], str(events))

# continuous: repeats while held, stops on release, no stuck key
events.clear()
engine.simulate_trigger("KEY_F14", 1)
time.sleep(0.5)
engine.simulate_trigger("KEY_F14", 0)
time.sleep(0.2)
n = presses(ecodes.KEY_B)
after = len(events)
time.sleep(0.3)
check("continuous repeats while held", n >= 5, f"{n} repeats")
check("continuous stops on release", len(events) == after)
check("no stuck key", events[-1] == (ecodes.KEY_B, 0) if events else False)

# toggle: on, runs, off
events.clear()
engine.simulate_trigger("KEY_F15", 1)
engine.simulate_trigger("KEY_F15", 0)
time.sleep(0.4)
running = presses(ecodes.KEY_A)
engine.simulate_trigger("KEY_F15", 1)
engine.simulate_trigger("KEY_F15", 0)
time.sleep(0.2)
after = len(events)
time.sleep(0.3)
check("toggle runs after first press", running >= 3, f"{running} repeats")
check("toggle stops after second press", len(events) == after)

# reconfigure to nothing -> engine releases the mouse
engine.configure([])
time.sleep(2.5)
check("releases mouse when no macros", not engine.attached_devices)

stop_reader.set()
engine.stop()
print("\nAll good." if not failures else f"\n{failures} check(s) failed.")
sys.exit(1 if failures else 0)
