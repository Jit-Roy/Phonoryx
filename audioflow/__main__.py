"""Module entrypoint for `python -m audioflow`."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from audioflow.ui.main_window import AudioFlowMainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("AudioFlow")
    win = AudioFlowMainWindow()
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        if p.is_file():
            win.load_project_path(p)
    win.showNormal()
    win.raise_()
    win.activateWindow()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

