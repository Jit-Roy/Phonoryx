"""Phonoryx — Node-Based Windows Audio Routing Studio."""

from __future__ import annotations

import os
import sys

__version__ = "0.1.0"


def get_base_dir() -> str:
    """Resolve app root reliably across source execution and PyInstaller frozen bundles."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and os.path.exists(os.path.join(meipass, "assets")):
            return meipass
        exe_dir = os.path.dirname(sys.executable)
        if os.path.exists(os.path.join(exe_dir, "assets")):
            return exe_dir
        if meipass:
            return meipass
        return exe_dir
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
