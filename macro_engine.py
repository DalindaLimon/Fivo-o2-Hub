"""
Software macro engine — the part of Fivo o2Hub that keeps running in the
background.

How it works:
  * A button that should run a macro is bound (in the mouse's onboard
    profile) to a spare "trigger" key such as F13.
  * This engine grabs the mouse's input device(s) exclusively, re-emits
    every event unchanged through a mirror uinput device, *except* trigger
    keys, which it swallows and replaces with the macro's playback.
  * Macro output goes through a separate virtual keyboard/mouse.

It survives the mouse going to sleep, being switched off, or the receiver
being re-plugged: lost devices are dropped and re-scanned every couple of
seconds. When no macro is assigned to any trigger key, it grabs nothing,
so it has zero effect on normal input.
"""

import errno
import os
import select
import threading
import time

import evdev
from evdev import ecodes, UInput, InputDevice

import appinfo
import keys
from macros import RepeatMode, StepType

MIRROR_PHYS = appinfo.MIRROR_PHYS
OUTPUT_NAME = appinfo.OUTPUT_NAME
HOTPLUG_CHECK_INTERVAL = 1.0  # seconds between cheap /dev/input change checks
TICK_INTERVAL = 1.0


class _Source:
    """A grabbed physical input device plus the uinput mirror that re-emits its events."""
    def __init__(self, dev, mirror):
        self.dev = dev
        self.mirror = mirror

    def close(self):
        """Release the grab and close both devices, ignoring errors from vanished devices."""
        for fn in (self.dev.ungrab, self.dev.close, self.mirror.close):
            try:
                fn()
            except (OSError, AttributeError):
                pass


