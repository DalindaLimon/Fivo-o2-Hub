"""
Minimal HID++ 2.0 driver talking straight to /dev/hidraw*.

This is the same protocol Logitech's own software uses. Every request is
a 20-byte "long" report:

    [0x11, device_index, feature_index, (function << 4) | sw_id, params...]

Feature *indexes* differ per device/firmware, so we look each feature ID up
once through the Root feature (0x0000) and cache the result.

Only the features a G502-class mouse needs are wrapped here; the onboard
profile *format* lives in onboard.py.
"""

import errno
import os
import re
import select
import struct
import threading
import time

REPORT_SHORT = 0x10
REPORT_LONG = 0x11
LONG_LEN = 20

# Feature IDs
F_ROOT = 0x0000
F_FEATURE_SET = 0x0001
F_FW_INFO = 0x0003
F_DEVICE_NAME = 0x0005
F_BATTERY_STATUS = 0x1000
F_BATTERY_VOLTAGE = 0x1001
F_UNIFIED_BATTERY = 0x1004
F_ADJUSTABLE_DPI = 0x2201
F_REPORT_RATE = 0x8060
F_COLOR_LED_EFFECTS = 0x8070
F_ONBOARD_PROFILES = 0x8100

ERROR_NAMES = {
    0x01: "unknown error",
    0x02: "invalid argument",
    0x03: "out of range",
    0x04: "hardware error",
    0x05: "internal error",
    0x06: "invalid feature index",
    0x07: "invalid function",
    0x08: "device busy",
    0x09: "unsupported",
}


class HidppError(RuntimeError):
    """Any HID++ failure. `code` is the HID++ 2.0 error code (see ERROR_NAMES) when the device sent one, else None."""
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


class DeviceOffline(HidppError):
    """The receiver answered but the mouse didn't (asleep, off, out of range)."""


class FeatureNotSupported(HidppError):
    """The device doesn't implement the requested HID++ feature (Root returned index 0)."""
    pass


