"""
Onboard-memory profiles (HID++ feature 0x8100), G402-family format.

This is the storage behind the mouse's "on-board memory mode":
DPI stages, polling rate, every button assignment (plus the G-Shift layer)
and LED effects all live in a flash sector per profile, sealed with a CRC.

Unlike libratbag, we never rebuild a sector from scratch: we keep the raw
bytes read from the mouse and only patch the fields the user changed, so
anything we don't understand (onboard macros, power settings, vendor
fields) survives a round trip untouched.

Sector layout (offsets in bytes; see libratbag src/hidpp20.c):
    0     report rate, as a period in ms (1 = 1000 Hz)
    1     default DPI stage index
    2     DPI-shift ("sniper") stage index
    3     5 x u16 LE DPI stages (0 / 0xFFFF = unused)
    13    profile colour (RGB)
    16    power mode, 17 angle snapping, 18..27 reserved
    28    u16 powersave timeout, 30 u16 poweroff timeout
    32    16 x 4-byte button bindings
    96    16 x 4-byte G-Shift button bindings
    160   profile name, 48 bytes
    208   2 x 11-byte LED effects
    230   2 x 11-byte alternate LED effects
    size-2  CRC-16/CCITT, big-endian
"""

import struct
from dataclasses import dataclass, field

import keys
from hidpp import crc_ccitt, HidppError

OFF_RATE = 0
OFF_DEFAULT_DPI = 1
OFF_SHIFT_DPI = 2
OFF_DPI = 3
OFF_BUTTONS = 32
OFF_ALT_BUTTONS = 96
OFF_NAME = 160
NAME_LEN = 48
OFF_LEDS = 208
OFF_ALT_LEDS = 230
LED_LEN = 11
DPI_SLOTS = 5
MAX_BUTTONS = 16

DIRECTORY_SECTOR = 0x0000
ROM_BASE = 0x0100

# -- button bindings -------------------------------------------------------------------

TYPE_MACRO = 0x00
TYPE_HID = 0x80
TYPE_SPECIAL = 0x90
TYPE_DISABLED = 0xFF

HID_NOOP = 0x00
HID_MOUSE = 0x01
HID_KEYBOARD = 0x02
HID_CONSUMER = 0x03

SPECIALS = {
    0x01: "Scroll Left (tilt)",
    0x02: "Scroll Right (tilt)",
    0x03: "DPI Up",
    0x04: "DPI Down",
    0x05: "DPI Cycle",
    0x06: "DPI Default",
    0x07: "DPI Shift (Sniper)",
    0x08: "Next Profile",
    0x09: "Previous Profile",
    0x0A: "Cycle Profiles",
    0x0B: "G-Shift",
    0x0C: "Battery Indicator",
}

MOUSE_BUTTON_NAMES = {
    1: "Left Click",
    2: "Right Click",
    3: "Middle Click",
    4: "Back",
    5: "Forward",
}

CONSUMER_NAMES = {
    0x00CD: "Play / Pause",
    0x00B5: "Next Track",
    0x00B6: "Previous Track",
    0x00B7: "Stop",
    0x00E2: "Mute",
    0x00E9: "Volume Up",
    0x00EA: "Volume Down",
    0x006F: "Brightness Up",
    0x0070: "Brightness Down",
    0x0192: "Calculator",
    0x018A: "Mail",
    0x0194: "File Manager",
    0x0223: "Browser Home",
    0x0224: "Browser Back",
    0x0225: "Browser Forward",
    0x0227: "Browser Refresh",
    0x0221: "Search",
}


