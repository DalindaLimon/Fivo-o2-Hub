"""
UI for creating/editing macros: the step list editor, key recorder, and
the Macros page that lists saved macros.
"""

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

import keys
import service
from macros import Macro, MacroStep, StepType, RepeatMode, REPEAT_MODE_LABELS


class MacroEditorDialog(Adw.Dialog):
    """Create or edit one macro: name, repeat mode, recorder and step list. Saves on 'Save' only."""
    def __init__(self, macro_store, macro=None, on_saved=None):
        super().__init__(title="Edit Macro" if macro else "New Macro",
                         content_width=560, content_height=680)
        self.macro_store = macro_store
        self.macro = macro or Macro()
        self.steps = [MacroStep(s.type, s.key, s.ms) for s in self.macro.steps]  # working copy
        self.on_saved = on_saved
        self._recording = False
        self._last_event_time = None

        toolbar_view = Adw.ToolbarView()
        self.set_child(toolbar_view)

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        toolbar_view.add_top_bar(header)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel_btn)

        save_btn = Gtk.Button(label="Save", css_classes=["suggested-action"])
        save_btn.connect("clicked", self.on_save)
        header.pack_end(save_btn)

        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        toolbar_view.set_content(scroller)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                          margin_top=12, margin_bottom=18, margin_start=12, margin_end=12)
        scroller.set_child(content)

        # -- name + repeat mode -------------------------------------------------
        info_group = Adw.PreferencesGroup()
        content.append(info_group)

        self.name_row = Adw.EntryRow(title="Macro name")
        self.name_row.set_text(self.macro.name)
        info_group.add(self.name_row)

        self.repeat_row = Adw.ComboRow(title="Repeat mode",
                                       subtitle="What happens while its button is pressed / held")
        self._repeat_values = list(RepeatMode)
        self.repeat_row.set_model(Gtk.StringList.new([REPEAT_MODE_LABELS[rm] for rm in self._repeat_values]))
        self.repeat_row.set_selected(self._repeat_values.index(self.macro.repeat_mode))
        info_group.add(self.repeat_row)

        # -- recorder -------------------------------------------------------------
        rec_group = Adw.PreferencesGroup(
            title="Record",
            description="Press Start, then type keys and/or click inside the box. Delays between "
                        "events are recorded as Wait steps.")
        content.append(rec_group)

        self.record_toggle = Gtk.ToggleButton(label="● Start Recording", halign=Gtk.Align.START,
                                              margin_bottom=6)
        self.record_toggle.connect("toggled", self.on_record_toggled)
        rec_group.add(self.record_toggle)

        self.delays_check = Gtk.CheckButton(label="Record delays", active=True, margin_bottom=6)
        rec_group.add(self.delays_check)

        self.record_frame = Gtk.Frame(focusable=True)
        rec_group.add(self.record_frame)
        self.record_label = Gtk.Label(label="Not recording", margin_top=28, margin_bottom=28,
                                      css_classes=["dim-label"])
        self.record_frame.set_child(self.record_label)

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        key_controller.connect("key-released", self.on_key_released)
        self.record_frame.add_controller(key_controller)

        click_gesture = Gtk.GestureClick()
        click_gesture.set_button(0)  # every button, not just primary
        click_gesture.connect("pressed", self.on_mouse_pressed)
        click_gesture.connect("released", self.on_mouse_released)
        self.record_frame.add_controller(click_gesture)

        # -- manual step add ----------------------------------------------------
        manual_group = Adw.PreferencesGroup(title="Add Step")
        content.append(manual_group)

        self.key_row = Adw.ComboRow(title="Key", enable_search=True,
                                    model=Gtk.StringList.new([keys.pretty(k) for k in keys.ALL_KEYS]))
        self.key_row.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        manual_group.add(self.key_row)

        btns = Gtk.Box(spacing=6, halign=Gtk.Align.END, margin_top=6)
        for label, st in [("Press", None), ("Key Down", StepType.KEY_DOWN), ("Key Up", StepType.KEY_UP)]:
            b = Gtk.Button(label=label)
            b.connect("clicked", self.on_add_key, st)
            btns.append(b)

        self.wait_spin = Gtk.SpinButton.new_with_range(1, 60000, 10)
        self.wait_spin.set_value(50)
        wait_btn = Gtk.Button(label="Add Wait (ms)")
        wait_btn.connect("clicked", lambda *_: self._append(MacroStep(StepType.WAIT, ms=int(self.wait_spin.get_value()))))
        btns.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        btns.append(self.wait_spin)
        btns.append(wait_btn)
        manual_group.add(btns)

        # -- step list --------------------------------------------------------------
        self.steps_group = Adw.PreferencesGroup(title="Sequence")
        clear_btn = Gtk.Button(label="Clear", css_classes=["flat"], valign=Gtk.Align.CENTER)
        clear_btn.connect("clicked", self.on_clear_steps)
        self.steps_group.set_header_suffix(clear_btn)
        content.append(self.steps_group)

        self.step_rows = []
        self.refresh_steps()

    # -- recording ---------------------------------------------------------------

    def on_record_toggled(self, toggle):
        """Start/stop recording into the step list."""
        self._recording = toggle.get_active()
        if self._recording:
            toggle.set_label("■ Stop Recording")
            self.record_label.set_text("Recording… type keys or click in here")
            self.record_frame.add_css_class("accent")
            self.record_frame.grab_focus()
            self._last_event_time = None
        else:
            toggle.set_label("● Start Recording")
            self.record_label.set_text("Not recording")
            self.record_frame.remove_css_class("accent")

    def _record(self, step_type, evdev_key):
        """Append a key step, preceded by a Wait step for the time since the last event."""
        now = time.monotonic()
        if self._last_event_time is not None and self.delays_check.get_active():
            elapsed_ms = min(int((now - self._last_event_time) * 1000), 5000)
            if elapsed_ms > 5:
                self.steps.append(MacroStep(StepType.WAIT, ms=elapsed_ms))
        self._last_event_time = now
        self._append(MacroStep(step_type, key=evdev_key))

    def on_key_pressed(self, _c, _keyval, keycode, _state):
        """Record a key-down (ignoring autorepeat)."""
        if not self._recording:
            return False
        name = keys.evdev_name_from_hw_keycode(keycode)
        if name:
            # Ignore autorepeat: a second Down without an Up in between.
            downs = [s for s in self.steps if s.key == name and s.type != StepType.WAIT]
            if not downs or downs[-1].type != StepType.KEY_DOWN:
                self._record(StepType.KEY_DOWN, name)
        return True  # consume so it doesn't trigger dialog shortcuts

    def on_key_released(self, _c, _keyval, keycode, _state):
        """Record a key-up."""
        if not self._recording:
            return False
        name = keys.evdev_name_from_hw_keycode(keycode)
        if name:
            self._record(StepType.KEY_UP, name)
        return True

    def on_mouse_pressed(self, gesture, _n, _x, _y):
        """Record a mouse-button down."""
        if not self._recording:
            return
        self.record_frame.grab_focus()
        evdev_key = keys.GDK_BUTTON_TO_EVDEV.get(gesture.get_current_button())
        if evdev_key:
            self._record(StepType.KEY_DOWN, evdev_key)

    def on_mouse_released(self, gesture, _n, _x, _y):
        """Record a mouse-button up."""
        if not self._recording:
            return
        evdev_key = keys.GDK_BUTTON_TO_EVDEV.get(gesture.get_current_button())
        if evdev_key:
            self._record(StepType.KEY_UP, evdev_key)

    # -- step list management -------------------------------------------------------

    def _append(self, step):
        """Add a step and refresh the list."""
        self.steps.append(step)
        self.refresh_steps()

    def on_add_key(self, _b, step_type):
        """Add the selected key as a full press (down, 20 ms, up) or a single down/up step."""
        idx = self.key_row.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION:
            return
        key_name = keys.ALL_KEYS[idx]
        if step_type is None:  # full press: down, short wait, up
            self.steps += [MacroStep(StepType.KEY_DOWN, key=key_name),
                           MacroStep(StepType.WAIT, ms=20),
                           MacroStep(StepType.KEY_UP, key=key_name)]
            self.refresh_steps()
        else:
            self._append(MacroStep(step_type, key=key_name))

    def on_clear_steps(self, _btn):
        """Remove all steps."""
        self.steps = []
        self.refresh_steps()

    def refresh_steps(self):
        """Rebuild the step rows (wait steps get an inline duration editor)."""
        for r in self.step_rows:
            self.steps_group.remove(r)
        self.step_rows = []

        if not self.steps:
            empty = Adw.ActionRow(title="No steps yet", subtitle="Record or add steps above.")
            self.steps_group.add(empty)
            self.step_rows.append(empty)
            return

        for i, step in enumerate(self.steps):
            row = Adw.ActionRow(title=f"{i + 1}. {step.label()}")
            if step.type == StepType.WAIT:
                spin = Gtk.SpinButton.new_with_range(1, 60000, 10)
                spin.set_value(step.ms)
                spin.set_valign(Gtk.Align.CENTER)
                spin.connect("value-changed", self.on_wait_changed, i, row)
                row.add_suffix(spin)

            for icon, tip, cb in [("go-up-symbolic", "Move up", lambda _b, i=i: self.on_move_step(i, -1)),
                                  ("go-down-symbolic", "Move down", lambda _b, i=i: self.on_move_step(i, 1)),
                                  ("user-trash-symbolic", "Remove", lambda _b, i=i: self.on_remove_step(i))]:
                b = Gtk.Button(icon_name=icon, tooltip_text=tip, css_classes=["flat"], valign=Gtk.Align.CENTER)
                b.connect("clicked", cb)
                row.add_suffix(b)

            self.steps_group.add(row)
            self.step_rows.append(row)

    def on_wait_changed(self, spin, index, row):
        """A wait step's duration was edited."""
        self.steps[index].ms = int(spin.get_value())
        row.set_title(f"{index + 1}. {self.steps[index].label()}")

    def on_move_step(self, index, direction):
        """Move a step up (-1) or down (+1)."""
        new_index = index + direction
        if 0 <= new_index < len(self.steps):
            self.steps[index], self.steps[new_index] = self.steps[new_index], self.steps[index]
            self.refresh_steps()

    def on_remove_step(self, index):
        """Delete a step."""
        del self.steps[index]
        self.refresh_steps()

    # -- save --------------------------------------------------------------------

    def on_save(self, _btn):
        # Close any key/button the sequence leaves held, so a looping macro
        # can't leave keys stuck down.
        """Save the macro, appending key-ups for anything left held so loops can't stick keys."""
        held = []
        for s in self.steps:
            if s.type == StepType.KEY_DOWN and s.key not in held:
                held.append(s.key)
            elif s.type == StepType.KEY_UP and s.key in held:
                held.remove(s.key)
        steps = list(self.steps) + [MacroStep(StepType.KEY_UP, key=k) for k in reversed(held)]

        self.macro.name = self.name_row.get_text().strip() or "Macro"
        self.macro.repeat_mode = self._repeat_values[self.repeat_row.get_selected()]
        self.macro.steps = steps
        self.macro_store.update(self.macro)
        if self.on_saved:
            self.on_saved(self.macro)
        self.close()


