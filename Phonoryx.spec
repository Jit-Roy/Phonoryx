# -*- mode: python ; coding: utf-8 -*-
"""Default PyInstaller specification (Phonoryx Full Edition)."""

import os
import shutil

# Forward directly to Phonoryx-Full.spec
spec_full = os.path.join(os.path.abspath(SPECPATH), "Phonoryx-Full.spec")
with open(spec_full, "r", encoding="utf-8") as f:
    code = f.read()
exec(compile(code, spec_full, "exec"))