@dataclass
class Binding:
    """One 4-byte button binding. `raw` is authoritative; helpers build it."""
    raw: bytes = b"\xff\x00\x00\x00"

    # constructors ---------------------------------------------------------------
    @staticmethod
    def mouse(button):
        """Binding that sends mouse button `button` (1 = left … 5 = forward, up to 16)."""
        return Binding(bytes([TYPE_HID, HID_MOUSE]) + struct.pack(">H", 1 << (button - 1)))

    @staticmethod
    def key(hid_usage, modifiers=0):
        """Binding that sends keyboard key `hid_usage` (USB HID usage, see keys.EVDEV_TO_HID) with HID `modifiers` bits."""
        return Binding(bytes([TYPE_HID, HID_KEYBOARD, modifiers & 0xFF, hid_usage & 0xFF]))

    @staticmethod
    def consumer(usage):
        """Binding that sends a consumer-control usage (media keys, see CONSUMER_NAMES)."""
        return Binding(bytes([TYPE_HID, HID_CONSUMER]) + struct.pack(">H", usage))

    @staticmethod
    def special(code):
        """Binding to a firmware function: DPI up/down/shift, profile switching, G-Shift… (see SPECIALS)."""
        return Binding(bytes([TYPE_SPECIAL, code, 0x00, 0x00]))

    @staticmethod
    def disabled():
        """Binding that does nothing."""
        return Binding(b"\xff\x00\x00\x00")

    # inspection -----------------------------------------------------------------
    @property
    def type(self):
        """Raw type byte: TYPE_HID, TYPE_SPECIAL, TYPE_MACRO or TYPE_DISABLED."""
        return self.raw[0]

    @property
    def kind(self):
        """Friendly category: 'mouse', 'key', 'consumer', 'special', 'onboard_macro', 'disabled' or 'unknown'."""
        t, s = self.raw[0], self.raw[1]
        if t == TYPE_HID:
            return {HID_MOUSE: "mouse", HID_KEYBOARD: "key", HID_CONSUMER: "consumer",
                    HID_NOOP: "disabled"}.get(s, "unknown")
        if t == TYPE_SPECIAL:
            return "special"
        if t == TYPE_MACRO:
            return "onboard_macro"
        if t == TYPE_DISABLED:
            return "disabled"
        return "unknown"

    @property
    def mouse_button(self):
        """For kind 'mouse': the button number (1-based), decoded from the bitmask."""
        mask = struct.unpack(">H", self.raw[2:4])[0]
        return mask.bit_length() if mask else 0

    @property
    def key_usage(self):
        """For kind 'key': the USB HID keyboard usage."""
        return self.raw[3]

    @property
    def modifiers(self):
        """For kind 'key': HID modifier bits (keys.MOD_*)."""
        return self.raw[2]

    @property
    def consumer_usage(self):
        """For kind 'consumer': the 16-bit consumer usage."""
        return struct.unpack(">H", self.raw[2:4])[0]

    @property
    def special_code(self):
        """For kind 'special': the firmware function code (keys of SPECIALS)."""
        return self.raw[1]

    def describe(self, macro_lookup=None):
        """Human-readable label. macro_lookup(hid_usage) -> macro name or None."""
        k = self.kind
        if k == "mouse":
            b = self.mouse_button
            return MOUSE_BUTTON_NAMES.get(b, f"Mouse Button {b}")
        if k == "key":
            if self.modifiers == 0 and macro_lookup:
                name = macro_lookup(self.key_usage)
                if name:
                    return f"Macro: {name}"
            return keys.describe_combo(self.modifiers, self.key_usage)
        if k == "consumer":
            return CONSUMER_NAMES.get(self.consumer_usage, f"Media 0x{self.consumer_usage:04x}")
        if k == "special":
            return SPECIALS.get(self.special_code, f"Special 0x{self.special_code:02x}")
        if k == "onboard_macro":
            return "Onboard Macro"
        if k == "disabled":
            return "Disabled"
        return f"Unknown ({self.raw.hex()})"

    def __eq__(self, other):
        return isinstance(other, Binding) and self.raw == other.raw


# -- LEDs --------------------------------------------------------------------------------

LED_OFF = 0x00
LED_FIXED = 0x01
LED_CYCLE = 0x03
LED_BREATHING = 0x0A

LED_MODE_NAMES = {
    LED_OFF: "Off",
    LED_FIXED: "Fixed",
    LED_CYCLE: "Color Cycle",
    LED_BREATHING: "Breathing",
}

LED_LOCATION_NAMES = {1: "Primary (DPI indicator)", 2: "Logo"}