class MacrosPage:
    """List of macros plus the background-service status."""
    title = "Macros"
    icon = "media-record-symbolic"

    def __init__(self, window, macro_store):
        self.window = window
        self.macro_store = macro_store

        self.widget = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                      margin_top=24, margin_bottom=24, margin_start=12, margin_end=12)
        self.widget.set_child(Adw.Clamp(maximum_size=760, child=box))

        self.status_group = Adw.PreferencesGroup()
        self.status_row = Adw.ActionRow(title="Background macro service")
        self.status_btn = Gtk.Button(label="Start", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
        self.status_btn.connect("clicked", self.on_start_service)
        self.status_row.add_suffix(self.status_btn)
        self.status_group.add(self.status_row)
        box.append(self.status_group)

        self.group = Adw.PreferencesGroup(
            title="Macros",
            description="Create a macro here, then assign it to a button on the Assignments page. "
                        "Repeat and toggle modes are played by the background service.")
        new_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="New macro",
                             css_classes=["flat"], valign=Gtk.Align.CENTER)
        new_btn.connect("clicked", self.on_new)
        self.group.set_header_suffix(new_btn)
        box.append(self.group)
        self.rows = []
        self.refresh()

    def refresh(self):
        """Rebuild the macro rows and the service status."""
        for r in self.rows:
            self.group.remove(r)
        self.rows = []

        if not self.macro_store.macros:
            empty = Adw.ActionRow(title="No macros yet", subtitle="Use + to create one.")
            self.group.add(empty)
            self.rows.append(empty)

        for macro in self.macro_store.macros:
            subtitle = f"{REPEAT_MODE_LABELS[macro.repeat_mode]} · {len(macro.steps)} steps"
            subtitle += " · assigned" if macro.trigger_key else " · not assigned"
            row = Adw.ActionRow(title=GLib.markup_escape_text(macro.name), subtitle=subtitle)

            edit_btn = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Edit",
                                  css_classes=["flat"], valign=Gtk.Align.CENTER)
            edit_btn.connect("clicked", self.on_edit, macro)
            row.add_suffix(edit_btn)

            del_btn = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete",
                                 css_classes=["flat"], valign=Gtk.Align.CENTER)
            del_btn.connect("clicked", self.on_delete, macro)
            row.add_suffix(del_btn)

            self.group.add(row)
            self.rows.append(row)

        self.refresh_status()

    def refresh_status(self):
        """Update the service row text and Start button."""
        assigned = any(m.trigger_key for m in self.macro_store.macros)
        active = service.is_active()
        if active:
            self.status_row.set_subtitle("Running — macros play even with this window closed")
        elif assigned:
            self.status_row.set_subtitle("Stopped — assigned macros won't play until it's started")
        else:
            self.status_row.set_subtitle("Stopped — starts automatically when you assign a macro")
        self.status_btn.set_visible(not active)

    def on_start_service(self, _b):
        """Start the service for this session."""
        try:
            service.start()
        except RuntimeError as e:
            self.window.show_error(f"Couldn't start the background service:\n\n{e}")
        GLib.timeout_add(600, lambda: (self.refresh_status(), False)[1])

    def on_new(self, _btn):
        """Open the editor for a new macro."""
        MacroEditorDialog(self.macro_store, macro=None, on_saved=self.on_macro_saved).present(self.window)

    def on_edit(self, _btn, macro):
        """Open the editor for an existing macro."""
        MacroEditorDialog(self.macro_store, macro=macro, on_saved=self.on_macro_saved).present(self.window)

    def on_delete(self, _btn, macro):
        """Delete a macro (confirming first if a button uses it; those buttons revert to factory)."""
        def really_delete():
            trigger = macro.trigger_key
            self.macro_store.remove(macro.id)
            if trigger:
                self.window.on_macro_deleted(trigger)
            self.refresh()
            self.window.on_macros_changed()

        if not macro.trigger_key:
            really_delete()
            return
        dlg = Adw.AlertDialog(heading=f"Delete “{macro.name}”?",
                              body="It's assigned to a button; that button goes back to its "
                                   "factory function.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: really_delete() if r == "delete" else None)
        dlg.present(self.window)

    def on_macro_saved(self, _macro):
        """Editor saved: refresh lists and button labels."""
        self.refresh()
        self.window.on_macros_changed()
