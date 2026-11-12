# AudioFlow — Node-Based Windows Audio Routing Studio

AudioFlow is a standalone Windows audio routing and processing studio combining a **Python + PySide6 (LGPL v3)** dark workstation UI (`audioflow/`) and a **C++20 real-time Core Audio / WASAPI engine DLL** (`engine/`).

## Quick Start

1. **Build the C++20 Audio Engine** (requires Visual Studio 2022+ Build Tools + CMake + Windows SDK):
   ```powershell
   cmake -S engine -B build/engine
   cmake --build build/engine --config Release --parallel
   ```
   Outputs `build/engine/bin/Release/audioflow_engine.dll` and `build/engine/bin/Release/audioflow_tests.exe`.

2. **Install Python Dependencies & Run Tests**:
   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\build\engine\bin\Release\audioflow_tests.exe
   .\.venv\Scripts\python.exe -m pytest -v
   ```

3. **Launch AudioFlow Studio**:
   ```powershell
   .\.venv\Scripts\python.exe -m audioflow
   ```

## Virtual Microphone / Browser Routing (Open-Source Setup)

AudioFlow captures per-application audio (`WASAPI` Process Loopback), hardware microphones, and system outputs with zero extra drivers. If you want to expose processed AudioFlow audio as a **microphone in Chrome, Discord, Zoom, or OBS**, install the free, MIT-licensed open-source Windows virtual audio driver:

- **[VirtualDrivers/Virtual-Audio-Driver](https://github.com/VirtualDrivers/Virtual-Audio-Driver)** (MIT License + MS-PL components, pre-signed releases via SignPath)

Once installed:
1. Add a **Virtual Audio Output** / **Virtual Microphone Output** node in AudioFlow and select its virtual render endpoint.
2. In Chrome / Discord / OBS, choose the paired virtual microphone input device.

