"""
Names, IDs and paths in one place. Every other module takes its branding
from here, so renaming the project only means editing this file (plus
install.sh and the udev rules file name).
"""

import os

APP_NAME = "Fivo o2Hub"
APP_SLUG = "fivo-o2hub"              # file/unit/config-dir names
APP_ID = "com.fivo.o2Hub"            # GApplication / desktop-file ID
VERSION = "1.0.0"

CONFIG_DIR = os.path.expanduser(f"~/.config/{APP_SLUG}")
LEGACY_CONFIG_DIRS = []      # old config dirs to import macros.json from, if a rename happens

SERVICE_UNIT = f"{APP_SLUG}.service"
LEGACY_SERVICE_UNITS = []    # old unit names to stop and remove on install

# Names of the virtual input devices the macro engine creates.
MIRROR_PHYS = f"{APP_SLUG}/mirror"
OUTPUT_PHYS = f"{APP_SLUG}/output"
OUTPUT_NAME = f"{APP_NAME} Macro Output"

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
