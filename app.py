#!/usr/bin/env python3
"""
Fivo o2Hub: configure Logitech gaming mice on Linux, starting with the G502.

Talks HID++ 2.0 straight to the mouse (hidpp.py / onboard.py), so no extra
daemon or root is needed. Everything except software macros is stored in
the mouse's onboard memory and keeps working with this app closed, on any
computer. Software macros run in a small background service (o2hubd.py).

See docs/ARCHITECTURE.md for how the pieces fit together.
"""

import colorsys
import math
import queue
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

import appinfo
import devices
import hidpp
import keys
import mouse_art
import onboard
import scroll_quirk
import service
from macro_ui import MacrosPage
from macros import MacroStore
from onboard import Binding, LedEffect

APP_ID = appinfo.APP_ID
SAVE_DELAY_MS = 600

CSS = """
row.o2-hover { background-color: alpha(@accent_bg_color, 0.18); }
"""
POLL_SECONDS = 3
BATTERY_EVERY = 10  # polls


# ---------------------------------------------------------------------------------------
# background worker: every HID++ call happens on this one thread, in order
# ---------------------------------------------------------------------------------------

class Worker:
    """Runs every HID++ call on one background thread, in submission order.

    The UI never blocks on the (wireless, possibly sleeping) mouse; results come
    back on the GTK main loop via callbacks.
    """
    def __init__(self):
        self._q = queue.Queue()
        threading.Thread(target=self._loop, daemon=True).start()

    def run(self, fn, done=None, error=None):
        """Queue fn(); afterwards call done(result) or error(exception) on the main loop."""
        self._q.put((fn, done, error))

    def _loop(self):
        """Thread body: execute queued jobs forever."""
        while True:
            fn, done, error = self._q.get()
            try:
                result = fn()
            except Exception as e:  # noqa: BLE001 - surfaced to the UI
                if error:
                    GLib.idle_add(lambda cb=error, e=e: (cb(e), False)[1])
                else:
                    print(f"worker error: {e!r}", file=sys.stderr)
                continue
            if done:
                # bind via defaults: the loop reassigns `done` before idle runs
                GLib.idle_add(lambda cb=done, r=result: (cb(r), False)[1])


def page_box(max_width=760):
    """A scrollable, width-clamped vertical box: the standard page body.
    Returns (outer widget to put in the stack, inner box to fill)."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                  margin_top=24, margin_bottom=24, margin_start=12, margin_end=12)
    clamp = Adw.Clamp(maximum_size=max_width, child=box)
    scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp)
    return scroller, box


def art_columns(box, art_widget_box):
    """Lay out [artwork | controls] side by side. Returns (row box, controls box).
    The row box flips to vertical on narrow windows (see MainWindow breakpoints)."""
    row = Gtk.Box(spacing=36)
    row.append(art_widget_box)
    controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, hexpand=True)
    row.append(controls)
    box.append(row)
    return row, controls


def make_views(device_name, interactive):
    """One MouseView per available photo of this model (top, then side), sized to
    their aspect ratios so they stack neatly in a ~330 px column."""
    out = []
    for name, art in mouse_art.views_for(device_name).items():
        width = 290 if name == "top" else 330
        out.append(mouse_art.MouseView(art, interactive=interactive,
                                       size=(width, round(width * art.height / art.width))))
    return out


def led_preview(effect, t):
    """Colour an LED shows at time t (seconds) for an onboard LedEffect.
    Returns (r, g, b, intensity 0..1) for mouse_art."""
    if effect.mode == onboard.LED_FIXED:
        return (*effect.color, 1.0)
    if effect.mode == onboard.LED_BREATHING:
        phase = (t * 1000 / max(effect.period, 200)) % 1.0
        return (*effect.color, (effect.brightness / 100) * (0.08 + 0.92 * (0.5 - 0.5 * math.cos(2 * math.pi * phase))))
    if effect.mode == onboard.LED_CYCLE:
        hue = (t * 1000 / max(effect.period, 200)) % 1.0
        r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        return (r * 255, g * 255, b * 255, effect.brightness / 100)
    return (0, 0, 0, 0.0)


def clear_group(group, rows):
    """Remove the given rows from an Adw.PreferencesGroup and empty the list."""
    for r in rows:
        group.remove(r)
    rows.clear()


# ---------------------------------------------------------------------------------------
# Sensitivity (DPI + polling rate)
# ---------------------------------------------------------------------------------------

class SensitivityPage:
    """DPI stages (value, default, shift), and polling rate."""
    title = "Sensitivity"
    icon = "input-mouse-symbolic"

    def __init__(self, win):
        self.win = win
        self.widget, box = page_box()

        self.group = Adw.PreferencesGroup(
            title="DPI Stages",
            description="The DPI Up / Down buttons step through these stages. "
                        "“Default” is the stage the mouse starts on; "
                        "“Shift” is used while the Sniper button is held.")
        self.add_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add stage",
                                  css_classes=["flat"], valign=Gtk.Align.CENTER)
        self.add_btn.connect("clicked", self.on_add)
        self.group.set_header_suffix(self.add_btn)
        box.append(self.group)
        self.rows = []

        rate_group = Adw.PreferencesGroup(title="Report Rate")
        self.rate_row = Adw.ComboRow(title="Polling rate",
                                     subtitle="How often the mouse reports its position")
        self.rate_row.connect("notify::selected", self.on_rate)
        rate_group.add(self.rate_row)
        box.append(rate_group)

        wheel_group = Adw.PreferencesGroup(
            title="Scroll Wheel",
            description="Linux reads this wheel in 1/8-notch steps, so a fast flick of the mouse "
                        "can rock the wheel enough to scroll. Notched scrolling only counts real "
                        "wheel clicks, like on Windows.")
        self.notched_row = Adw.SwitchRow(title="Notched scrolling",
                                         subtitle="Ignore tiny wheel movements")
        self.notched_row.set_active(scroll_quirk.is_enabled())
        self.notched_row.connect("notify::active", self.on_notched)
        wheel_group.add(self.notched_row)
        box.append(wheel_group)
        self._notched_busy = False
        self._notched_changed = False

        self._building = False
        self._rates = []

    def refresh(self):
        """Rebuild the stage rows from the edited profile and the live stage index."""
        p = self.win.profile
        if not p:
            return
        self._building = True
        clear_group(self.group, self.rows)
        rng = self.win.info["dpi_range"]
        stages = [v for v in p.dpis if v]
        active = self.win.live_dpi_index
        default_radio = None
        shift_radio = None
        for i, dpi in enumerate(stages):
            row = Adw.ActionRow(title=f"Stage {i + 1}")
            if i == active and self.win.profile_is_live():
                row.set_subtitle("● In use now")
                row.add_css_class("accent")

            spin = Gtk.SpinButton(valign=Gtk.Align.CENTER, numeric=True, width_chars=6)
            step = rng.get("step") or 50
            spin.set_adjustment(Gtk.Adjustment(value=dpi, lower=rng["min"], upper=rng["max"],
                                               step_increment=step, page_increment=step * 10))
            spin.set_snap_to_ticks(True)
            spin.connect("value-changed", self.on_dpi_changed, i)
            row.add_suffix(spin)

            d = Gtk.CheckButton(label="Default", valign=Gtk.Align.CENTER, active=(i == p.default_dpi_index))
            if default_radio:
                d.set_group(default_radio)
            else:
                default_radio = d
            d.connect("toggled", self.on_default, i)
            row.add_suffix(d)

            s = Gtk.CheckButton(label="Shift", valign=Gtk.Align.CENTER, active=(i == p.shift_dpi_index))
            if shift_radio:
                s.set_group(shift_radio)
            else:
                shift_radio = s
            s.connect("toggled", self.on_shift, i)
            row.add_suffix(s)

            rm = Gtk.Button(icon_name="user-trash-symbolic", css_classes=["flat"],
                            valign=Gtk.Align.CENTER, tooltip_text="Remove stage",
                            sensitive=len(stages) > 1)
            rm.connect("clicked", self.on_remove, i)
            row.add_suffix(rm)
            self.group.add(row)
            self.rows.append(row)
        self.add_btn.set_sensitive(len(stages) < onboard.DPI_SLOTS)

        self._rates = self.win.info["report_rates"]
        self.rate_row.set_model(Gtk.StringList.new([f"{r} Hz" + ("  (1 ms)" if r == 1000 else "")
                                                    for r in self._rates]))
        if p.report_rate in self._rates:
            self.rate_row.set_selected(self._rates.index(p.report_rate))
        self._building = False

    def on_notched(self, row, _p):
        """Notched-scrolling switch: install/remove the libinput quirk (asks for the
        admin password via pkexec), off the UI thread."""
        if self._notched_busy:
            return
        want = row.get_active()
        if want == scroll_quirk.is_enabled():
            return
        self._notched_busy = True
        row.set_sensitive(False)
        row.set_subtitle("Waiting for authentication…")

        def work():
            scroll_quirk.set_enabled(want)

        def done(_r):
            self._notched_busy = False
            self._notched_changed = True
            row.set_sensitive(True)
            row.set_subtitle("Log out and back in to apply")
            self.win.toast("Saved — log out and back in to apply")

        def err(e):
            self._notched_busy = True       # don't re-trigger while reverting the switch
            row.set_active(scroll_quirk.is_enabled())
            self._notched_busy = False
            row.set_sensitive(True)
            row.set_subtitle("Ignore tiny wheel movements")
            if "cancelled" not in str(e):
                self.win.show_error(f"Couldn't change notched scrolling:\n\n{e}")

        # Not the HID++ worker: pkexec can block for as long as the password prompt is open.
        threading.Thread(target=lambda: self._run_bg(work, done, err), daemon=True).start()

    @staticmethod
    def _run_bg(fn, done, err):
        """Run fn() on the current (background) thread, reporting back on the main loop."""
        try:
            r = fn()
        except Exception as e:  # noqa: BLE001 - shown to the user
            GLib.idle_add(lambda e=e: (err(e), False)[1])
            return
        GLib.idle_add(lambda r=r: (done(r), False)[1])

    def _stages(self):
        """The profile's non-empty DPI stages."""
        return [v for v in self.win.profile.dpis if v]

    def on_dpi_changed(self, spin, i):
        """A stage's DPI spin button changed."""
        if self._building:
            return
        stages = self._stages()
        stages[i] = int(spin.get_value())
        self.win.profile.dpis = stages
        self.win.schedule_save(refresh=False)

    def on_default(self, btn, i):
        """A 'Default' radio was selected."""
        if not self._building and btn.get_active():
            self.win.profile.default_dpi_index = i
            self.win.schedule_save(refresh=False)

    def on_shift(self, btn, i):
        """A 'Shift' radio was selected."""
        if not self._building and btn.get_active():
            self.win.profile.shift_dpi_index = i
            self.win.schedule_save(refresh=False)

    def on_add(self, _b):
        """Append a stage (double the last one, capped at the sensor max)."""
        stages = self._stages()
        rng = self.win.info["dpi_range"]
        stages.append(min(rng["max"], (stages[-1] if stages else 800) * 2))
        self.win.profile.dpis = stages
        self.win.schedule_save()

    def on_remove(self, _b, i):
        """Delete a stage, keeping default/shift pointing at sensible stages."""
        p = self.win.profile
        stages = self._stages()
        del stages[i]
        for attr in ("default_dpi_index", "shift_dpi_index"):
            v = getattr(p, attr)
            if v > i or v >= len(stages):
                setattr(p, attr, max(0, v - 1))
        p.dpis = stages
        self.win.schedule_save()

    def on_rate(self, row, _p):
        """Polling-rate combo changed."""
        if self._building or not self._rates:
            return
        idx = row.get_selected()
        if idx < len(self._rates):
            self.win.profile.report_rate = self._rates[idx]
            self.win.schedule_save(refresh=False)


