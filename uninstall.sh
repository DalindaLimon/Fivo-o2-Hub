#!/usr/bin/env bash
# Remove everything install.sh added. Your mouse keeps its settings (they live
# in its onboard memory). Pass --purge to also delete your saved macros.
set -uo pipefail

APP_ID="com.fivo.o2Hub"
SLUG="fivo-o2hub"

systemctl --user disable --now "$SLUG.service" 2>/dev/null
rm -f "$HOME/.config/systemd/user/$SLUG.service"
systemctl --user daemon-reload 2>/dev/null

rm -f "$HOME/.local/share/applications/$APP_ID.desktop"
rm -f "$HOME/.local/share/icons/hicolor/scalable/apps/$APP_ID.svg"

HERE="$(cd "$(dirname "$0")" && pwd)"
if [ "$(python3 "$HERE/scroll_quirk.py" --status 2>/dev/null)" = enabled ]; then
    sudo python3 "$HERE/scroll_quirk.py" --disable
fi

if [ -f "/etc/udev/rules.d/70-$SLUG.rules" ]; then
    sudo rm -f "/etc/udev/rules.d/70-$SLUG.rules"
    sudo udevadm control --reload-rules
fi

if [ "${1:-}" = "--purge" ]; then
    rm -rf "$HOME/.config/$SLUG"
    echo "Removed saved macros."
fi

echo "Fivo o2Hub uninstalled. (You were left in the 'input' group; remove with: sudo gpasswd -d \$USER input)"
