"""
Key tables.

Three naming systems meet here:
  * evdev names ("KEY_A", "BTN_LEFT") — what the Linux input layer and the
    macro engine speak, also what macros.json stores;
  * USB HID keyboard usages (0x04 = A) — what onboard button bindings store;
  * GTK hardware keycodes — evdev code + 8 on Linux, used when recording.
"""

# evdev name -> Linux keycode (input-event-codes.h)
LINUX_KEYCODES = {
    "KEY_ESC": 1,
    "KEY_1": 2, "KEY_2": 3, "KEY_3": 4, "KEY_4": 5, "KEY_5": 6,
    "KEY_6": 7, "KEY_7": 8, "KEY_8": 9, "KEY_9": 10, "KEY_0": 11,
    "KEY_MINUS": 12, "KEY_EQUAL": 13, "KEY_BACKSPACE": 14, "KEY_TAB": 15,
    "KEY_Q": 16, "KEY_W": 17, "KEY_E": 18, "KEY_R": 19, "KEY_T": 20,
    "KEY_Y": 21, "KEY_U": 22, "KEY_I": 23, "KEY_O": 24, "KEY_P": 25,
    "KEY_LEFTBRACE": 26, "KEY_RIGHTBRACE": 27, "KEY_ENTER": 28, "KEY_LEFTCTRL": 29,
    "KEY_A": 30, "KEY_S": 31, "KEY_D": 32, "KEY_F": 33, "KEY_G": 34,
    "KEY_H": 35, "KEY_J": 36, "KEY_K": 37, "KEY_L": 38,
    "KEY_SEMICOLON": 39, "KEY_APOSTROPHE": 40, "KEY_GRAVE": 41,
    "KEY_LEFTSHIFT": 42, "KEY_BACKSLASH": 43,
    "KEY_Z": 44, "KEY_X": 45, "KEY_C": 46, "KEY_V": 47, "KEY_B": 48,
    "KEY_N": 49, "KEY_M": 50,
    "KEY_COMMA": 51, "KEY_DOT": 52, "KEY_SLASH": 53, "KEY_RIGHTSHIFT": 54,
    "KEY_KPASTERISK": 55,
    "KEY_LEFTALT": 56, "KEY_SPACE": 57, "KEY_CAPSLOCK": 58,
    "KEY_F1": 59, "KEY_F2": 60, "KEY_F3": 61, "KEY_F4": 62, "KEY_F5": 63,
    "KEY_F6": 64, "KEY_F7": 65, "KEY_F8": 66, "KEY_F9": 67, "KEY_F10": 68,
    "KEY_NUMLOCK": 69, "KEY_SCROLLLOCK": 70,
    "KEY_KP7": 71, "KEY_KP8": 72, "KEY_KP9": 73, "KEY_KPMINUS": 74,
    "KEY_KP4": 75, "KEY_KP5": 76, "KEY_KP6": 77, "KEY_KPPLUS": 78,
    "KEY_KP1": 79, "KEY_KP2": 80, "KEY_KP3": 81, "KEY_KP0": 82, "KEY_KPDOT": 83,
    "KEY_102ND": 86,
    "KEY_F11": 87, "KEY_F12": 88,
    "KEY_RO": 89, "KEY_HENKAN": 92, "KEY_KATAKANAHIRAGANA": 93, "KEY_MUHENKAN": 94,
    "KEY_KPENTER": 96, "KEY_RIGHTCTRL": 97, "KEY_KPSLASH": 98, "KEY_SYSRQ": 99,
    "KEY_RIGHTALT": 100,
    "KEY_HOME": 102, "KEY_UP": 103, "KEY_PAGEUP": 104, "KEY_LEFT": 105,
    "KEY_RIGHT": 106, "KEY_END": 107, "KEY_DOWN": 108, "KEY_PAGEDOWN": 109,
    "KEY_INSERT": 110, "KEY_DELETE": 111,
    "KEY_MUTE": 113, "KEY_VOLUMEDOWN": 114, "KEY_VOLUMEUP": 115,
    "KEY_PAUSE": 119, "KEY_YEN": 124,
    "KEY_LEFTMETA": 125, "KEY_RIGHTMETA": 126, "KEY_COMPOSE": 127,
    "KEY_NEXTSONG": 163, "KEY_PLAYPAUSE": 164, "KEY_PREVIOUSSONG": 165, "KEY_STOPCD": 166,
    "KEY_F13": 183, "KEY_F14": 184, "KEY_F15": 185, "KEY_F16": 186,
    "KEY_F17": 187, "KEY_F18": 188, "KEY_F19": 189, "KEY_F20": 190,
    "KEY_F21": 191, "KEY_F22": 192, "KEY_F23": 193, "KEY_F24": 194,
    "BTN_LEFT": 272, "BTN_RIGHT": 273, "BTN_MIDDLE": 274,
    "BTN_SIDE": 275, "BTN_EXTRA": 276, "BTN_FORWARD": 277,
    "BTN_BACK": 278, "BTN_TASK": 279,
}
KEYCODE_NAMES = {v: k for k, v in LINUX_KEYCODES.items()}