# ---------------------------------------------------------------------------------------
# Assignments (buttons)
# ---------------------------------------------------------------------------------------

CATEGORIES = [
    ("mouse", "Mouse Button"),
    ("key", "Keystroke"),
    ("consumer", "Media & System"),
    ("special", "DPI / Profile / Scroll"),
    ("macro", "Macro"),
    ("disabled", "Disabled"),
]


class AssignDialog(Adw.Dialog):
    """Dialog to choose what a button does. Calls on_apply(binding, macro).

    Exactly one of the two is set: a Binding for everything stored on the mouse,
    or a Macro (the page then allocates its trigger key).
    """
    def __init__(self, win, title, binding, on_apply):
        super().__init__(title=title, content_width=460)
        self.win = win
        self.on_apply = on_apply
        self._capturing = False

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)
        apply_btn = Gtk.Button(label="Assign", css_classes=["suggested-action"])
        apply_btn.connect("clicked", self.on_apply_clicked)
        header.pack_end(apply_btn)
        tv.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                      margin_top=12, margin_bottom=18, margin_start=12, margin_end=12)
        tv.set_content(box)
        self.set_child(tv)

        g = Adw.PreferencesGroup()
        self.cat_row = Adw.ComboRow(title="Action type",
                                    model=Gtk.StringList.new([c[1] for c in CATEGORIES]))
        g.add(self.cat_row)
        box.append(g)

        self.stack = Gtk.Stack(vhomogeneous=False, transition_type=Gtk.StackTransitionType.CROSSFADE)
        box.append(self.stack)

        # mouse
        mg = Adw.PreferencesGroup()
        self.mouse_row = Adw.ComboRow(title="Button", model=Gtk.StringList.new(
            [onboard.MOUSE_BUTTON_NAMES.get(i, f"Mouse Button {i}") for i in range(1, 9)]))
        mg.add(self.mouse_row)
        self.stack.add_named(mg, "mouse")

        # keystroke
        kg = Adw.PreferencesGroup(description="Stored on the mouse — works without this app.")
        self.capture_btn = Gtk.Button(label="Record a key combination…", margin_bottom=6)
        self.capture_btn.connect("clicked", self.on_capture)
        kg.add(self.capture_btn)
        self.key_row = Adw.ComboRow(title="Key", enable_search=True,
                                    model=Gtk.StringList.new([keys.pretty(k) for k in keys.BINDABLE_KEYS]))
        self.key_row.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        kg.add(self.key_row)
        self.mod_checks = {}
        mods_row = Adw.ActionRow(title="Modifiers")
        for mask, label in [(keys.MOD_LCTRL, "Ctrl"), (keys.MOD_LSHIFT, "Shift"),
                            (keys.MOD_LALT, "Alt"), (keys.MOD_LMETA, "Super")]:
            cb = Gtk.CheckButton(label=label, valign=Gtk.Align.CENTER)
            mods_row.add_suffix(cb)
            self.mod_checks[mask] = cb
        kg.add(mods_row)
        self.stack.add_named(kg, "key")

        # consumer
        cg = Adw.PreferencesGroup()
        self._consumers = list(onboard.CONSUMER_NAMES.items())
        self.consumer_row = Adw.ComboRow(title="Action", model=Gtk.StringList.new(
            [n for _, n in self._consumers]))
        cg.add(self.consumer_row)
        self.stack.add_named(cg, "consumer")

        # special
        sg = Adw.PreferencesGroup(description="G-Shift: while held, the other buttons use their "
                                              "G-Shift layer assignments.")
        self._specials = list(onboard.SPECIALS.items())
        self.special_row = Adw.ComboRow(title="Function", model=Gtk.StringList.new(
            [n for _, n in self._specials]))
        sg.add(self.special_row)
        self.stack.add_named(sg, "special")

        # macro
        self.macro_group = Adw.PreferencesGroup(
            description="Macros are played by the background macro service; it is started "
                        "automatically when you assign one.")
        self._macros = list(win.macro_store.macros)
        if self._macros:
            self.macro_row = Adw.ComboRow(title="Macro", model=Gtk.StringList.new(
                [m.name for m in self._macros]))
            self.macro_group.add(self.macro_row)
        else:
            self.macro_row = None
            self.macro_group.add(Adw.ActionRow(title="No macros yet",
                                               subtitle="Create one on the Macros page first."))
        self.stack.add_named(self.macro_group, "macro")

        dg = Adw.PreferencesGroup()
        dg.add(Adw.ActionRow(title="This button will do nothing."))
        self.stack.add_named(dg, "disabled")

        self.cat_row.connect("notify::selected", self.on_cat)

        keyc = Gtk.EventControllerKey()
        keyc.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keyc.connect("key-pressed", self.on_key)
        self.add_controller(keyc)

        self._load(binding)

    def _load(self, b):
        """Pre-select the widgets to match the button's current binding."""
        kind = b.kind
        macro = None
        if kind == "key" and b.modifiers == 0:
            macro = self.win.macro_store.by_hid_usage(b.key_usage)
        cat = "macro" if macro else kind
        cats = [c for c, _ in CATEGORIES]
        self.cat_row.set_selected(cats.index(cat) if cat in cats else 0)
        self.on_cat(self.cat_row, None)
        if kind == "mouse" and 1 <= b.mouse_button <= 8:
            self.mouse_row.set_selected(b.mouse_button - 1)
        elif kind == "key" and not macro:
            name = keys.HID_TO_EVDEV.get(b.key_usage)
            if name in keys.BINDABLE_KEYS:
                self.key_row.set_selected(keys.BINDABLE_KEYS.index(name))
            for mask, cb in self.mod_checks.items():
                cb.set_active(bool(b.modifiers & (mask | mask << 4)))
        elif kind == "consumer":
            codes = [c for c, _ in self._consumers]
            if b.consumer_usage in codes:
                self.consumer_row.set_selected(codes.index(b.consumer_usage))
        elif kind == "special":
            codes = [c for c, _ in self._specials]
            if b.special_code in codes:
                self.special_row.set_selected(codes.index(b.special_code))
        elif macro and self.macro_row:
            self.macro_row.set_selected(self._macros.index(macro))

    def on_cat(self, row, _p):
        """Show the controls for the chosen action type."""
        self.stack.set_visible_child_name(CATEGORIES[row.get_selected()][0])

    def on_capture(self, _b):
        """Start capturing the next key combination typed."""
        self._capturing = True
        self.capture_btn.set_label("Press the key combination now…")
        self.capture_btn.add_css_class("suggested-action")

    def on_key(self, _ctl, _keyval, keycode, state):
        """While capturing: take the pressed key and modifiers (by hardware keycode, layout-independent)."""
        if not self._capturing:
            return False
        name = keys.evdev_name_from_hw_keycode(keycode)
        if name in keys.MODIFIERS or name is None:
            return True  # wait for the non-modifier key
        if name not in keys.BINDABLE_KEYS:
            self.capture_btn.set_label(f"{keys.pretty(name)} can't be stored on the mouse — try another")
            return True
        self.key_row.set_selected(keys.BINDABLE_KEYS.index(name))
        self.mod_checks[keys.MOD_LCTRL].set_active(bool(state & Gdk.ModifierType.CONTROL_MASK))
        self.mod_checks[keys.MOD_LSHIFT].set_active(bool(state & Gdk.ModifierType.SHIFT_MASK))
        self.mod_checks[keys.MOD_LALT].set_active(bool(state & Gdk.ModifierType.ALT_MASK))
        self.mod_checks[keys.MOD_LMETA].set_active(bool(state & Gdk.ModifierType.SUPER_MASK))
        self._capturing = False
        self.capture_btn.remove_css_class("suggested-action")
        self.capture_btn.set_label("Record a key combination…")
        return True

    def on_apply_clicked(self, _b):
        """Build the Binding/Macro from the widgets and hand it to on_apply."""
        cat = CATEGORIES[self.cat_row.get_selected()][0]
        macro = None
        if cat == "mouse":
            b = Binding.mouse(self.mouse_row.get_selected() + 1)
        elif cat == "key":
            name = keys.BINDABLE_KEYS[self.key_row.get_selected()]
            mods = sum(m for m, cb in self.mod_checks.items() if cb.get_active())
            b = Binding.key(keys.EVDEV_TO_HID[name], mods)
        elif cat == "consumer":
            b = Binding.consumer(self._consumers[self.consumer_row.get_selected()][0])
        elif cat == "special":
            b = Binding.special(self._specials[self.special_row.get_selected()][0])
        elif cat == "macro":
            if not self.macro_row:
                return
            macro = self._macros[self.macro_row.get_selected()]
            b = None
        else:
            b = Binding.disabled()
        self.close()
        self.on_apply(b, macro)


