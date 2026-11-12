<div align="center">
  <img src="assets/icons/app_icon.png" alt="Phonoryx Logo" width="160" />
  <br />
  <img src="assets/icons/phonoryx_title.svg" alt="Phonoryx" width="260" />

  <br /><br />

  <p><strong>Visual node-based low-latency Windows audio routing studio &amp; virtual device router.</strong></p>

  <p>
    <img src="https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6?style=for-the-badge&logo=windows&logoColor=white" alt="Platform" />
    <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python" />
    <img src="https://img.shields.io/badge/UI-PySide6%20(Qt%206)-41CD52?style=for-the-badge&logo=qt&logoColor=white" alt="PySide6" />
    <img src="https://img.shields.io/badge/Engine-C%2B%2B20%20WASAPI-00599C?style=for-the-badge&logo=cplusplus&logoColor=white" alt="C++20 WASAPI" />
    <img src="https://img.shields.io/badge/Build-PyInstaller-1B1F27?style=for-the-badge" alt="PyInstaller" />
    <img src="https://img.shields.io/badge/License-MIT-F59E0B?style=for-the-badge" alt="MIT License" />
  </p>

  <br />

  ![Phonoryx Studio](assets/screenshot.png)
</div>

---

## Requirements

- **Operating System:** Windows 10 (build 20348+) or Windows 11 (x64)
  > Windows 10/11 WASAPI Process Loopback support is required for per-application audio capture.
- **Python:** Python `3.10` – `3.12` (when running from source or building the executable)
- **Visual C++ Redistributable:** Microsoft Visual C++ Redistributable (x64)
- **No CMake or C++ Compiler Required:** A precompiled native audio engine (`assets/bin/phonoryx_engine.dll`) is already bundled in this repository, so end users do **not** need CMake or Visual Studio Build Tools.
- **Optional Virtual Audio Cables:** [VB-Cable](https://vb-audio.com/Cable/) or similar virtual audio drivers if you want virtual cable input/output routing endpoints.

## Installing Requirements

Create a virtual environment and install the Python dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Running the App

Launch Phonoryx directly from source (no CMake build step needed):

```powershell
python main.py
```

Or run the package module directly:

```powershell
python -m phonoryx
```

## Building the Executable (PyInstaller)

A preconfigured [`Phonoryx.spec`](Phonoryx.spec) is included. It automatically bundles `assets/`, the precompiled `phonoryx_engine.dll`, and the application icon (`assets/icons/app_icon.ico`):

```powershell
pyinstaller Phonoryx.spec --noconfirm
```

The standalone bundle is written to `dist\Phonoryx\Phonoryx.exe`. End users can run it directly with zero CMake or Python setup.

## Cache Location

Phonoryx stores its workspace cache (`workspace_cache.json` — open tabs, node layouts, device/app selections, and dock state) at the same system `LocalAppData` path pattern as PyCam:

```text
%LOCALAPPDATA%\Phonoryx
```

(Typically `C:\Users\<YourUsername>\AppData\Local\Phonoryx\workspace_cache.json`.)

## License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for full details.