# evdev name -> USB HID keyboard usage (HID Usage Tables, page 0x07)
EVDEV_TO_HID = {}
for _i, _c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    EVDEV_TO_HID[f"KEY_{_c}"] = 0x04 + _i
for _i, _d in enumerate("1234567890"):
    EVDEV_TO_HID[f"KEY_{_d}"] = 0x1E + _i
for _n in range(1, 13):
    EVDEV_TO_HID[f"KEY_F{_n}"] = 0x3A + _n - 1
for _n in range(13, 25):
    EVDEV_TO_HID[f"KEY_F{_n}"] = 0x68 + _n - 13
for _n in range(1, 10):
    EVDEV_TO_HID[f"KEY_KP{_n}"] = 0x59 + _n - 1
EVDEV_TO_HID.update({
    "KEY_ENTER": 0x28, "KEY_ESC": 0x29, "KEY_BACKSPACE": 0x2A, "KEY_TAB": 0x2B,
    "KEY_SPACE": 0x2C, "KEY_MINUS": 0x2D, "KEY_EQUAL": 0x2E, "KEY_LEFTBRACE": 0x2F,
    "KEY_RIGHTBRACE": 0x30, "KEY_BACKSLASH": 0x31, "KEY_SEMICOLON": 0x33,
    "KEY_APOSTROPHE": 0x34, "KEY_GRAVE": 0x35, "KEY_COMMA": 0x36, "KEY_DOT": 0x37,
    "KEY_SLASH": 0x38, "KEY_CAPSLOCK": 0x39, "KEY_SYSRQ": 0x46, "KEY_SCROLLLOCK": 0x47,
    "KEY_PAUSE": 0x48, "KEY_INSERT": 0x49, "KEY_HOME": 0x4A, "KEY_PAGEUP": 0x4B,
    "KEY_DELETE": 0x4C, "KEY_END": 0x4D, "KEY_PAGEDOWN": 0x4E, "KEY_RIGHT": 0x4F,
    "KEY_LEFT": 0x50, "KEY_DOWN": 0x51, "KEY_UP": 0x52, "KEY_NUMLOCK": 0x53,
    "KEY_KPSLASH": 0x54, "KEY_KPASTERISK": 0x55, "KEY_KPMINUS": 0x56, "KEY_KPPLUS": 0x57,
    "KEY_KPENTER": 0x58, "KEY_KP0": 0x62, "KEY_KPDOT": 0x63, "KEY_102ND": 0x64,
    "KEY_COMPOSE": 0x65,
    "KEY_RO": 0x87, "KEY_KATAKANAHIRAGANA": 0x88, "KEY_YEN": 0x89,
    "KEY_HENKAN": 0x8A, "KEY_MUHENKAN": 0x8B,
    "KEY_MUTE": 0x7F, "KEY_VOLUMEUP": 0x80, "KEY_VOLUMEDOWN": 0x81,
    "KEY_LEFTCTRL": 0xE0, "KEY_LEFTSHIFT": 0xE1, "KEY_LEFTALT": 0xE2, "KEY_LEFTMETA": 0xE3,
    "KEY_RIGHTCTRL": 0xE4, "KEY_RIGHTSHIFT": 0xE5, "KEY_RIGHTALT": 0xE6, "KEY_RIGHTMETA": 0xE7,
})
HID_TO_EVDEV = {v: k for k, v in EVDEV_TO_HID.items()}