class HidppDevice:
    """One HID++ 2.0 device reachable through a hidraw node."""

    def __init__(self, path, device_index=0xFF, sw_id=0x0A, timeout=1.5):
        self.path = path
        self.device_index = device_index
        self.sw_id = sw_id & 0x0F or 0x0A
        self.timeout = timeout
        self._lock = threading.RLock()
        self._features = {F_ROOT: 0}
        self._fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)

    # -- transport ------------------------------------------------------------

    def close(self):
        """Close the hidraw file descriptor. Safe to call twice."""
        with self._lock:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _drain(self):
        """Discard anything already queued on the fd (stale replies, notifications) before a new request."""
        while True:
            r, _, _ = select.select([self._fd], [], [], 0)
            if not r:
                return
            try:
                os.read(self._fd, 64)
            except BlockingIOError:
                return

    def _raw_request(self, feat_idx, function, params=b""):
        """Send one request by feature *index* and wait for its reply.

        Replies are matched on feature index + function + our software ID, so other
        programs talking to the same device (or unsolicited notifications) are ignored.
        Returns the 16 parameter bytes. Raises DeviceOffline on timeout / receiver
        error 0x8F, HidppError on a HID++ 2.0 error reply.
        """
        if self._fd is None:
            raise HidppError("device is closed")
        func_byte = ((function & 0x0F) << 4) | self.sw_id
        msg = bytes([REPORT_LONG, self.device_index, feat_idx, func_byte]) + bytes(params)
        if len(msg) > LONG_LEN:
            raise ValueError("HID++ params too long")
        msg += bytes(LONG_LEN - len(msg))

        self._drain()
        try:
            os.write(self._fd, msg)
        except OSError as e:
            if e.errno in (errno.ENODEV, errno.EIO, errno.ENXIO):
                raise DeviceOffline(f"device disappeared ({e})") from e
            raise HidppError(f"write failed: {e}") from e

        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DeviceOffline("no response from device (asleep or out of range?)")
            r, _, _ = select.select([self._fd], [], [], remaining)
            if not r:
                continue
            try:
                data = os.read(self._fd, 64)
            except BlockingIOError:
                continue
            except OSError as e:
                raise DeviceOffline(f"read failed ({e})") from e
            if len(data) < 7 or data[0] not in (REPORT_SHORT, REPORT_LONG):
                continue
            # HID++ 1.0 error from the receiver: target device not reachable.
            if data[2] == 0x8F and data[3] == feat_idx and data[4] == func_byte:
                raise DeviceOffline(f"receiver reports device unreachable (0x{data[5]:02x})")
            # HID++ 2.0 error
            if data[2] == 0xFF and data[3] == feat_idx and data[4] == func_byte:
                code = data[5]
                raise HidppError(
                    f"feature 0x{feat_idx:02x} fn {function}: {ERROR_NAMES.get(code, hex(code))}",
                    code=code,
                )
            if data[2] == feat_idx and data[3] == func_byte:
                return bytes(data[4:]).ljust(16, b"\0")

    def request(self, feature_id, function, params=b""):
        """Send `function` of `feature_id` (e.g. F_ADJUSTABLE_DPI) with `params`; returns the 16 reply bytes. Thread-safe."""
        with self._lock:
            idx = self.feature_index(feature_id)
            return self._raw_request(idx, function, params)

    # -- root / feature table -------------------------------------------------------

    def ping(self):
        """Return (major, minor) protocol version, raises on failure."""
        with self._lock:
            r = self._raw_request(0, 1, bytes([0, 0, 0x5A]))
            return r[0], r[1]

    def feature_index(self, feature_id):
        """Look up (and cache) the device-specific index of a feature ID via Root.getFeature. Raises FeatureNotSupported."""
        idx = self._features.get(feature_id)
        if idx is None:
            r = self._raw_request(0, 0, struct.pack(">H", feature_id))
            idx = r[0]
            self._features[feature_id] = idx
        if idx == 0 and feature_id != F_ROOT:
            raise FeatureNotSupported(f"feature 0x{feature_id:04x} not supported")
        return idx

    def has_feature(self, feature_id):
        """True if the device implements `feature_id`."""
        try:
            self.feature_index(feature_id)
            return True
        except FeatureNotSupported:
            return False

    def list_features(self):
        """All features as [(index, feature_id, type_flags, version)] — handy for exploring a new device."""
        count = self.request(F_FEATURE_SET, 0)[0]
        out = []
        for i in range(count + 1):
            r = self.request(F_FEATURE_SET, 1, bytes([i]))
            out.append((i, (r[0] << 8) | r[1], r[2], r[3]))
        return out

    # -- 0x0005 device name --------------------------------------------------------

    def get_name(self):
        """Marketing name, e.g. 'G502 LIGHTSPEED Wireless Gaming Mouse' (feature 0x0005)."""
        length = self.request(F_DEVICE_NAME, 0)[0]
        name = b""
        while len(name) < length:
            name += self.request(F_DEVICE_NAME, 1, bytes([len(name)]))
        return name[:length].decode("utf-8", "replace").strip("\0 ").strip()

    # -- 0x0003 firmware ---------------------------------------------------------------

    def get_firmware(self):
        """List of (type, 'PFX xx.yy.Bzzzz') entries. type 0 = main application."""
        r = self.request(F_FW_INFO, 0)
        count = r[0]
        out = []
        for i in range(count):
            e = self.request(F_FW_INFO, 1, bytes([i]))
            ftype = e[0] & 0x0F
            prefix = e[1:4].decode("ascii", "replace").strip("\0 ")
            version = f"{e[4]:02x}.{e[5]:02x}.B{e[6]:02x}{e[7]:02x}"
            out.append((ftype, f"{prefix} {version}".strip()))
        return out

    # -- battery ------------------------------------------------------------------

    # Logitech's own discharge curve for 1-cell LiPo, as used by Solaar.
    _VOLTAGE_CURVE = [
        (4186, 100), (4067, 90), (3989, 80), (3922, 70), (3859, 60),
        (3811, 50), (3778, 40), (3751, 30), (3717, 20), (3671, 10),
        (3646, 5), (3579, 2), (3500, 0),
    ]

    @classmethod
    def voltage_to_percent(cls, mv):
        """Estimate charge % from battery millivolts by interpolating _VOLTAGE_CURVE."""
        curve = cls._VOLTAGE_CURVE
        if mv >= curve[0][0]:
            return 100
        for (v_hi, p_hi), (v_lo, p_lo) in zip(curve, curve[1:]):
            if v_lo <= mv < v_hi:
                return round(p_lo + (p_hi - p_lo) * (mv - v_lo) / (v_hi - v_lo))
        return 0

    def get_battery(self):
        """Return dict(percent, charging, voltage|None) or None if unsupported."""
        if self.has_feature(F_UNIFIED_BATTERY):
            r = self.request(F_UNIFIED_BATTERY, 1)
            # [0] percent, [1] level flags, [2] charging status
            return {"percent": r[0], "charging": r[2] in (1, 2), "full": r[2] == 3, "voltage": None}
        if self.has_feature(F_BATTERY_VOLTAGE):
            r = self.request(F_BATTERY_VOLTAGE, 0)
            mv = (r[0] << 8) | r[1]
            flags = r[2]
            charging = bool(flags & 0x80)
            full = charging and (flags & 0x07) == 1
            return {"percent": self.voltage_to_percent(mv), "charging": charging,
                    "full": full, "voltage": mv}
        if self.has_feature(F_BATTERY_STATUS):
            r = self.request(F_BATTERY_STATUS, 0)
            return {"percent": r[0], "charging": r[2] in (1, 2), "full": r[2] == 3, "voltage": None}
        return None

    # -- 0x2201 adjustable DPI ---------------------------------------------------------

    def get_dpi_range(self, sensor=0):
        """Return (min, max, step) or an explicit list of allowed values."""
        r = self.request(F_ADJUSTABLE_DPI, 1, bytes([sensor]))
        values, step = [], None
        i = 1
        while i + 1 < 16:
            v = (r[i] << 8) | r[i + 1]
            if v == 0:
                break
            if v > 0xE000:
                step = v - 0xE000
            else:
                values.append(v)
            i += 2
        if step and len(values) >= 2:
            return {"min": values[0], "max": values[-1], "step": step, "list": None}
        return {"min": min(values), "max": max(values), "step": None, "list": values}

    def get_sensor_dpi(self, sensor=0):
        """The DPI the sensor is using right now."""
        r = self.request(F_ADJUSTABLE_DPI, 2, bytes([sensor]))
        return (r[1] << 8) | r[2]

    def set_sensor_dpi(self, dpi, sensor=0):
        """Set the sensor DPI immediately (temporary: in onboard mode the profile's stages win on the next stage change)."""
        self.request(F_ADJUSTABLE_DPI, 3, bytes([sensor]) + struct.pack(">H", dpi))

    # -- 0x8060 report rate --------------------------------------------------------------

    def get_report_rates(self):
        """Supported polling rates in Hz, highest first."""
        mask = self.request(F_REPORT_RATE, 0)[0]
        return sorted((1000 // (bit + 1) for bit in range(8) if mask & (1 << bit)), reverse=True)

    def get_report_rate(self):
        """Current polling rate in Hz."""
        ms = self.request(F_REPORT_RATE, 1)[0]
        return 1000 // max(ms, 1)

    def set_report_rate(self, hz):
        """Set the polling rate in Hz (must be one of get_report_rates()). Onboard profiles store their own rate."""
        self.request(F_REPORT_RATE, 2, bytes([max(1, 1000 // hz)]))

    # -- 0x8070 LED zones (info only; persistent LED state lives in onboard profiles) ------

    def get_led_zones(self):
        """List of dict(index, location, effects=[effect_id, ...])."""
        info = self.request(F_COLOR_LED_EFFECTS, 0)
        zones = []
        for z in range(info[0]):
            zi = self.request(F_COLOR_LED_EFFECTS, 1, bytes([z]))
            location = (zi[1] << 8) | zi[2]
            effects = []
            for e in range(zi[3]):
                ei = self.request(F_COLOR_LED_EFFECTS, 2, bytes([z, e]))
                effects.append((ei[2] << 8) | ei[3])
            zones.append({"index": z, "location": location, "effects": effects})
        return zones

    # -- 0x8100 onboard profiles -----------------------------------------------------------

    MODE_ONBOARD = 1
    MODE_HOST = 2

    def onboard_description(self):
        """Describe the onboard memory: profile count, button count, sector size, format IDs (feature 0x8100 fn 0)."""
        r = self.request(F_ONBOARD_PROFILES, 0)
        return {
            "memory_model": r[0],
            "profile_format": r[1],
            "macro_format": r[2],
            "profile_count": r[3],
            "rom_profile_count": r[4],
            "button_count": r[5],
            "sector_count": r[6],
            "sector_size": (r[7] << 8) | r[8],
            "mechanical_layout": r[9],
            "various_info": r[10],
        }

    def get_onboard_mode(self):
        """MODE_ONBOARD (1) — profiles in flash drive the mouse — or MODE_HOST (2) — software drives it."""
        return self.request(F_ONBOARD_PROFILES, 2)[0]

    def set_onboard_mode(self, mode):
        """Switch between MODE_ONBOARD and MODE_HOST. Fivo o2Hub always uses onboard mode."""
        self.request(F_ONBOARD_PROFILES, 1, bytes([mode]))

    def get_current_profile(self):
        """1-based index of the active onboard profile (0x01xx = ROM profile)."""
        r = self.request(F_ONBOARD_PROFILES, 4)
        return (r[0] << 8) | r[1]

    def set_current_profile(self, index):
        """Make onboard profile `index` (1-based) active. Also makes the mouse re-read that profile from flash."""
        self.request(F_ONBOARD_PROFILES, 3, struct.pack(">H", index))

    def get_current_dpi_index(self):
        """Index of the DPI stage in use within the active profile (0-based)."""
        return self.request(F_ONBOARD_PROFILES, 11)[0]

    def set_current_dpi_index(self, index):
        """Switch the active profile to DPI stage `index` (0-based)."""
        self.request(F_ONBOARD_PROFILES, 12, bytes([index]))

    def read_sector(self, sector, size):
        """Read `size` bytes of onboard-memory sector `sector` (16 bytes per request). Returns bytes."""
        out = bytearray(size)
        offset = 0
        with self._lock:
            while offset < size:
                # Firmware rejects reads that would run past the end of the
                # sector, so the last chunk is re-aligned to size - 16.
                o = min(offset, size - 16)
                chunk = self.request(F_ONBOARD_PROFILES, 5, struct.pack(">HH", sector, o))
                out[o:o + 16] = chunk[:16]
                offset += 16
        return bytes(out)

    def write_sector(self, sector, data):
        """Write a whole sector: start (fn 6), 16-byte chunks (fn 7), end (fn 8).

        `data` must already carry its CRC (see onboard.Profile.sealed). The last
        chunk is padded with 0xFF; the device ignores bytes past the declared size.
        """
        size = len(data)
        padded = bytes(data) + b"\xff" * (-size % 16)
        with self._lock:
            self.request(F_ONBOARD_PROFILES, 6, struct.pack(">HHH", sector, 0, size))
            for off in range(0, len(padded), 16):
                self.request(F_ONBOARD_PROFILES, 7, padded[off:off + 16])
            self.request(F_ONBOARD_PROFILES, 8)


def crc_ccitt(data):
    """CRC-16/CCITT-FALSE, as used to seal onboard-memory sectors."""
    crc = 0xFFFF
    for b in data:
        t = (crc >> 8) ^ b
        crc = (crc << 8) & 0xFFFF
        q = t ^ (t >> 4)
        crc ^= q
        q = (q << 5) & 0xFFFF
        crc ^= q
        q = (q << 7) & 0xFFFF
        crc ^= q
    return crc


# -- discovery -----------------------------------------------------------------------------

LOGITECH_VID = 0x046D


def _hidraw_info(node):
    """Parse /sys/class/hidraw/<node>/device/uevent into a dict, adding integer 'vid'/'pid'. None if unreadable."""
    info = {}
    try:
        with open(f"/sys/class/hidraw/{node}/device/uevent") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                info[k] = v
    except OSError:
        return None
    hid_id = info.get("HID_ID", "")
    try:
        _bus, vid, pid = hid_id.split(":")
        info["vid"], info["pid"] = int(vid, 16), int(pid, 16)
    except ValueError:
        return None
    return info


def find_devices():
    """Return [{path, name, pid, wireless}] for every hidraw node that answers HID++ 2.0.

    Receivers are skipped: with the logitech-dj driver loaded (the default),
    each paired device gets its own hidraw node, which is what we want.
    """
    try:
        nodes = sorted(os.listdir("/sys/class/hidraw"), key=lambda n: int(n[6:]) if n[6:].isdigit() else 0)
    except OSError:
        return []
    found = []
    for node in nodes:
        info = _hidraw_info(node)
        if not info or info["vid"] != LOGITECH_VID:
            continue
        if "receiver" in info.get("HID_NAME", "").lower():
            continue
        path = f"/dev/{node}"
        if not os.access(path, os.R_OK | os.W_OK):
            continue
        try:
            dev = HidppDevice(path, timeout=0.6)
        except OSError:
            continue
        try:
            major, _minor = dev.ping()
            if major >= 2:
                # logitech-dj children have a phys like ".../input2:1" (receiver slot 1)
                phys = info.get("HID_PHYS", "")
                found.append({"path": path, "name": info.get("HID_NAME", node), "pid": info["pid"],
                              "wireless": bool(re.search(r"/input\d+:\d+$", phys))})
        except (HidppError, OSError):
            pass
        finally:
            dev.close()
    return found


def unreadable_logitech_nodes():
    """hidraw nodes that belong to Logitech devices but that we can't open — used
    to give a precise permissions hint instead of 'no devices found'."""
    out = []
    try:
        nodes = os.listdir("/sys/class/hidraw")
    except OSError:
        return out
    for node in nodes:
        info = _hidraw_info(node)
        if info and info["vid"] == LOGITECH_VID and not os.access(f"/dev/{node}", os.R_OK | os.W_OK):
            out.append(f"/dev/{node} ({info.get('HID_NAME', '?')})")
    return out