@dataclass
class LedEffect:
    """One LED zone's effect as stored in a profile (11 bytes on the device).

    `mode` is one of LED_OFF / LED_FIXED / LED_CYCLE / LED_BREATHING; `period` is
    milliseconds per cycle; `brightness` is percent. Unknown modes keep their
    original bytes in `raw` so they round-trip unchanged.
    """
    mode: int = LED_FIXED
    color: tuple = (0, 160, 255)
    period: int = 5000        # ms for cycle / breathing
    brightness: int = 100     # percent
    raw: bytes = field(default=b"", repr=False)  # original bytes for unknown modes

    @staticmethod
    def decode(b):
        """Parse the 11 stored bytes into an LedEffect."""
        mode = b[0]
        if mode == LED_FIXED:
            return LedEffect(mode, tuple(b[1:4]), raw=bytes(b))
        if mode == LED_CYCLE:
            period = struct.unpack(">H", b[6:8])[0]
            return LedEffect(mode, (0, 160, 255), period or 10000, b[8] or 100, raw=bytes(b))
        if mode == LED_BREATHING:
            period = struct.unpack(">H", b[4:6])[0]
            return LedEffect(mode, tuple(b[1:4]), period or 5000, b[7] or 100, raw=bytes(b))
        if mode == LED_OFF:
            return LedEffect(LED_OFF, raw=bytes(b))
        return LedEffect(mode, raw=bytes(b))

    def encode(self):
        """Serialise back to the 11-byte on-device layout."""
        out = bytearray(LED_LEN)
        out[0] = self.mode
        bright = 0 if self.brightness >= 100 else max(1, int(self.brightness))
        if self.mode == LED_FIXED:
            out[1:4] = bytes(self.color)
        elif self.mode == LED_CYCLE:
            out[6:8] = struct.pack(">H", int(self.period))
            out[8] = bright
        elif self.mode == LED_BREATHING:
            out[1:4] = bytes(self.color)
            out[4:6] = struct.pack(">H", int(self.period))
            out[6] = 0  # default waveform
            out[7] = bright
        elif self.mode == LED_OFF:
            pass
        else:
            return bytes(self.raw[:LED_LEN]).ljust(LED_LEN, b"\0")
        return bytes(out)


# -- profiles ----------------------------------------------------------------------------


