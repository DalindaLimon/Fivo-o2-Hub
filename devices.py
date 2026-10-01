"""
Per-model knowledge the protocol can't tell us: what each button index is
physically called. Everything else (DPI range, polling rates, LED zones,
button count) is read from the device itself.
"""

G502_BUTTONS = [
    ("Left Click", "G1"),
    ("Right Click", "G2"),
    ("Middle Click (wheel)", "G3"),
    ("Back", "G4 · thumb, rear"),
    ("Forward", "G5 · thumb, front"),
    ("Sniper / DPI Shift", "G6 · below the thumb buttons"),
    ("DPI Down", "G7 · next to left click"),
    ("DPI Up", "G8 · next to left click"),
    ("Top Button", "G9 · behind the wheel"),
    ("Wheel Tilt Right", "scroll wheel, push right"),
    ("Wheel Tilt Left", "scroll wheel, push left"),
]

MODELS = {
    "G502": G502_BUTTONS,
}


def button_labels(device_name, count):
    """[(title, subtitle)] for each button index."""
    for key, labels in MODELS.items():
        if key.lower() in device_name.lower():
            out = list(labels[:count])
            break
    else:
        out = []
    while len(out) < count:
        out.append((f"Button {len(out) + 1}", ""))
    return out


def zone_label(location, index):
    """Human name for an LED zone from its 0x8070 location code, e.g. 'Logo'."""
    from onboard import LED_LOCATION_NAMES
    return LED_LOCATION_NAMES.get(location, f"Zone {index + 1}")
