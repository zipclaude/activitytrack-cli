# SPDX-License-Identifier: GPL-3.0-or-later
"""The entry point PyInstaller bundles for Windows: activitytrack.exe."""
import sys

from activitytrack_cli.main import main

sys.exit(main())