class AssignmentsPage:
    """Button assignments for both layers, with clickable photos of the mouse."""
    title = "Assignments"
    icon = "input-keyboard-symbolic"

    def __init__(self, win):
        self.win = win
        self.widget, box = page_box(max_width=1060)
        self.gshift = False
        self.views = []

        self.art_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START, spacing=18)
        self.columns, box = art_columns(box, self.art_box)
        self.responsive = [self.columns]

        layer_box = Gtk.Box(css_classes=["linked"], halign=Gtk.Align.CENTER)
        self.btn_default = Gtk.ToggleButton(label="Default layer", active=True)
        self.btn_gshift = Gtk.ToggleButton(label="G-Shift layer", group=self.btn_default)
        self.btn_default.connect("toggled", self.on_layer)
        layer_box.append(self.btn_default)
        layer_box.append(self.btn_gshift)
        box.append(layer_box)

        self.group = Adw.PreferencesGroup(title="Buttons")
        reset = Gtk.Button(label="Restore defaults", css_classes=["flat"], valign=Gtk.Align.CENTER)
        reset.connect("clicked", self.on_reset)
        self.group.set_header_suffix(reset)
        box.append(self.group)
        self.rows = []

        self.hint = Gtk.Label(wrap=True, xalign=0, css_classes=["dim-label"])
        box.append(self.hint)

    def on_layer(self, btn):
        """Switch between the default and G-Shift layer."""
        self.gshift = not self.btn_default.get_active()
        self.refresh()

    def _ensure_view(self):
        """Create the clickable photos (top + side) once we know which mouse this is."""
        if self.views:
            return
        self.views = make_views(self.win.info["name"], interactive=True)
        for v in self.views:
            v.on_activate = self.open_button
            v.on_hover = self._on_art_hover
            self.art_box.append(v)
        self.art_box.set_visible(bool(self.views))

    def _highlight(self, index):
        """Emphasise button `index` on every photo (None clears)."""
        for v in self.views:
            v.set_highlight(index)

    def _on_art_hover(self, index):
        """Highlight the list row of the button hovered on a photo."""
        for i, row in enumerate(self.rows):
            if i == index:
                row.add_css_class("o2-hover")
            else:
                row.remove_css_class("o2-hover")

    def refresh(self):
        """Rebuild rows and update the photos for the current profile and layer."""
        p = self.win.profile
        if not p:
            return
        self._ensure_view()
        clear_group(self.group, self.rows)
        labels = devices.button_labels(self.win.info["name"], p.button_count)
        lookup = self.win.macro_name_for_usage
        for i, (title, sub) in enumerate(labels):
            b = p.get_button(i, gshift=self.gshift)
            row = Adw.ActionRow(title=title, subtitle=sub, activatable=True)
            val = Gtk.Label(label=b.describe(lookup), ellipsize=3, max_width_chars=22,
                            css_classes=["dim-label"] if b.kind == "disabled" else [])
            row.add_suffix(val)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", self.on_row, i, title)
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", lambda *_a, i=i: self._highlight(i))
            motion.connect("leave", lambda *_a: self._highlight(None))
            row.add_controller(motion)
            self.group.add(row)
            self.rows.append(row)

        if self.gshift:
            self.group.set_title("Buttons — G-Shift layer")
            self.hint.set_label(
                "These assignments apply while a button set to “G-Shift” is held."
                if p.has_gshift_button() else
                "No button is set to “G-Shift” in this profile, so this layer is never used. "
                "Assign G-Shift (under DPI / Profile / Scroll) to a button to activate it.")
        else:
            self.group.set_title("Buttons")
            self.hint.set_label("Assignments are saved to the mouse's onboard memory and work "
                                "without this app — except macros, which need the background service. "
                                "Click a button on the picture or in the list to change it.")

    def open_button(self, index):
        """Open the assignment dialog for a button (from the picture)."""
        labels = devices.button_labels(self.win.info["name"], self.win.profile.button_count)
        self.on_row(None, index, labels[index][0])

    def on_row(self, _row, index, title):
        """Open the assignment dialog for button `index`."""
        p = self.win.profile
        current = p.get_button(index, gshift=self.gshift)
        layer = " (G-Shift)" if self.gshift else ""
        AssignDialog(self.win, f"{title}{layer}", current,
                     lambda b, m: self.apply(index, b, m)).present(self.win)

    def apply(self, index, binding, macro):
        """Store the chosen binding (allocating a trigger key for macros); confirms before removing the only Left Click."""
        p = self.win.profile
        if macro is not None:
            try:
                trigger = self.win.macro_store.ensure_trigger(macro)
            except RuntimeError as e:
                self.win.show_error(str(e))
                return
            binding = Binding.key(keys.EVDEV_TO_HID[trigger], 0)

        def commit():
            p.set_button(index, binding, gshift=self.gshift)
            self.win.after_buttons_changed(uses_macro=macro is not None)

        if index == 0 and not self.gshift and binding != Binding.mouse(1) and not self._has_left_click(p, binding, index):
            dlg = Adw.AlertDialog(
                heading="Remove left click?",
                body="No other button is set to Left Click. You'd have to use the keyboard "
                     "(or another mouse) to click until you change it back.")
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", "Reassign anyway")
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
            dlg.connect("response", lambda _d, r: commit() if r == "ok" else None)
            dlg.present(self.win)
        else:
            commit()

    @staticmethod
    def _has_left_click(p, new_binding, index):
        """True if some button other than `index` still sends Left Click."""
        return any(p.get_button(i) == Binding.mouse(1) for i in range(p.button_count) if i != index)

    def on_reset(self, _b):
        """Restore both button layers from the factory ROM profile."""
        p = self.win.profile
        rom = self.win.ob.rom_template
        for off in (onboard.OFF_BUTTONS, onboard.OFF_ALT_BUTTONS):
            p.data[off:off + 64] = rom[off:off + 64]
        self.win.after_buttons_changed()
        self.win.toast("Buttons restored to factory defaults")