class MacroEngine:
    """Grabs the mouse's input device(s), forwards events, and plays macros on trigger keys.

    Threads: `run()` is the pump loop (forwarding must never block); device
    discovery runs on a short-lived scan thread; each playing macro gets its own
    thread. Output goes through one shared virtual device guarded by _out_lock.
    """
    def __init__(self, log=print, name_hint="G502"):
        self.log = log
        self.name_hint = name_hint
        self._lock = threading.RLock()
        self._out_lock = threading.Lock()
        self._macros_by_trigger = {}   # evdev code -> Macro
        self._sources = {}             # path -> _Source
        self._output = None
        self._stop = threading.Event()
        self._thread = None
        self._held = {}                # code -> bool (continuous mode)
        self._toggled = {}             # code -> bool (toggle mode)
        self._playing = set()          # codes with a live playback thread
        self._pressed_by_macro = set() # output codes currently held down
        # Device discovery opens every input node on the system (~100 ms), so it
        # only runs when something changed, and on its own thread: the event
        # pump must never stall, or the cursor visibly freezes.
        self._scan_needed = True
        self._scan_thread = None
        self._scan_result = None       # list of paths from the last finished scan
        self._input_dir_mtime = None
        self._last_hotplug_check = 0.0

    # -- configuration --------------------------------------------------------------

    def configure(self, macros):
        """Set the macros to serve (thread-safe). Triggers a rescan so grabs follow the new set."""
        mapping = {}
        for m in macros:
            if m.trigger_key and m.steps:
                code = ecodes.ecodes.get(m.trigger_key)
                if code is not None:
                    mapping[code] = m
        with self._lock:
            old = set(self._macros_by_trigger)
            self._macros_by_trigger = mapping
            for code in old - set(mapping):
                self._held[code] = False
                self._toggled[code] = False
        self._scan_needed = True  # re-evaluate grabs

    @property
    def trigger_codes(self):
        """evdev key codes currently mapped to a macro."""
        with self._lock:
            return set(self._macros_by_trigger)

    # -- device discovery -------------------------------------------------------------

    def _candidate_paths(self):
        """Input nodes of the target mouse that can emit any configured trigger key."""
        wanted = self.trigger_codes
        out = []
        for path in evdev.list_devices():
            try:
                dev = InputDevice(path)
            except OSError:
                continue
            try:
                if dev.phys == MIRROR_PHYS or dev.name == OUTPUT_NAME:
                    continue
                if self.name_hint.lower() not in dev.name.lower():
                    continue
                caps = dev.capabilities().get(ecodes.EV_KEY, [])
                if wanted & set(caps):
                    out.append(path)
            finally:
                dev.close()
        return out

    def _open_source(self, path):
        """Open, mirror (same name/IDs so desktop settings still apply) and grab an input node."""
        dev = InputDevice(path)
        try:
            info = dev.info
            mirror = UInput.from_device(
                dev, name=dev.name, vendor=info.vendor, product=info.product,
                version=info.version, bustype=info.bustype, phys=MIRROR_PHYS)
        except Exception:
            dev.close()
            raise
        try:
            dev.grab()
        except OSError:
            mirror.close()
            dev.close()
            raise
        self.log(f"Macro engine attached to {dev.name} ({path})")
        return _Source(dev, mirror)

    def _ensure_output(self):
        """Create the virtual keyboard+mouse used for macro output, once."""
        if self._output is not None:
            return
        key_codes = {ecodes.ecodes[k] for k in keys.ALL_KEYS if k in ecodes.ecodes}
        key_codes |= {ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE,
                      ecodes.BTN_SIDE, ecodes.BTN_EXTRA}
        self._output = UInput(
            events={ecodes.EV_KEY: sorted(key_codes),
                    ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y, ecodes.REL_WHEEL]},
            name=OUTPUT_NAME, phys=appinfo.OUTPUT_PHYS)

    def _check_hotplug(self):
        """Cheap: a stat() of /dev/input, whose mtime changes when nodes come or go."""
        now = time.monotonic()
        if now - self._last_hotplug_check < HOTPLUG_CHECK_INTERVAL:
            return
        self._last_hotplug_check = now
        try:
            mtime = os.stat("/dev/input").st_mtime_ns
        except OSError:
            return
        if mtime != self._input_dir_mtime:
            self._input_dir_mtime = mtime
            self._scan_needed = True

    def _maybe_start_scan(self):
        """Kick off a background device scan if one is needed and none is running."""
        if not self._scan_needed or (self._scan_thread and self._scan_thread.is_alive()):
            return
        self._scan_needed = False

        def scan():
            result = self._candidate_paths() if self.trigger_codes else []
            with self._lock:
                self._scan_result = result

        self._scan_thread = threading.Thread(target=scan, daemon=True)
        self._scan_thread.start()

    def _apply_scan_result(self):
        """On the pump thread: attach to newly wanted devices, drop ones no longer wanted."""
        with self._lock:
            wanted, self._scan_result = self._scan_result, None
        if wanted is None:
            return

        for path in list(self._sources):
            if path not in wanted:
                self._drop(path, "no longer needed")

        for path in wanted:
            if path in self._sources:
                continue
            try:
                self._ensure_output()
                self._sources[path] = self._open_source(path)
            except OSError as e:
                if e.errno == errno.EBUSY:
                    self.log(f"{path} is grabbed by another program (input-remapper, a VM, "
                             f"a second copy of this engine?) — macros can't attach.")
                elif e.errno in (errno.EACCES, errno.EPERM):
                    self.log(f"No permission for {path} or /dev/uinput — is your user in the "
                             f"'input' group? (log out/in after adding it)")
                else:
                    self.log(f"Couldn't attach to {path}: {e}")

    def _drop(self, path, reason):
        """Detach from a device; a lost device triggers a rescan so we re-attach when it returns."""
        src = self._sources.pop(path, None)
        if src:
            src.close()
            self.log(f"Macro engine detached from {path} ({reason})")
            if reason.startswith("device lost"):
                self._scan_needed = True
        if not self._sources:
            self._stop_all_playback()

    # -- main loop ------------------------------------------------------------------------

    def start(self):
        """Run the engine in a background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the engine thread and wait for it (up to 3 s)."""
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
        self._thread = None

    @property
    def running(self):
        """True while the engine thread is alive."""
        return bool(self._thread and self._thread.is_alive())

    @property
    def attached_devices(self):
        """Names of the input devices currently grabbed."""
        return [s.dev.name for s in self._sources.values()]

    def run(self, on_tick=None):
        """Blocking loop. on_tick() is called about once a second (config reloads)."""
        try:
            last_tick = 0.0
            while not self._stop.is_set():
                self._check_hotplug()
                self._maybe_start_scan()
                self._apply_scan_result()
                fds = {s.dev.fd: path for path, s in self._sources.items()}
                if not fds:
                    self._stop.wait(0.25)
                else:
                    r, _, _ = select.select(list(fds), [], [], 0.25)
                    for fd in r:
                        self._pump(fds[fd])
                if on_tick and time.monotonic() - last_tick >= TICK_INTERVAL:
                    last_tick = time.monotonic()
                    on_tick()
        finally:
            self._stop_all_playback()
            for path in list(self._sources):
                self._drop(path, "engine stopped")
            if self._output:
                self._output.close()
                self._output = None

    def _pump(self, path):
        """Read pending events from one source: trigger keys go to playback, everything else to the mirror."""
        src = self._sources.get(path)
        if not src:
            return
        try:
            for event in src.dev.read():
                if event.type == ecodes.EV_KEY and event.code in self._macros_by_trigger:
                    self._handle_trigger(event.code, event.value)
                elif event.type == ecodes.EV_MSC and event.code == ecodes.MSC_SCAN:
                    continue  # scan codes would pair with swallowed trigger keys
                else:
                    src.mirror.write_event(event)
        except BlockingIOError:
            pass
        except OSError as e:
            self._drop(path, f"device lost: {os.strerror(e.errno) if e.errno else e}")

    # -- playback ---------------------------------------------------------------------------

    def _handle_trigger(self, code, value):
        """Apply the macro's repeat mode to a trigger press (1) / release (0); autorepeat (2) is ignored."""
        with self._lock:
            macro = self._macros_by_trigger.get(code)
        if macro is None or value == 2:  # ignore autorepeat
            return
        pressed = value == 1
        mode = macro.repeat_mode

        if mode == RepeatMode.SEQUENCE:
            if pressed and code not in self._playing:
                self._spawn(code, macro, lambda: True, once=True)
        elif mode == RepeatMode.CONTINUOUS:
            self._held[code] = pressed
            if pressed and code not in self._playing:
                self._spawn(code, macro, lambda: self._held.get(code, False))
        elif mode == RepeatMode.TOGGLE:
            if pressed:
                now_on = not self._toggled.get(code, False)
                self._toggled[code] = now_on
                if now_on and code not in self._playing:
                    self._spawn(code, macro, lambda: self._toggled.get(code, False))

    def _spawn(self, code, macro, keep_going, once=False):
        """Start a playback thread for `macro`; it loops while keep_going() (or once), then releases held keys."""
        self._playing.add(code)

        def run():
            try:
                while not self._stop.is_set():
                    for step in macro.steps:
                        if self._stop.is_set() or (not once and not keep_going()):
                            break
                        self._run_step(step)
                    if once or not keep_going():
                        break
                    if not any(s.type == StepType.WAIT for s in macro.steps):
                        time.sleep(0.01)  # never spin a zero-delay loop flat out
            finally:
                self._release_all()
                self._playing.discard(code)

        threading.Thread(target=run, daemon=True).start()

    def _run_step(self, step):
        """Execute one step: emit a key event, or sleep in small slices so stops are prompt."""
        if step.type == StepType.WAIT:
            # Sleep in small slices so releasing a held button stops promptly.
            end = time.monotonic() + max(step.ms, 0) / 1000.0
            while not self._stop.is_set():
                left = end - time.monotonic()
                if left <= 0:
                    break
                time.sleep(min(left, 0.02))
            return
        code = ecodes.ecodes.get(step.key)
        if code is None:
            return
        value = 1 if step.type == StepType.KEY_DOWN else 0
        with self._out_lock:
            if self._output is None:
                return
            self._output.write(ecodes.EV_KEY, code, value)
            self._output.syn()
            if value:
                self._pressed_by_macro.add(code)
            else:
                self._pressed_by_macro.discard(code)

    def _release_all(self):
        """Never leave a key stuck down when a macro is interrupted."""
        with self._out_lock:
            if self._output is None:
                self._pressed_by_macro.clear()
                return
            for code in list(self._pressed_by_macro):
                self._output.write(ecodes.EV_KEY, code, 0)
            if self._pressed_by_macro:
                self._output.syn()
            self._pressed_by_macro.clear()

    def _stop_all_playback(self):
        """Signal every running macro to stop and release any keys they hold."""
        for code in list(self._held):
            self._held[code] = False
        for code in list(self._toggled):
            self._toggled[code] = False
        self._release_all()

    # -- testing hook -------------------------------------------------------------------------

    def simulate_trigger(self, trigger_key, value):
        """Feed a fake trigger press/release (used by test_macros.py)."""
        self._ensure_output()
        self._handle_trigger(ecodes.ecodes[trigger_key], value)
