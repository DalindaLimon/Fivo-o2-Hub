# Contributing to Fivo o2Hub

Thanks for helping! New mouse support, bug fixes, and UI polish are all
welcome.

## Getting started

```bash
git clone <this repo> && cd fivo-o2hub
./install.sh --no-autostart      # deps + udev rules (one time)
python3 main.py                  # run from the checkout; no build step
```

Then read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). It explains how the
pieces fit, the HID++ and onboard-memory formats, and the threading rules.
[docs/API.md](docs/API.md) documents every class and method.

## Ground rules

- **Document what you write.** Every module, class, method and function
  gets a docstring. After changing code, regenerate the reference:

  ```bash
  python3 tools/gen_api_docs.py --check   # must report 0 undocumented items
  python3 tools/gen_api_docs.py           # rewrites docs/API.md
  ```

- **Never break someone's mouse.** Changes to `onboard.py` must preserve
  bytes they don't understand, and keep CRCs valid. Test round trips on
  real hardware and say in the PR which device you tested.
- **Keep the macro engine's pump non-blocking.** Nothing slow (device
  scans, file I/O, sleeps) may run in `MacroEngine.run`'s loop. It freezes
  the cursor.
- **Talk to the mouse only from the worker thread** in the GUI
  (`MainWindow.worker.run(...)`).
- Match the surrounding style: plain Python 3, GTK 4 / libadwaita, no new
  dependencies without discussion.

## Checklist for a pull request

- [ ] `python3 tools/gen_api_docs.py --check` reports 0 items, and `docs/API.md` is regenerated
- [ ] `python3 test_macros.py` passes (if you touched macros or the engine)
- [ ] `python3 diagnostics.py` is clean on your machine
- [ ] UI changes: before and after screenshots (`tools/ui_snapshot.py`)
- [ ] Device-format changes: which mouse and firmware you tested on

## Reporting bugs

Please include the output of `python3 diagnostics.py`. For macro problems,
also include `journalctl --user -u fivo-o2hub -n 50`.