# ---------------------------------------------------------------------------------------
# Lighting
# ---------------------------------------------------------------------------------------

class LightingPage:
    """LED effects per zone, with photos and an animated colour preview."""
    title = "Lighting"
    icon = "display-brightness-symbolic"

    def __init__(self, win):
        self.win = win
        self.widget, outer = page_box(max_width=1060)
        self.views = []
        self.art_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START, spacing=12)
        self.columns, self.box = art_columns(outer, self.art_box)
        self.responsive = [self.columns]
        # live preview: one glowing dot per LED zone, animated with the chosen effect
        self.preview_box = Gtk.Box(spacing=24, halign=Gtk.Align.CENTER, margin_top=6)
        self.art_box.append(self.preview_box)
        self.art_box.append(Gtk.Label(label="Live preview", css_classes=["dim-label", "caption"]))
        self.preview_dots = {}   # zone index -> (DrawingArea, [current rgba])

        sync_group = Adw.PreferencesGroup()
        self.sync_row = Adw.SwitchRow(title="Same effect on all zones", active=True)
        sync_group.add(self.sync_row)
        self.box.append(sync_group)

        self.zone_widgets = []
        self._building = False

    def _build_zones(self):
        """Create one control group per LED zone the device reports."""
        for zw in self.zone_widgets:
            self.box.remove(zw["group"])
        self.zone_widgets = []
        for z in self.win.info["led_zones"]:
            modes = [m for m in z["effects"] if m in onboard.LED_MODE_NAMES]
            g = Adw.PreferencesGroup(title=devices.zone_label(z["location"], z["index"]))
            mode_row = Adw.ComboRow(title="Effect", model=Gtk.StringList.new(
                [onboard.LED_MODE_NAMES[m] for m in modes]))
            g.add(mode_row)

            color_row = Adw.ActionRow(title="Color")
            color_btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False),
                                              valign=Gtk.Align.CENTER)
            color_row.add_suffix(color_btn)
            g.add(color_row)

            presets = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
            for rgb in [(255, 0, 0), (255, 110, 0), (255, 220, 0), (0, 255, 60),
                        (0, 200, 255), (0, 60, 255), (170, 0, 255), (255, 255, 255)]:
                sw = Gtk.Button(css_classes=["flat", "circular"], width_request=26, height_request=26)
                sw.set_child(self._swatch(rgb))
                sw.connect("clicked", self.on_preset, len(self.zone_widgets), rgb)
                presets.append(sw)
            preset_row = Adw.ActionRow(title="Presets")
            preset_row.add_suffix(presets)
            g.add(preset_row)

            speed_row = Adw.ActionRow(title="Speed", subtitle="Seconds per cycle")
            speed = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 20, 0.5)
            speed.set_size_request(220, -1)
            speed.set_draw_value(True)
            speed.set_inverted(False)
            speed_row.add_suffix(speed)
            g.add(speed_row)

            bright_row = Adw.ActionRow(title="Brightness")
            bright = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 100, 1)
            bright.set_size_request(220, -1)
            bright.set_draw_value(True)
            bright.set_format_value_func(lambda _s, v: f"{int(v)} %")
            bright_row.add_suffix(bright)
            g.add(bright_row)

            zw = {"zone": z["index"], "group": g, "modes": modes, "mode_row": mode_row,
                  "color_row": color_row, "color_btn": color_btn, "preset_row": preset_row,
                  "speed_row": speed_row, "speed": speed, "bright_row": bright_row, "bright": bright}
            i = len(self.zone_widgets)
            mode_row.connect("notify::selected", lambda *_a, i=i: self.on_change(i))
            color_btn.connect("notify::rgba", lambda *_a, i=i: self.on_change(i))
            speed.connect("value-changed", lambda *_a, i=i: self.on_change(i))
            bright.connect("value-changed", lambda *_a, i=i: self.on_change(i))
            self.box.append(g)
            self.zone_widgets.append(zw)

    @staticmethod
    def _swatch(rgb):
        """A small filled circle used on preset-colour buttons."""
        area = Gtk.DrawingArea(content_width=18, content_height=18)

        def draw(_a, cr, w, h):
            cr.arc(w / 2, h / 2, min(w, h) / 2, 0, 6.2832)
            cr.set_source_rgb(*(c / 255 for c in rgb))
            cr.fill()
        area.set_draw_func(draw)
        return area

    def animate(self, t):
        """Called ~20x/s by the window while this page is visible: update the preview dots."""
        for zone, rgba in self.win.led_snapshot(t).items():
            if zone in self.preview_dots:
                area, state = self.preview_dots[zone]
                if state[0] != rgba:
                    state[0] = rgba
                    area.queue_draw()

    def _build_preview(self):
        """One labelled, glowing dot per LED zone the device reports."""
        for z in self.win.info["led_zones"]:
            state = [(0, 0, 0, 0.0)]
            area = Gtk.DrawingArea(content_width=44, content_height=44, halign=Gtk.Align.CENTER)

            def draw(_a, cr, w, h, state=state):
                r, g, b, k = state[0]
                cx, cy = w / 2, h / 2
                cr.arc(cx, cy, 20, 0, 6.2832)          # unlit LED housing
                cr.set_source_rgb(0.13, 0.14, 0.16)
                cr.fill()
                for radius, alpha in ((20, 0.25), (15, 0.45), (10, 1.0)):   # glow, then core
                    cr.arc(cx, cy, radius, 0, 6.2832)
                    cr.set_source_rgba(r / 255, g / 255, b / 255, alpha * k)
                    cr.fill()
            area.set_draw_func(draw)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            col.append(area)
            col.append(Gtk.Label(label=devices.zone_label(z["location"], z["index"]).split(" (")[0],
                                 css_classes=["caption"]))
            self.preview_box.append(col)
            self.preview_dots[z["index"]] = (area, state)

    def refresh(self):
        """Load the zones' effects from the profile into the controls."""
        p = self.win.profile
        if not p:
            return
        if not self.views:
            self.views = make_views(self.win.info["name"], interactive=False)
            for v in reversed(self.views):   # photos above the preview dots
                self.art_box.prepend(v)
        if not self.preview_dots:
            self._build_preview()
        if len(self.zone_widgets) != len(self.win.info["led_zones"]):
            self._build_zones()
        self._building = True
        for zw in self.zone_widgets:
            eff = p.get_led(zw["zone"])
            if eff.mode in zw["modes"]:
                zw["mode_row"].set_selected(zw["modes"].index(eff.mode))
            rgba = Gdk.RGBA()
            rgba.red, rgba.green, rgba.blue, rgba.alpha = (*(c / 255 for c in eff.color), 1.0)
            zw["color_btn"].set_rgba(rgba)
            zw["speed"].set_value(eff.period / 1000)
            zw["bright"].set_value(eff.brightness)
            self._update_visibility(zw)
        self._building = False

    def _update_visibility(self, zw):
        """Show only the controls that apply to the selected effect."""
        mode = zw["modes"][zw["mode_row"].get_selected()] if zw["modes"] else onboard.LED_OFF
        has_color = mode in (onboard.LED_FIXED, onboard.LED_BREATHING)
        animated = mode in (onboard.LED_CYCLE, onboard.LED_BREATHING)
        zw["color_row"].set_visible(has_color)
        zw["preset_row"].set_visible(has_color)
        zw["speed_row"].set_visible(animated)
        zw["bright_row"].set_visible(animated)

    def _effect_from(self, zw):
        """Build an LedEffect from a zone's controls."""
        mode = zw["modes"][zw["mode_row"].get_selected()]
        c = zw["color_btn"].get_rgba()
        return LedEffect(mode=mode, color=(round(c.red * 255), round(c.green * 255), round(c.blue * 255)),
                         period=int(zw["speed"].get_value() * 1000),
                         brightness=int(zw["bright"].get_value()))

    def on_preset(self, _b, i, rgb):
        """Apply a preset colour to a zone's colour button."""
        rgba = Gdk.RGBA()
        rgba.red, rgba.green, rgba.blue, rgba.alpha = (*(c / 255 for c in rgb), 1.0)
        self.zone_widgets[i]["color_btn"].set_rgba(rgba)

    def on_change(self, i):
        """Any lighting control changed: update the profile (all zones if synced) and schedule a save."""
        if self._building:
            return
        zw = self.zone_widgets[i]
        self._update_visibility(zw)
        eff = self._effect_from(zw)
        p = self.win.profile
        targets = self.zone_widgets if self.sync_row.get_active() else [zw]
        for t in targets:
            if eff.mode in t["modes"]:
                p.set_led(t["zone"], eff)
        if self.sync_row.get_active() and len(targets) > 1:
            self.refresh()  # mirror the controls on the other zones
        self.win.schedule_save(refresh=False)


