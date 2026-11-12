"""Module entrypoint for `python -m phonoryx`."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from phonoryx import get_base_dir
from phonoryx.ui.main_window import PhonoryxMainWindow


def main() -> int:
    # Inform Windows Shell of explicit AppUserModelID so the taskbar binds Phonoryx's icon
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PhonoryxStudio.Phonoryx.1.0")
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("Phonoryx")
    app.setOrganizationName("PhonoryxStudio")

    ico_path = os.path.join(get_base_dir(), "assets", "icons", "app_icon.ico")
    if os.path.exists(ico_path):
        app.setWindowIcon(QIcon(ico_path))

    win = PhonoryxMainWindow()
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        if p.is_file():
            win.load_project_path(p)
    if win.isMaximized():
        win.showMaximized()
    else:
        win.show()
    win._apply_win32_taskbar_icon()
    win.raise_()
    win.activateWindow()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
