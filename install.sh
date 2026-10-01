#!/usr/bin/env bash
# Fivo o2Hub installer.
#
#   ./install.sh                 interactive; asks about starting at login (default: no)
#   ./install.sh --autostart     also start the macro service at every login
#   ./install.sh --no-autostart  never start it at login
#   ./install.sh --notched-scroll  only whole wheel notches scroll (like Windows)
#   ./install.sh --smooth-scroll   keep Linux's high-resolution wheel (default)
#   ./install.sh --no-deps       skip installing system packages
#
# Safe to re-run: it updates everything in place.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP_ID="com.fivo.o2Hub"
APP_NAME="Fivo o2Hub"
RULES="70-fivo-o2hub.rules"

AUTOSTART=ask
NOTCHED=ask
INSTALL_DEPS=1
for arg in "$@"; do
    case "$arg" in
        --autostart) AUTOSTART=yes ;;
        --no-autostart) AUTOSTART=no ;;
        --notched-scroll) NOTCHED=yes ;;
        --smooth-scroll) NOTCHED=no ;;
        --no-deps) INSTALL_DEPS=0 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "Unknown option: $arg (see --help)"; exit 1 ;;
    esac
done

step() { echo; echo "== $*"; }

echo "$APP_NAME installer"

# --- 1. dependencies --------------------------------------------------------------
if [ "$INSTALL_DEPS" = 1 ]; then
    step "Installing dependencies"
    if command -v pacman >/dev/null; then
        sudo pacman -S --needed python python-gobject python-cairo gtk4 libadwaita librsvg python-evdev
    elif command -v apt-get >/dev/null; then
        sudo apt-get install -y python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-rsvg-2.0 python3-evdev
    elif command -v dnf >/dev/null; then
        sudo dnf install -y python3 python3-gobject python3-cairo gtk4 libadwaita librsvg2 python3-evdev
    else
        echo "Unknown package manager. Install: Python 3, PyGObject + pycairo, GTK 4, libadwaita (>= 1.6),"
        echo "librsvg (with GObject introspection) and python-evdev, then re-run with --no-deps."
    fi
fi

# --- 2. device permissions ---------------------------------------------------------
step "Installing udev rules (mouse access without root, virtual input for macros)"
sudo install -m 644 "$HERE/data/$RULES" "/etc/udev/rules.d/$RULES"
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=hidraw --subsystem-match=misc

NEED_RELOGIN=0

step "Scroll wheel"
CURRENT_NOTCHED=$(python3 "$HERE/scroll_quirk.py" --status)
if [ "$NOTCHED" = ask ]; then
    if [ -t 0 ]; then
        echo "Linux reads Logitech wheels in 1/8-notch steps, so moving the mouse fast can"
        echo "rock the wheel enough to scroll. Notched scrolling only counts whole wheel"
        echo "clicks, like on Windows (you lose smooth sub-notch scrolling)."
        if [ "$CURRENT_NOTCHED" = enabled ]; then prompt="[Y/n]"; else prompt="[y/N]"; fi
        read -r -p "Use notched scrolling? $prompt " reply
        case "${reply:-}" in
            [yY]*) NOTCHED=yes ;;
            [nN]*) NOTCHED=no ;;
            *) [ "$CURRENT_NOTCHED" = enabled ] && NOTCHED=yes || NOTCHED=no ;;
        esac
    else
        NOTCHED=keep
    fi
fi
if [ "$NOTCHED" = yes ] && [ "$CURRENT_NOTCHED" != enabled ]; then
    sudo python3 "$HERE/scroll_quirk.py" --enable
    NEED_RELOGIN=1
elif [ "$NOTCHED" = no ] && [ "$CURRENT_NOTCHED" = enabled ]; then
    sudo python3 "$HERE/scroll_quirk.py" --disable
    NEED_RELOGIN=1
else
    echo "Notched scrolling: $CURRENT_NOTCHED (change it any time under Sensitivity in the app)"
fi

if ! id -nG "$USER" | grep -qw input; then
    step "Adding $USER to the 'input' group (needed by the macro service)"
    sudo usermod -aG input "$USER"
    NEED_RELOGIN=1
fi

# --- 3. app menu entry + icon ----------------------------------------------------------
step "Adding $APP_NAME to your app menu"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$ICON_DIR" "$APPS_DIR"
install -m 644 "$HERE/data/icons/$APP_ID.svg" "$ICON_DIR/$APP_ID.svg"
cat > "$APPS_DIR/$APP_ID.desktop" <<EOF
[Desktop Entry]
Name=$APP_NAME
Comment=Configure Logitech gaming mice: DPI, buttons, lighting, macros
Exec=/usr/bin/env python3 $HERE/main.py
Icon=$APP_ID
Terminal=false
Type=Application
Categories=Settings;HardwareSettings;
Keywords=logitech;g502;mouse;dpi;macro;rgb;
StartupWMClass=$APP_ID
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS_DIR" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" || true

# --- 4. background macro service ---------------------------------------------------------
step "Setting up the background macro service"
svc() { python3 -c "import sys; sys.path.insert(0, '$HERE'); import service; $1"; }
svc "service.install()"   # writes the unit and removes units from older versions

CURRENT=$(svc "print('yes' if service.is_autostart() else 'no')")
if [ "$AUTOSTART" = ask ]; then
    if [ -t 0 ]; then
        echo "The macro service plays your software macros. The app starts it whenever"
        echo "you use macros; it can also start automatically when you log in."
        if [ "$CURRENT" = yes ]; then prompt="[Y/n]"; else prompt="[y/N]"; fi
        read -r -p "Start the macro service at login? $prompt " reply
        case "${reply:-}" in
            [yY]*) AUTOSTART=yes ;;
            [nN]*) AUTOSTART=no ;;
            *) AUTOSTART=$CURRENT ;;
        esac
    else
        AUTOSTART=$CURRENT   # non-interactive: keep whatever was chosen before
    fi
fi

if [ "$AUTOSTART" = yes ]; then
    svc "service.set_autostart(True)"
    echo "Macro service will start at login."
else
    svc "service.set_autostart(False)"
    echo "Macro service will NOT start at login (enable it any time under Device in the app)."
fi
svc "service.restart()"   # pick up new code if it was running

# --- done -------------------------------------------------------------------------------
echo
echo "Done. Start \"$APP_NAME\" from your app menu, or run:  python3 $HERE/main.py"
if [ "$NEED_RELOGIN" = 1 ]; then
    echo "Log out and back in once so the changes take effect (input group / scroll setting)."
fi
if systemctl is-active --quiet ratbagd 2>/dev/null; then
    echo "Note: ratbagd (Piper) is running. It's fine to keep, but don't edit the mouse in"
    echo "Piper and $APP_NAME at the same time."
fi