# HID modifier byte bits (same order as a boot-protocol keyboard report)
MOD_LCTRL, MOD_LSHIFT, MOD_LALT, MOD_LMETA = 0x01, 0x02, 0x04, 0x08
MOD_RCTRL, MOD_RSHIFT, MOD_RALT, MOD_RMETA = 0x10, 0x20, 0x40, 0x80
MODIFIER_LABELS = [(MOD_LCTRL | MOD_RCTRL, "Ctrl"), (MOD_LSHIFT | MOD_RSHIFT, "Shift"),
                   (MOD_LALT | MOD_RALT, "Alt"), (MOD_LMETA | MOD_RMETA, "Super")]

# Keys nothing on a normal desktop uses. A button bound to one of these is a
# "software macro" button: the macro engine swallows the key and plays the
# macro instead. F13-F24 first, then the Japanese-layout keys as overflow.
TRIGGER_KEYS = [f"KEY_F{n}" for n in range(13, 25)] + [
    "KEY_RO", "KEY_KATAKANAHIRAGANA", "KEY_YEN", "KEY_HENKAN", "KEY_MUHENKAN"]

LETTERS = [f"KEY_{c}" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
DIGITS = [f"KEY_{d}" for d in "1234567890"]
FUNCTION = [f"KEY_F{n}" for n in range(1, 25)]
MODIFIERS = ["KEY_LEFTSHIFT", "KEY_RIGHTSHIFT", "KEY_LEFTCTRL", "KEY_RIGHTCTRL",
             "KEY_LEFTALT", "KEY_RIGHTALT", "KEY_LEFTMETA", "KEY_RIGHTMETA"]
NAVIGATION = ["KEY_UP", "KEY_DOWN", "KEY_LEFT", "KEY_RIGHT", "KEY_HOME", "KEY_END",
              "KEY_PAGEUP", "KEY_PAGEDOWN", "KEY_INSERT", "KEY_DELETE"]
WHITESPACE = ["KEY_SPACE", "KEY_TAB", "KEY_ENTER", "KEY_BACKSPACE", "KEY_ESC"]
PUNCT = ["KEY_MINUS", "KEY_EQUAL", "KEY_LEFTBRACE", "KEY_RIGHTBRACE",
         "KEY_SEMICOLON", "KEY_APOSTROPHE", "KEY_GRAVE", "KEY_BACKSLASH",
         "KEY_COMMA", "KEY_DOT", "KEY_SLASH", "KEY_102ND"]
LOCKS_AND_SYSTEM = ["KEY_CAPSLOCK", "KEY_NUMLOCK", "KEY_SCROLLLOCK", "KEY_SYSRQ", "KEY_PAUSE",
                    "KEY_COMPOSE"]
NUMPAD = ["KEY_KP0", "KEY_KP1", "KEY_KP2", "KEY_KP3", "KEY_KP4", "KEY_KP5",
          "KEY_KP6", "KEY_KP7", "KEY_KP8", "KEY_KP9", "KEY_KPDOT", "KEY_KPENTER",
          "KEY_KPPLUS", "KEY_KPMINUS", "KEY_KPASTERISK", "KEY_KPSLASH"]
MEDIA = ["KEY_MUTE", "KEY_VOLUMEDOWN", "KEY_VOLUMEUP", "KEY_PLAYPAUSE",
         "KEY_NEXTSONG", "KEY_PREVIOUSSONG", "KEY_STOPCD"]
MOUSE_BUTTONS = ["BTN_LEFT", "BTN_RIGHT", "BTN_MIDDLE", "BTN_SIDE", "BTN_EXTRA"]

MOUSE_BUTTON_LABELS = {
    "BTN_LEFT": "Mouse Left Click",
    "BTN_RIGHT": "Mouse Right Click",
    "BTN_MIDDLE": "Mouse Middle Click",
    "BTN_SIDE": "Mouse Back Button",
    "BTN_EXTRA": "Mouse Forward Button",
    "BTN_FORWARD": "Mouse Forward (alt)",
    "BTN_BACK": "Mouse Back (alt)",
    "BTN_TASK": "Mouse Task Button",
}

ALL_KEYS = (LETTERS + DIGITS + FUNCTION + MODIFIERS + NAVIGATION + WHITESPACE
            + PUNCT + LOCKS_AND_SYSTEM + NUMPAD + MEDIA + MOUSE_BUTTONS)

# Keys that can be stored directly in an onboard keyboard binding.
BINDABLE_KEYS = [k for k in ALL_KEYS if k in EVDEV_TO_HID and k not in MODIFIERS] + MODIFIERS

_PRETTY = {
    "KEY_LEFTBRACE": "[", "KEY_RIGHTBRACE": "]", "KEY_SEMICOLON": ";", "KEY_APOSTROPHE": "'",
    "KEY_GRAVE": "`", "KEY_BACKSLASH": "\\", "KEY_COMMA": ",", "KEY_DOT": ".", "KEY_SLASH": "/",
    "KEY_MINUS": "-", "KEY_EQUAL": "=", "KEY_102ND": "< > (ISO)", "KEY_SYSRQ": "Print Screen",
    "KEY_LEFTMETA": "Left Super", "KEY_RIGHTMETA": "Right Super", "KEY_LEFTCTRL": "Left Ctrl",
    "KEY_RIGHTCTRL": "Right Ctrl", "KEY_LEFTSHIFT": "Left Shift", "KEY_RIGHTSHIFT": "Right Shift",
    "KEY_LEFTALT": "Left Alt", "KEY_RIGHTALT": "Right Alt", "KEY_PAGEUP": "Page Up",
    "KEY_PAGEDOWN": "Page Down", "KEY_CAPSLOCK": "Caps Lock", "KEY_NUMLOCK": "Num Lock",
    "KEY_SCROLLLOCK": "Scroll Lock", "KEY_BACKSPACE": "Backspace", "KEY_COMPOSE": "Menu",
    "KEY_KPASTERISK": "Numpad *", "KEY_KPSLASH": "Numpad /", "KEY_KPMINUS": "Numpad -",
    "KEY_KPPLUS": "Numpad +", "KEY_KPENTER": "Numpad Enter", "KEY_KPDOT": "Numpad .",
    "KEY_VOLUMEUP": "Volume Up", "KEY_VOLUMEDOWN": "Volume Down", "KEY_PLAYPAUSE": "Play/Pause",
    "KEY_NEXTSONG": "Next Track", "KEY_PREVIOUSSONG": "Previous Track", "KEY_STOPCD": "Stop",
}


def pretty(key_name: str) -> str:
    """Short human label for an evdev key name: 'KEY_LEFTBRACE' -> '[', 'BTN_LEFT' -> 'Mouse Left Click'."""
    if key_name in MOUSE_BUTTON_LABELS:
        return MOUSE_BUTTON_LABELS[key_name]
    if key_name in _PRETTY:
        return _PRETTY[key_name]
    name = key_name.replace("KEY_", "")
    if name.startswith("KP") and name[2:].isdigit():
        return f"Numpad {name[2:]}"
    return name.title() if len(name) > 3 else name


def describe_combo(modifiers: int, hid_usage: int) -> str:
    """Label for a HID modifier mask + key usage, e.g. 'Ctrl + Shift + C'."""
    parts = [label for mask, label in MODIFIER_LABELS if modifiers & mask]
    evdev_name = HID_TO_EVDEV.get(hid_usage)
    parts.append(pretty(evdev_name) if evdev_name else f"HID 0x{hid_usage:02x}")
    return " + ".join(parts)


def evdev_name_from_hw_keycode(hw_keycode: int):
    """GTK/X11/Wayland hardware keycode -> evdev name (layout independent)."""
    code = hw_keycode - 8
    name = KEYCODE_NAMES.get(code)
    if name:
        return name
    try:
        from evdev import ecodes
        n = ecodes.KEY.get(code)
        if isinstance(n, list):
            n = n[0]
        return n
    except ImportError:
        return None


GDK_BUTTON_TO_EVDEV = {
    1: "BTN_LEFT",
    2: "BTN_MIDDLE",
    3: "BTN_RIGHT",
    8: "BTN_SIDE",
    9: "BTN_EXTRA",
}
