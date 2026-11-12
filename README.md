# AudioFlow — Node-Based Windows Audio Routing Studio

AudioFlow is a standalone Windows audio routing and processing studio combining a **Python 3.13 + PySide6 (LGPL v3)** dark workstation UI (`audioflow/`), a **C++20 real-time Core Audio / WASAPI engine DLL** (`engine/`), and an optional **WDK PortCls WaveRT virtual audio driver** (`driver/`).

## Quick Start

1. **Build the C++20 Audio Engine** (requires Visual Studio Build Tools 2022 + CMake + Windows SDK):
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

## Virtual Audio Driver (`driver/`)

`driver/` contains the WDM/KS `PortCls` (`WaveRT` + `Topology`) virtual audio driver (`AudioFlowVirtualAudio.inf`, `AudioFlowVirtualAudio.vcxproj`, `driver/src/*.cpp`) exposing 4 virtual playback and 4 virtual capture endpoints with zero-copy MDL-mapped shared ring buffers. Compiling `.sys` requires installing the **Windows Driver Kit (WDK)**; see [driver/README.md](driver/README.md).