# ---------------------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------------------

class ProfilesPage:
    """The onboard profiles: enable, activate, rename, reset."""
    title = "Profiles"
    icon = "view-list-bullet-symbolic"

    def __init__(self, win):
        self.win = win
        self.widget, box = page_box()
        self.group = Adw.PreferencesGroup(
            title="Onboard Profiles",
            description="Stored on the mouse. The G9 button (or any button set to a profile "
                        "function) switches between enabled profiles — even on another computer.")
        box.append(self.group)
        self.rows = []

    def refresh(self):
        """Rebuild the profile rows."""
        ob = self.win.ob
        if not ob:
            return
        clear_group(self.group, self.rows)
        active = self.win.active_index
        for p in ob.profiles:
            row = Adw.ActionRow(title=p.display_name(), use_markup=False)
            if p.index == active:
                row.set_subtitle("Active on the mouse")
                row.add_prefix(Gtk.Image(icon_name="object-select-symbolic", css_classes=["accent"]))
            elif not p.enabled:
                row.set_subtitle("Disabled")
            else:
                row.set_subtitle(f"{sum(1 for v in p.dpis if v)} DPI stages")

            use = Gtk.Button(label="Activate", valign=Gtk.Align.CENTER,
                             sensitive=p.index != active)
            use.connect("clicked", lambda _b, i=p.index: self.win.activate_profile(i))
            row.add_suffix(use)

            ren = Gtk.Button(icon_name="document-edit-symbolic", css_classes=["flat"],
                             valign=Gtk.Align.CENTER, tooltip_text="Rename")
            ren.connect("clicked", self.on_rename, p)
            row.add_suffix(ren)

            rst = Gtk.Button(icon_name="edit-undo-symbolic", css_classes=["flat"],
                             valign=Gtk.Align.CENTER, tooltip_text="Reset to factory defaults")
            rst.connect("clicked", self.on_reset, p)
            row.add_suffix(rst)

            sw = Gtk.Switch(active=p.enabled, valign=Gtk.Align.CENTER, tooltip_text="Enabled")
            sw.connect("state-set", self.on_enable, p)
            row.add_suffix(sw)
            self.group.add(row)
            self.rows.append(row)

    def on_enable(self, _sw, state, p):
        """Enabled switch toggled."""
        self.win.set_profile_enabled(p.index, state)
        return False

    def on_rename(self, _b, p):
        """Ask for a new name and save it to the mouse."""
        dlg = Adw.AlertDialog(heading="Rename profile")
        entry = Gtk.Entry(text=p.name, placeholder_text=f"Profile {p.index}", activates_default=True)
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Rename")
        dlg.set_default_response("ok")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

        def resp(_d, r):
            if r == "ok":
                p.name = entry.get_text().strip()[:23]
                self.win.save_profile_now(p)
        dlg.connect("response", resp)
        dlg.present(self.win)

    def on_reset(self, _b, p):
        """Confirm, then reset the profile to factory settings."""
        dlg = Adw.AlertDialog(heading=f"Reset “{p.display_name()}”?",
                              body="DPI stages, buttons and lighting of this profile go back to "
                                   "Logitech's factory settings.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("reset", "Reset")
        dlg.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: self.win.reset_profile(p.index) if r == "reset" else None)
        dlg.present(self.win)


# ---------------------------------------------------------------------------------------
# Device / settings
# ---------------------------------------------------------------------------------------

class DevicePage:
    """Device information and background-service controls."""
    title = "Device"
    icon = "preferences-system-symbolic"

    def __init__(self, win):
        self.win = win
        self.widget, box = page_box()

        self.info_group = Adw.PreferencesGroup(title="Device")
        box.append(self.info_group)
        self.info_rows = {}
        for key, title in [("name", "Model"), ("battery", "Battery"), ("firmware", "Firmware"),
                           ("connection", "Connection"), ("memory", "Onboard memory")]:
            r = Adw.ActionRow(title=title, subtitle_selectable=True)
            r.add_css_class("property")
            self.info_group.add(r)
            self.info_rows[key] = r

        svc = Adw.PreferencesGroup(
            title="Background Service",
            description="Plays software macros, and keeps doing so with this window closed. "
                        "It is started automatically while you use macros; starting it at "
                        "login is up to you.")
        self.svc_row = Adw.SwitchRow(title="Macro service running")
        self.svc_row.connect("notify::active", self.on_svc)
        svc.add(self.svc_row)
        self.autostart_row = Adw.SwitchRow(
            title="Start at login",
            subtitle="Macros work right after you log in, without opening this app")
        self.autostart_row.connect("notify::active", self.on_autostart)
        svc.add(self.autostart_row)
        log_row = Adw.ActionRow(title="Service log", activatable=True)
        log_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        log_row.connect("activated", self.on_log)
        svc.add(log_row)
        box.append(svc)
        self._svc_updating = False

    def refresh(self):
        """Fill in model, battery, firmware, connection, memory and service state."""
        info = self.win.info
        if not info:
            return
        self.info_rows["name"].set_subtitle(info["name"])
        self.info_rows["firmware"].set_subtitle(", ".join(v for _t, v in info["firmware"]) or "—")
        self.info_rows["connection"].set_subtitle(info["connection"])
        d = self.win.ob.desc if self.win.ob else {}
        self.info_rows["memory"].set_subtitle(
            f"{d.get('profile_count', '?')} profiles · {d.get('button_count', '?')} buttons · "
            f"format {d.get('profile_format', '?')}")
        self.info_rows["battery"].set_subtitle(self.win.battery_text(long=True))
        self.refresh_service()

    def refresh_service(self):
        """Re-read service state from systemd into the two switches."""
        self._svc_updating = True
        active = service.is_active()
        self.svc_row.set_active(active)
        self.svc_row.set_subtitle("Running" if active else "Stopped")
        self.autostart_row.set_active(service.is_autostart())
        self._svc_updating = False

    def on_svc(self, row, _p):
        """'Macro service running' switch: start/stop for this session."""
        if self._svc_updating:
            return
        try:
            service.start() if row.get_active() else service.stop()
        except RuntimeError as e:
            self.win.show_error(f"Couldn't change the background service:\n\n{e}")
        GLib.timeout_add(600, lambda: (self.refresh_service(), False)[1])

    def on_autostart(self, row, _p):
        """'Start at login' switch: opt in/out of autostart."""
        if self._svc_updating:
            return
        try:
            service.set_autostart(row.get_active())
        except RuntimeError as e:
            self.win.show_error(f"Couldn't change the login setting:\n\n{e}")
        GLib.timeout_add(600, lambda: (self.refresh_service(), False)[1])

    def on_log(self, _r):
        """Show the last lines of the service journal."""
        dlg = Adw.AlertDialog(heading="Macro service log",
                              body=service.recent_log(20) or "(no log entries yet)")
        dlg.add_response("ok", "Close")
        dlg.present(self.win)


