"""
Macro data model + JSON persistence.

A macro is an ordered list of steps (key down / key up / wait-ms) plus a
repeat mode. The repeat mode is implemented in software by macro_engine.py.
"""
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from enum import Enum

import appinfo
import keys


CONFIG_DIR = appinfo.CONFIG_DIR
MACROS_FILE = os.path.join(CONFIG_DIR, "macros.json")


def migrate_legacy_config():
    """Copy macros.json from an older install's config dir, once."""
    if os.path.exists(MACROS_FILE):
        return
    for old_dir in appinfo.LEGACY_CONFIG_DIRS:
        old = os.path.join(old_dir, "macros.json")
        if os.path.exists(old):
            os.makedirs(CONFIG_DIR, exist_ok=True)
            shutil.copy2(old, MACROS_FILE)
            return


class RepeatMode(str, Enum):
    """How a macro behaves while its button is pressed: play once, repeat while held, or toggle."""
    SEQUENCE = "sequence"        # play once per press
    CONTINUOUS = "continuous"    # repeat while held down
    TOGGLE = "toggle"            # press to start, press again to stop


REPEAT_MODE_LABELS = {
    RepeatMode.SEQUENCE: "Play Once (Sequence)",
    RepeatMode.CONTINUOUS: "Repeat While Held (Continuous)",
    RepeatMode.TOGGLE: "Toggle On / Off",
}


class StepType(str, Enum):
    """Kind of macro step: key down, key up, or wait."""
    KEY_DOWN = "key_down"
    KEY_UP = "key_up"
    WAIT = "wait"


@dataclass
class MacroStep:
    """One macro step. `key` is an evdev name (keys/buttons); `ms` is used by WAIT steps."""
    type: StepType
    key: str = ""   # evdev key name, e.g. "KEY_A"
    ms: int = 0     # delay in milliseconds

    def label(self):
        """Human-readable description, e.g. 'Press A' or 'Wait 50 ms'."""
        if self.type == StepType.WAIT:
            return f"Wait {self.ms} ms"
        pretty = self.key.replace("KEY_", "") if self.key else "?"
        return f"{'Press' if self.type == StepType.KEY_DOWN else 'Release'} {pretty}"

    def to_dict(self):
        """JSON-ready dict."""
        return {"type": self.type.value, "key": self.key, "ms": self.ms}

    @staticmethod
    def from_dict(d):
        """Inverse of to_dict."""
        return MacroStep(type=StepType(d["type"]), key=d.get("key", ""), ms=d.get("ms", 0))


@dataclass
class Macro:
    """A named macro: ordered steps + repeat mode.

    `trigger_key` is the spare key (see keys.TRIGGER_KEYS) a mouse button is
    bound to in order to run this macro; '' while the macro isn't assigned.
    """
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = "New Macro"
    repeat_mode: RepeatMode = RepeatMode.SEQUENCE
    steps: list = field(default_factory=list)
    trigger_key: str = ""  # evdev key name the button gets remapped to

    def to_dict(self):
        """JSON-ready dict (the macros.json format)."""
        return {
            "id": self.id,
            "name": self.name,
            "repeat_mode": self.repeat_mode.value,
            "steps": [s.to_dict() for s in self.steps],
            "trigger_key": self.trigger_key,
        }

    @staticmethod
    def from_dict(d):
        """Inverse of to_dict; tolerates missing fields."""
        return Macro(
            id=d.get("id", uuid.uuid4().hex[:8]),
            name=d.get("name", "Macro"),
            repeat_mode=RepeatMode(d.get("repeat_mode", "sequence")),
            steps=[MacroStep.from_dict(s) for s in d.get("steps", [])],
            trigger_key=d.get("trigger_key", ""),
        )


class MacroStore:
    """All macros, persisted to ~/.config/fivo-o2hub/macros.json. Every mutation saves immediately."""
    def __init__(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        migrate_legacy_config()
        self.macros = []
        self.load()

    def load(self):
        """(Re)read macros.json; an unreadable file yields an empty list."""
        if not os.path.exists(MACROS_FILE):
            self.macros = []
            return
        try:
            with open(MACROS_FILE) as f:
                data = json.load(f)
            self.macros = [Macro.from_dict(m) for m in data]
        except (OSError, ValueError, KeyError):
            self.macros = []

    def save(self):
        # Atomic replace: the background macro engine re-reads this file
        # whenever it changes and must never see a half-written version.
        """Write macros.json atomically (temp file + rename) so the service never reads a partial file."""
        fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".macros-", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump([m.to_dict() for m in self.macros], f, indent=2)
        os.replace(tmp, MACROS_FILE)

    def add(self, macro: Macro):
        """Append a macro and save."""
        self.macros.append(macro)
        self.save()

    def update(self, macro: Macro):
        """Replace the macro with the same id (or add it) and save."""
        for i, m in enumerate(self.macros):
            if m.id == macro.id:
                self.macros[i] = macro
                self.save()
                return
        self.add(macro)

    def remove(self, macro_id: str):
        """Delete a macro by id and save."""
        self.macros = [m for m in self.macros if m.id != macro_id]
        self.save()

    def get(self, macro_id: str):
        """Macro by id, or None."""
        for m in self.macros:
            if m.id == macro_id:
                return m
        return None

    def used_trigger_keys(self):
        """Set of trigger keys currently taken."""
        return {m.trigger_key for m in self.macros if m.trigger_key}

    def next_free_trigger_key(self):
        """Pick a spare trigger key (F13-F24, then Japanese-layout keys)."""
        used = self.used_trigger_keys()
        for candidate in keys.TRIGGER_KEYS:
            if candidate not in used:
                return candidate
        return None

    def ensure_trigger(self, macro):
        """Give `macro` a trigger key if it has none; returns the key name."""
        if not macro.trigger_key:
            free = self.next_free_trigger_key()
            if free is None:
                raise RuntimeError(
                    f"All {len(keys.TRIGGER_KEYS)} spare trigger keys are in use. "
                    "Delete or unassign a macro first.")
            macro.trigger_key = free
            self.update(macro)
        return macro.trigger_key

    def by_hid_usage(self, usage):
        """Macro whose trigger key has this HID keyboard usage, if any."""
        name = keys.HID_TO_EVDEV.get(usage)
        if name not in keys.TRIGGER_KEYS:
            return None
        for m in self.macros:
            if m.trigger_key == name:
                return m
        return None

    def release_unbound(self, bound_usages):
        """Free trigger keys of macros no button uses any more."""
        changed = False
        for m in self.macros:
            if m.trigger_key and keys.EVDEV_TO_HID.get(m.trigger_key) not in bound_usages:
                m.trigger_key = ""
                changed = True
        if changed:
            self.save()
        return changed


def mtime():
    """Modification time (ns) of macros.json, 0 if missing — used by the service to notice changes."""
    try:
        return os.stat(MACROS_FILE).st_mtime_ns
    except OSError:
        return 0


def load_macros():
    """Read macros.json (used by the daemon). Only side effect: a one-time
    copy from a legacy config dir."""
    migrate_legacy_config()
    try:
        with open(MACROS_FILE) as f:
            return [Macro.from_dict(m) for m in json.load(f)]
    except (OSError, ValueError, KeyError):
        return []