class Profile:
    """One onboard profile: a mutable view over its raw flash sector.

    Properties read and patch `data` in place; nothing touches the device until
    OnboardProfiles.save(). `index` is 1-based, `sector` is its flash address,
    `from_rom` means the user sector was empty/corrupt and the factory defaults
    were loaded instead.
    """
    def __init__(self, index, sector, data, enabled, button_count, sector_size, from_rom=False):
        self.index = index            # 1-based
        self.sector = sector
        self.data = bytearray(data)
        self.enabled = enabled
        self.button_count = min(button_count, MAX_BUTTONS)
        self.sector_size = sector_size
        self.from_rom = from_rom      # True if user sector was blank/corrupt

    # report rate ----------------------------------------------------------------
    @property
    def report_rate(self):
        """Polling rate in Hz (stored as a period in ms). Settable."""
        return 1000 // max(1, self.data[OFF_RATE])

    @report_rate.setter
    def report_rate(self, hz):
        self.data[OFF_RATE] = max(1, 1000 // int(hz))

    # DPI --------------------------------------------------------------------------
    @property
    def dpis(self):
        """The 5 DPI slots, 0 for unused. Assign a list of 1–5 values to change stages
        (default/shift indices are clamped to the new count)."""
        out = []
        for i in range(DPI_SLOTS):
            v = struct.unpack_from("<H", self.data, OFF_DPI + 2 * i)[0]
            out.append(0 if v in (0, 0xFFFF) else v)
        return out

    @dpis.setter
    def dpis(self, values):
        values = [int(v) for v in values if v][:DPI_SLOTS]
        if not values:
            raise ValueError("a profile needs at least one DPI stage")
        values += [0] * (DPI_SLOTS - len(values))
        for i, v in enumerate(values):
            struct.pack_into("<H", self.data, OFF_DPI + 2 * i, v)
        n = sum(1 for v in values if v)
        if self.default_dpi_index >= n:
            self.default_dpi_index = 0
        if self.shift_dpi_index >= n:
            self.shift_dpi_index = 0

    @property
    def default_dpi_index(self):
        """Stage the mouse starts on when the profile loads. Settable."""
        return self.data[OFF_DEFAULT_DPI]

    @default_dpi_index.setter
    def default_dpi_index(self, i):
        self.data[OFF_DEFAULT_DPI] = int(i)

    @property
    def shift_dpi_index(self):
        """Stage used while the Sniper / DPI-Shift button is held. Settable."""
        return self.data[OFF_SHIFT_DPI]

    @shift_dpi_index.setter
    def shift_dpi_index(self, i):
        self.data[OFF_SHIFT_DPI] = int(i)

    # name -------------------------------------------------------------------------
    @property
    def name(self):
        """User-visible profile name ('' if unset). Settable; stored as UTF-16LE."""
        raw = bytes(self.data[OFF_NAME:OFF_NAME + NAME_LEN])
        if raw == b"\xff" * NAME_LEN or raw == b"\0" * NAME_LEN:
            return ""
        # Newer firmware (incl. G502 LIGHTSPEED) stores UTF-16LE; older ones ASCII.
        if len(raw) > 1 and raw[1] == 0:
            text = raw.decode("utf-16-le", "replace")
        else:
            text = raw.decode("latin-1", "replace")
        text = text.split("\0")[0].strip("￿\xff ")
        return "" if text == "PROFILE_NAME_DEFAULT" else text

    @name.setter
    def name(self, text):
        enc = (text or "").encode("utf-16-le")[:NAME_LEN - 2]
        self.data[OFF_NAME:OFF_NAME + NAME_LEN] = enc.ljust(NAME_LEN, b"\0")

    def display_name(self):
        """The name, or 'Profile N' when it has none."""
        return self.name or f"Profile {self.index}"

    # buttons ------------------------------------------------------------------------
    def get_button(self, i, gshift=False):
        """Binding of button `i` (0-based) on the normal layer, or the G-Shift layer if `gshift`."""
        off = (OFF_ALT_BUTTONS if gshift else OFF_BUTTONS) + 4 * i
        return Binding(bytes(self.data[off:off + 4]))

    def set_button(self, i, binding, gshift=False):
        """Store a Binding for button `i` on the normal or G-Shift layer."""
        off = (OFF_ALT_BUTTONS if gshift else OFF_BUTTONS) + 4 * i
        self.data[off:off + 4] = binding.raw

    def has_gshift_button(self):
        """True if some button is bound to G-Shift (otherwise the G-Shift layer is unreachable)."""
        return any(self.get_button(i).kind == "special" and self.get_button(i).special_code == 0x0B
                   for i in range(self.button_count))

    # LEDs ----------------------------------------------------------------------------
    def get_led(self, zone):
        """LedEffect of zone `zone` (0 = primary/DPI, 1 = logo on the G502)."""
        off = OFF_LEDS + LED_LEN * zone
        return LedEffect.decode(self.data[off:off + LED_LEN])

    def set_led(self, zone, effect):
        """Store an LedEffect for `zone` (also mirrored into the alternate-LED slot, as libratbag does)."""
        enc = effect.encode()
        for base in (OFF_LEDS, OFF_ALT_LEDS):
            off = base + LED_LEN * zone
            if off + LED_LEN <= self.sector_size - 2:
                self.data[off:off + LED_LEN] = enc

    # serialisation ---------------------------------------------------------------------
    def sealed(self):
        """Sector bytes with a freshly computed CRC — what gets written to flash."""
        out = bytearray(self.data[:self.sector_size])
        crc = crc_ccitt(out[:-2])
        out[-2:] = struct.pack(">H", crc)
        return bytes(out)


def sector_valid(data):
    """True if a sector's trailing big-endian CRC matches its contents."""
    return len(data) > 2 and crc_ccitt(data[:-2]) == struct.unpack(">H", data[-2:])[0]


class OnboardProfiles:
    """Reads/writes the whole onboard-profile store of one device."""

    def __init__(self, dev):
        self.dev = dev
        self.desc = dev.onboard_description()
        if self.desc["memory_model"] != 0x01 or self.desc["macro_format"] != 0x01:
            raise HidppError(
                f"Unsupported onboard memory layout "
                f"(model {self.desc['memory_model']}, format {self.desc['profile_format']})")
        self.sector_size = self.desc["sector_size"]
        self.profiles = []
        self.rom_template = None
        self.load()

    @property
    def button_count(self):
        """Number of buttons per profile (11 on the G502)."""
        return min(self.desc["button_count"], MAX_BUTTONS)

    def load(self):
        """(Re)read the profile directory and every profile from the device.

        Forces onboard mode if needed. Profiles whose user sector fails its CRC are
        loaded from the matching ROM (factory) profile and flagged `from_rom`.
        """
        dev = self.dev
        if dev.get_onboard_mode() != dev.MODE_ONBOARD:
            dev.set_onboard_mode(dev.MODE_ONBOARD)

        self.rom_template = dev.read_sector(ROM_BASE + 1, self.sector_size)

        directory = dev.read_sector(DIRECTORY_SECTOR, self.sector_size)
        entries = {}
        if sector_valid(directory):
            for i in range(self.desc["profile_count"]):
                addr = struct.unpack_from(">H", directory, 4 * i)[0]
                if addr == 0xFFFF:
                    break
                entries[i + 1] = (addr, bool(directory[4 * i + 2]))

        self.profiles = []
        for i in range(1, self.desc["profile_count"] + 1):
            addr, enabled = entries.get(i, (i, False))
            data = dev.read_sector(addr, self.sector_size)
            from_rom = False
            if not sector_valid(data):
                rom_idx = i if i <= self.desc["rom_profile_count"] else 1
                data = dev.read_sector(ROM_BASE + rom_idx, self.sector_size)
                from_rom = True
            self.profiles.append(Profile(i, addr, data, enabled, self.button_count,
                                         self.sector_size, from_rom))
        return self.profiles

    def active_index(self):
        """1-based index of the profile the mouse is using, or None if it's a ROM profile."""
        cur = self.dev.get_current_profile()
        return cur if 1 <= cur <= len(self.profiles) else None

    def get(self, index):
        """Profile by 1-based index."""
        return self.profiles[index - 1]

    def write_directory(self):
        """Rewrite sector 0, the list of profile addresses and their enabled flags."""
        buf = bytearray(b"\xff" * self.sector_size)
        pos = 0
        for p in self.profiles:
            struct.pack_into(">H", buf, pos, p.sector)
            buf[pos + 2] = 1 if p.enabled else 0
            buf[pos + 3] = 0
            pos += 4
        buf[pos:pos + 4] = b"\xff\xff\x00\x00"
        buf[-2:] = struct.pack(">H", crc_ccitt(buf[:-2]))
        self.dev.write_sector(DIRECTORY_SECTOR, bytes(buf))

    def save(self, profile, reload_if_active=True, data=None):
        """Write one profile to flash; if it's the live one, make the mouse reload it
        while keeping the DPI stage the user was on. `data` is an optional
        snapshot from profile.sealed() taken on the UI thread."""
        self.dev.write_sector(profile.sector, data if data is not None else profile.sealed())
        profile.from_rom = False
        if not profile.enabled:
            profile.enabled = True
            self.write_directory()
        if reload_if_active and self.active_index() == profile.index:
            try:
                dpi_idx = self.dev.get_current_dpi_index()
            except HidppError:
                dpi_idx = None
            self.dev.set_current_profile(profile.index)
            n_stages = sum(1 for v in profile.dpis if v)
            if dpi_idx is not None and dpi_idx < n_stages:
                try:
                    self.dev.set_current_dpi_index(dpi_idx)
                except HidppError:
                    pass

    def set_enabled(self, index, enabled):
        """Enable/disable a profile (at least one must stay enabled). Writes ROM defaults first if the slot was empty."""
        p = self.get(index)
        if not enabled and sum(1 for q in self.profiles if q.enabled) <= 1 and p.enabled:
            raise HidppError("At least one profile has to stay enabled.")
        if enabled and p.from_rom:
            # Materialise the ROM defaults into the user sector first.
            self.dev.write_sector(p.sector, p.sealed())
            p.from_rom = False
        p.enabled = enabled
        self.write_directory()
        if not enabled and self.active_index() == index:
            nxt = next(q for q in self.profiles if q.enabled)
            self.dev.set_current_profile(nxt.index)

    def activate(self, index):
        """Enable (if needed) and switch the mouse to profile `index`."""
        p = self.get(index)
        if not p.enabled:
            self.set_enabled(index, True)
        self.dev.set_current_profile(index)

    def reset_to_factory(self, index):
        """Replace a profile with the factory ROM profile (keeping its name) and save it."""
        p = self.get(index)
        name = p.name
        p.data = bytearray(self.rom_template)
        if name:
            p.name = name
        self.save(p)
        return p