# ---------------------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------------------

class MainWindow(Adw.ApplicationWindow):
    """The application window; owns device state and coordinates the pages.

    State: `dev` (HidppDevice), `ob` (OnboardProfiles), `info` (static device
    facts), `profile` (the Profile being edited), `active_index`, `live_dpi_index`,
    `battery`. Pages read these and call back into schedule_save() and friends.
    """
    def __init__(self, app):
        super().__init__(application=app, title=appinfo.APP_NAME, default_width=1120, default_height=760)
        self.worker = Worker()
        self.macro_store = MacroStore()
        self.dev = None
        self.ob = None
        self.info = None
        self.profile = None
        self.active_index = None
        self.live_dpi_index = None
        self.battery = None
        self.offline = False
        self._save_source = None
        self._poll_count = 0
        self._loading = False

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        self.root_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.toast_overlay.set_child(self.root_stack)

        # loading / empty states
        spinner_page = Adw.StatusPage(title="Looking for your mouse…", child=Adw.Spinner(
            width_request=32, height_request=32, halign=Gtk.Align.CENTER))
        self.root_stack.add_named(self._wrap_toolbar(spinner_page), "loading")
        self.empty_page = Adw.StatusPage(icon_name="input-mouse-symbolic", title="No mouse found")
        retry = Gtk.Button(label="Try again", halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
        retry.connect("clicked", lambda *_: self.load_device())
        self.empty_page.set_child(retry)
        self.root_stack.add_named(self._wrap_toolbar(self.empty_page), "empty")

        # main UI
        self.pages = [SensitivityPage(self), AssignmentsPage(self), LightingPage(self),
                      MacrosPage(self, self.macro_store), ProfilesPage(self), DevicePage(self)]

        split = Adw.OverlaySplitView(min_sidebar_width=220, max_sidebar_width=260)
        self.split = split

        # sidebar
        sb_tv = Adw.ToolbarView()
        sb_header = Adw.HeaderBar(show_end_title_buttons=False)
        sb_header.set_title_widget(Adw.WindowTitle(title=appinfo.APP_NAME))
        sb_tv.add_top_bar(sb_header)
        sb_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sidebar_art = Gtk.Box(halign=Gtk.Align.CENTER, margin_top=6)
        self.sidebar_view = None
        sb_box.append(self.sidebar_art)
        self.dev_title = Gtk.Label(css_classes=["title-3"], wrap=True, xalign=0,
                                   margin_start=18, margin_end=12, margin_top=6)
        self.dev_sub = Gtk.Label(css_classes=["dim-label", "caption"], xalign=0, wrap=True,
                                 margin_start=18, margin_end=12, margin_bottom=12)
        sb_box.append(self.dev_title)
        sb_box.append(self.dev_sub)
        self.nav = Gtk.ListBox(css_classes=["navigation-sidebar"])
        for pg in self.pages:
            row_box = Gtk.Box(spacing=12, margin_top=4, margin_bottom=4)
            row_box.append(Gtk.Image(icon_name=pg.icon))
            row_box.append(Gtk.Label(label=pg.title, xalign=0))
            self.nav.append(row_box)
        self.nav.connect("row-selected", self.on_nav)
        sb_box.append(self.nav)
        sb_tv.set_content(sb_box)
        split.set_sidebar(sb_tv)

        # content
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.page_title = Adw.WindowTitle()
        header.set_title_widget(self.page_title)
        toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", tooltip_text="Sidebar", active=True)
        toggle.bind_property("active", split, "show-sidebar",
                             GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        header.pack_start(toggle)

        self.profile_dd = Gtk.DropDown(tooltip_text="Active onboard profile", valign=Gtk.Align.CENTER)
        self._profile_dd_indices = []
        self._profile_dd_handler = self.profile_dd.connect("notify::selected", self.on_profile_dd)
        header.pack_end(self.profile_dd)

        self.battery_btn = Gtk.Button(css_classes=["flat"], tooltip_text="Battery")
        bat_box = Gtk.Box(spacing=4)
        self.battery_icon = Gtk.Image(icon_name="battery-missing-symbolic")
        self.battery_label = Gtk.Label()
        bat_box.append(self.battery_icon)
        bat_box.append(self.battery_label)
        self.battery_btn.set_child(bat_box)
        self.battery_btn.connect("clicked", lambda *_: self.select_page(len(self.pages) - 1))
        header.pack_end(self.battery_btn)
        tv.add_top_bar(header)

        self.banner = Adw.Banner(button_label="Retry")
        self.banner.connect("button-clicked", lambda *_: self.load_device())
        tv.add_top_bar(self.banner)

        self.content_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        for i, pg in enumerate(self.pages):
            self.content_stack.add_named(pg.widget, str(i))
        tv.set_content(self.content_stack)
        split.set_content(tv)

        # Only one breakpoint applies at a time (the last added that matches), so
        # the smaller one repeats the medium one's setters.
        responsive = [b for pg in self.pages for b in getattr(pg, "responsive", [])]
        medium = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 1080sp"))
        small = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 760sp"))
        for b in responsive:  # stack artwork above the controls
            medium.add_setter(b, "orientation", Gtk.Orientation.VERTICAL)
            small.add_setter(b, "orientation", Gtk.Orientation.VERTICAL)
        small.add_setter(split, "collapsed", True)  # sidebar becomes an overlay
        self.add_breakpoint(medium)
        self.add_breakpoint(small)
        split.connect("notify::collapsed",
                      lambda sv, _p: sv.set_show_sidebar(not sv.get_collapsed()))
        self._t0 = time.monotonic()
        GLib.timeout_add(50, self._animate)

        self.root_stack.add_named(split, "main")

        self.connect("close-request", self.on_close)
        GLib.timeout_add_seconds(POLL_SECONDS, self.poll)
        self.nav.select_row(self.nav.get_row_at_index(0))
        self.load_device()

    def _animate(self):
        """Drive the LED preview on whichever visible page wants it."""
        page = self.current_page()
        if self.profile and self.get_mapped() and hasattr(page, "animate"):
            page.animate(time.monotonic() - self._t0)
        return True

    def led_snapshot(self, t=0.0):
        """{zone: (r, g, b, intensity)} for the edited profile's LEDs at time t."""
        if not self.profile or not self.info:
            return {}
        return {z["index"]: led_preview(self.profile.get_led(z["index"]), t)
                for z in self.info["led_zones"]}

    def _wrap_toolbar(self, child):
        """Put a status page under a plain header bar (loading / empty screens)."""
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        tv.set_content(child)
        return tv

    # -- navigation ----------------------------------------------------------------------

    def on_nav(self, _lb, row):
        """Sidebar selection changed: show that page."""
        if row is None:
            return
        i = row.get_index()
        self.content_stack.set_visible_child_name(str(i))
        self.page_title.set_title(self.pages[i].title)
        self.page_title.set_subtitle(self.profile.display_name() if self.profile else "")
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)
        self.pages[i].refresh()

    def select_page(self, i):
        """Programmatically select page `i` in the sidebar."""
        self.nav.select_row(self.nav.get_row_at_index(i))

    def current_page(self):
        """The page object currently shown."""
        row = self.nav.get_selected_row()
        return self.pages[row.get_index()] if row else None

    # -- device loading ----------------------------------------------------------------------

    def load_device(self):
        """Find the mouse and read everything from it on the worker; prefers the previous device, then a G502."""
        if self._loading:
            return
        self._loading = True
        if not self.ob:
            self.root_stack.set_visible_child_name("loading")
        prev_path = self.dev.path if self.dev else None

        def work():
            found = hidpp.find_devices()
            if not found:
                return None
            chosen = next((f for f in found if f["path"] == prev_path), None)
            if chosen is None:
                g502 = [f for f in found if "g502" in f["name"].lower()]
                chosen = (g502 or found)[0]
            path = chosen["path"]
            dev = hidpp.HidppDevice(path)
            name = dev.get_name()
            if not dev.has_feature(hidpp.F_ONBOARD_PROFILES):
                raise hidpp.HidppError(f"{name} has no onboard-profile support; it isn't supported yet.")
            ob = onboard.OnboardProfiles(dev)
            info = {
                "name": name,
                "firmware": dev.get_firmware() if dev.has_feature(hidpp.F_FW_INFO) else [],
                "dpi_range": dev.get_dpi_range() if dev.has_feature(hidpp.F_ADJUSTABLE_DPI)
                else {"min": 100, "max": 25600, "step": 50},
                "report_rates": dev.get_report_rates() if dev.has_feature(hidpp.F_REPORT_RATE)
                else [1000, 500, 250, 125],
                "led_zones": dev.get_led_zones() if dev.has_feature(hidpp.F_COLOR_LED_EFFECTS) else [],
                "connection": ("Wireless (LIGHTSPEED receiver)" if chosen["wireless"] else "USB cable")
                              + f" · {path}",
            }
            battery = dev.get_battery()
            active = ob.active_index()
            dpi_idx = dev.get_current_dpi_index()
            if prev_path and self.dev and self.dev is not dev:
                self.dev.close()
            return dev, ob, info, battery, active, dpi_idx

        self.worker.run(work, self._on_loaded, self._on_load_error)

    def _on_loaded(self, result):
        """Device data arrived: install it and show the main UI."""
        self._loading = False
        if result is None:
            self._show_empty()
            return
        self.dev, self.ob, self.info, self.battery, self.active_index, self.live_dpi_index = result
        self.offline = False
        self.banner.set_revealed(False)
        editing = self.profile.index if self.profile else None
        idx = editing if editing and self.ob.get(editing).enabled else self.active_index
        self.profile = self.ob.get(idx or 1)
        self.dev_title.set_label(self.info["name"])
        self.dev_sub.set_label("Onboard memory mode")
        art = mouse_art.art_for(self.info["name"])
        if art and self.sidebar_view is None:
            self.sidebar_view = mouse_art.MouseView(art, size=(150, 219))
            self.sidebar_art.append(self.sidebar_view)
        self.root_stack.set_visible_child_name("main")
        self.refresh_all()
        self.ensure_service_for_macros()

    def _on_load_error(self, e):
        """Loading failed: show the asleep banner, or an explanatory empty page."""
        self._loading = False
        if isinstance(e, hidpp.DeviceOffline) and self.ob:
            self._set_offline(True)
            return
        if isinstance(e, hidpp.DeviceOffline):
            self.empty_page.set_title("Mouse is asleep or switched off")
            self.empty_page.set_description("The receiver is plugged in but the G502 didn't answer. "
                                            "Move the mouse to wake it up, then try again.")
            self.root_stack.set_visible_child_name("empty")
            GLib.timeout_add_seconds(3, lambda: (self.load_device(), False)[1])
            return
        self.empty_page.set_title("Couldn't talk to the mouse")
        self.empty_page.set_description(str(e))
        self.root_stack.set_visible_child_name("empty")

    def _show_empty(self):
        """No device: explain why (permissions vs. not plugged in) and retry periodically."""
        bad = hidpp.unreadable_logitech_nodes()
        self.empty_page.set_title("No mouse found")
        if bad:
            self.empty_page.set_description(
                "A Logitech device is connected but this user can't open it:\n"
                + "\n".join(bad) + "\n\nRun ./install.sh (installs a udev rule), then replug the receiver.")
        else:
            self.empty_page.set_description("Plug in the LIGHTSPEED receiver or the USB cable. "
                                            "This screen refreshes automatically.")
        self.root_stack.set_visible_child_name("empty")
        GLib.timeout_add_seconds(3, lambda: (self.load_device() if self.root_stack.get_visible_child_name() == "empty" else None, False)[1])

    def _set_offline(self, offline):
        """Toggle the 'mouse asleep' banner; flushes pending saves when the mouse returns."""
        if offline == self.offline:
            return
        self.offline = offline
        if offline:
            self.banner.set_title("The mouse is asleep or out of range — changes will be saved when it wakes up.")
            self.banner.set_revealed(True)
            self.battery_icon.set_from_icon_name("battery-missing-symbolic")
            self.battery_label.set_label("")
        else:
            self.banner.set_revealed(False)
            if self._save_source is None and getattr(self, "_pending_save", None):
                self.flush_save()

    # -- refresh -------------------------------------------------------------------------------

    def ensure_service_for_macros(self):
        """If macros are assigned but the service isn't running, start it for this
        session (never enables autostart — that is opt-in)."""
        if not any(m.trigger_key for m in self.macro_store.macros):
            return
        if not service.available() or service.is_active():
            return
        try:
            service.start()
            self.toast("Macro service started")
        except RuntimeError as e:
            self.show_error(f"Your macros won't play: the background service couldn't start.\n\n{e}")
        self.pages[5].refresh_service()

    def refresh_all(self):
        """Refresh header and every page."""
        self._refresh_profile_dd()
        self._refresh_battery()
        for pg in self.pages:
            pg.refresh()
        page = self.current_page()
        if page:
            self.page_title.set_subtitle(self.profile.display_name() if self.profile else "")

    def _refresh_profile_dd(self):
        """Rebuild the header profile dropdown (enabled profiles, ● = active)."""
        self.profile_dd.handler_block(self._profile_dd_handler)
        enabled = [p for p in self.ob.profiles if p.enabled]
        self._profile_dd_indices = [p.index for p in enabled]
        self.profile_dd.set_model(Gtk.StringList.new(
            [p.display_name() + (" ●" if p.index == self.active_index else "") for p in enabled]))
        if self.profile and self.profile.index in self._profile_dd_indices:
            self.profile_dd.set_selected(self._profile_dd_indices.index(self.profile.index))
        self.profile_dd.handler_unblock(self._profile_dd_handler)

    def battery_text(self, long=False):
        """Battery as text, e.g. '45 % · charging · 3.79 V' (voltage only if long)."""
        b = self.battery
        if not b:
            return "Unknown"
        s = f"{b['percent']} %"
        if b.get("charging"):
            s += " · fully charged" if b.get("full") else " · charging"
        if long and b.get("voltage"):
            s += f" · {b['voltage'] / 1000:.2f} V"
        return s

    def _refresh_battery(self):
        """Update the header battery icon and label."""
        b = self.battery
        if not b:
            self.battery_btn.set_visible(False)
            return
        self.battery_btn.set_visible(True)
        level = min(100, max(0, round(b["percent"] / 10) * 10))
        icon = f"battery-level-{level}-charging-symbolic" if b["charging"] else f"battery-level-{level}-symbolic"
        if b.get("full"):
            icon = "battery-full-charged-symbolic"
        self.battery_icon.set_from_icon_name(icon)
        self.battery_label.set_label(f"{b['percent']}%")
        self.battery_btn.set_tooltip_text(f"Battery: {self.battery_text(long=True)}")

    def profile_is_live(self):
        """True if the edited profile is the one active on the mouse."""
        return self.profile is not None and self.profile.index == self.active_index

    def macro_name_for_usage(self, usage):
        """Macro name for a bound trigger key's HID usage (for button labels), or None."""
        m = self.macro_store.by_hid_usage(usage)
        return m.name if m else None

    # -- polling --------------------------------------------------------------------------------

    def poll(self):
        """Every few seconds: read active profile, DPI stage and (sometimes) battery; detect sleep/wake."""
        if not self.dev or self._loading:
            return True
        self._poll_count += 1
        want_battery = self._poll_count % BATTERY_EVERY == 1 or self.offline
        dev, ob = self.dev, self.ob

        def work():
            active = ob.active_index()
            dpi = dev.get_current_dpi_index()
            bat = dev.get_battery() if want_battery else None
            return active, dpi, bat

        def done(r):
            active, dpi, bat = r
            was_offline = self.offline
            self._set_offline(False)
            if bat:
                self.battery = bat
                self._refresh_battery()
            changed = active != self.active_index or dpi != self.live_dpi_index
            self.active_index, self.live_dpi_index = active, dpi
            if was_offline:
                self.load_device()  # the mouse may have been changed elsewhere
            elif changed:
                if self.profile and active and self.profile.index != active and self._save_source is None:
                    self.profile = self.ob.get(active)  # user pressed a profile button on the mouse
                    self.refresh_all()
                else:
                    self._refresh_profile_dd()
                    page = self.current_page()
                    if isinstance(page, (SensitivityPage, ProfilesPage)):
                        page.refresh()

        def err(e):
            if isinstance(e, hidpp.DeviceOffline):
                self._set_offline(True)
                if "disappeared" in str(e) or "read failed" in str(e):
                    self.load_device()  # receiver unplugged / hidraw node gone

        self.worker.run(work, done, err)
        return True

    # -- saving ------------------------------------------------------------------------------------

    def schedule_save(self, refresh=True):
        """Debounced write of the edited profile to the mouse."""
        if not self.profile:
            return
        self._pending_save = True
        if refresh:
            page = self.current_page()
            if page:
                page.refresh()
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(SAVE_DELAY_MS, self.flush_save)

    def flush_save(self):
        """Write the edited profile now (normally called by the debounce timer)."""
        self._save_source = None
        if not getattr(self, "_pending_save", False) or self.offline:
            return False
        self._pending_save = False
        p, ob = self.profile, self.ob
        data = p.sealed()

        def work():
            ob.save(p, data=data)
            return ob.active_index(), self.dev.get_current_dpi_index()

        def done(r):
            self.active_index, self.live_dpi_index = r
            self._refresh_profile_dd()

        def err(e):
            self._pending_save = True
            if isinstance(e, hidpp.DeviceOffline):
                self._set_offline(True)
            else:
                self.show_error(f"Couldn't save to the mouse:\n\n{e}")

        self.worker.run(work, done, err)
        return False

    def save_profile_now(self, p):
        """Write a specific profile immediately (rename, macro cleanup on other profiles)."""
        data = p.sealed()
        self.worker.run(lambda: self.ob.save(p, data=data),
                        lambda _r: self.refresh_all(),
                        lambda e: self.show_error(f"Couldn't save to the mouse:\n\n{e}"))

    def after_buttons_changed(self, uses_macro=False):
        """After any button edit: free unused trigger keys, save, and start the service for macros."""
        self.sync_macro_triggers()
        self.schedule_save()
        self.pages[3].refresh()
        if uses_macro:
            self.ensure_service_for_macros()

    def sync_macro_triggers(self):
        """Free trigger keys of macros no longer bound to any button in any profile."""
        if not self.ob:
            return
        bound = set()
        for p in self.ob.profiles:
            for gs in (False, True):
                for i in range(p.button_count):
                    b = p.get_button(i, gshift=gs)
                    if b.kind == "key" and b.modifiers == 0:
                        bound.add(b.key_usage)
        self.macro_store.release_unbound(bound)

    def on_macro_deleted(self, macro_trigger):
        """Buttons that pointed at a deleted macro go back to factory behaviour."""
        usage = keys.EVDEV_TO_HID.get(macro_trigger)
        if usage is None or not self.ob:
            return
        rom = self.ob.rom_template
        for p in self.ob.profiles:
            touched = False
            for gs, base in ((False, onboard.OFF_BUTTONS), (True, onboard.OFF_ALT_BUTTONS)):
                for i in range(p.button_count):
                    b = p.get_button(i, gshift=gs)
                    if b.kind == "key" and b.modifiers == 0 and b.key_usage == usage:
                        p.set_button(i, Binding(bytes(rom[base + 4 * i:base + 4 * i + 4])), gshift=gs)
                        touched = True
            if touched:
                if p is self.profile:
                    self.schedule_save()
                else:
                    self.save_profile_now(p)

    def on_macros_changed(self):
        """Macros were edited: refresh the button labels."""
        self.pages[1].refresh()

    # -- profile management ------------------------------------------------------------------------

    def on_profile_dd(self, dd, _p):
        """Header profile dropdown changed: activate that profile."""
        i = dd.get_selected()
        if i < len(self._profile_dd_indices):
            self.activate_profile(self._profile_dd_indices[i])

    def activate_profile(self, index):
        """Make profile `index` active on the mouse and edit it."""
        self.flush_save()
        ob = self.ob

        def work():
            ob.activate(index)
            return ob.active_index(), self.dev.get_current_dpi_index()

        def done(r):
            self.active_index, self.live_dpi_index = r
            self.profile = ob.get(index)
            self.refresh_all()
            self.toast(f"Switched to {self.profile.display_name()}")

        self.worker.run(work, done, lambda e: self.show_error(str(e)))

    def set_profile_enabled(self, index, enabled):
        """Enable/disable an onboard profile."""
        ob = self.ob

        def work():
            ob.set_enabled(index, enabled)
            return ob.active_index()

        def done(active):
            self.active_index = active
            if not self.profile.enabled:
                self.profile = ob.get(active)
            self.refresh_all()

        def err(e):
            self.show_error(str(e))
            self.refresh_all()

        self.worker.run(work, done, err)

    def reset_profile(self, index):
        """Reset a profile to factory settings."""
        ob = self.ob
        self.worker.run(lambda: ob.reset_to_factory(index),
                        lambda _p: (self.sync_macro_triggers(), self.refresh_all(),
                                    self.toast("Profile reset to factory defaults")),
                        lambda e: self.show_error(str(e)))

    # -- misc ------------------------------------------------------------------------------------------

    def on_close(self, _w):
        """Window closing: finish any pending save synchronously so no edit is lost."""
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = None
            if getattr(self, "_pending_save", False) and self.ob and not self.offline:
                # finish the last write synchronously so nothing is lost
                try:
                    self.ob.save(self.profile)
                except hidpp.HidppError:
                    pass
        return False

    def toast(self, text):
        """Show a short notification."""
        self.toast_overlay.add_toast(Adw.Toast(title=text, timeout=2))

    def show_error(self, text):
        """Show an error dialog."""
        dlg = Adw.AlertDialog(heading="Something went wrong", body=str(text))
        dlg.add_response("ok", "OK")
        dlg.present(self)


class O2HubApp(Adw.Application):
    """The GApplication: single instance, one main window."""

    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        """One-time setup: icon search path, default window icon, app CSS."""
        Adw.Application.do_startup(self)
        # Our icon lives in data/icons; make it findable without installing.
        display = Gdk.Display.get_default()
        Gtk.IconTheme.get_for_display(display).add_search_path(f"{appinfo.DATA_DIR}/icons")
        Gtk.Window.set_default_icon_name(APP_ID)
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        """Present the main window (creating it on first activation)."""
        win = self.props.active_window
        if not win:
            win = MainWindow(self)
        win.present()


def main():
    """Run the application; returns the exit status."""
    return O2HubApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
