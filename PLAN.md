# Project: AudioFlow — Node-Based Windows Audio Routing Studio

## 1. Project Overview

Build a Windows desktop application called **AudioFlow**, a professional, node-based audio routing and processing application.

The application should allow users to visually create audio connections between Windows applications, physical audio devices, virtual audio devices, and custom processing nodes.

The fundamental concept is a visual audio patchbay, similar to a node-based programming environment, but every connection represents a real-time audio stream.

The application should function independently without requiring OBS Studio, VB-CABLE, Voicemeeter, or other third-party audio-routing software.

The long-term objective is to build a complete custom audio-routing ecosystem, including our own virtual audio driver.

## 2. Core Requirements

The application must support:

1. Enumerating Windows audio input and output devices.
2. Capturing audio from individual Windows applications.
3. Capturing system-wide audio.
4. Capturing microphone and line-in audio.
5. Sending audio to physical output devices.
6. Sending audio to other applications through custom virtual audio devices.
7. Splitting one audio stream into multiple independent destinations.
8. Mixing multiple audio streams into one output.
9. Dynamic audio routing without restarting the application.
10. Visualizing audio levels and signal flow in real time.

## 3. Technology Stack

Use the following technologies:

### Programming language
- **Primary Application & UI Layer**: **Python 3.13+** (application orchestration, project persistence, UI state, graph editing, device/process management UI, bindings to the native engine).
- **Underlying Real-Time Audio Engine**: **C / C++20** native shared library (`audioflow_engine.dll` exposing a clean C ABI to Python via `ctypes`/`cffi` or extension bindings) — all real-time audio callbacks, WASAPI streams, DSP, mixing, splitting, and driver I/O run strictly in native C/C++ with zero Python/GIL involvement on real-time threads.

### Audio infrastructure
- Windows Core Audio APIs (called from the underlying C/C++ engine).
- WASAPI for audio capture and playback.
- MMDevice API for device enumeration.
- WASAPI Loopback Capture for system audio.
- WASAPI Process Loopback Capture for individual process audio.
- Windows Audio Processing Objects where appropriate.

### Virtual audio driver
- **C / C++** with the Windows Driver Kit (WDK).
- Microsoft SYSVAD sample as the starting reference.
- WDM/KS (PortCls / WaveRT) audio driver architecture.
- A custom virtual audio driver exposing capture and render endpoints.

The driver must eventually expose multiple independently selectable virtual audio devices.

### User interface
- **PyQt6 (Qt 6 for Python)**.
- `QGraphicsView` / `QGraphicsScene` (Graphics View Framework) or an equivalent PyQt6 node-editor implementation.
- Custom node-based canvas.
- Dark professional audio-studio interface.

Do not use a web-based frontend as the primary application interface.

## 4. Node-Based Editor

Create an interactive node canvas.

The canvas should support:

- Dragging nodes.
- Connecting node ports.
- Disconnecting connections.
- Moving and arranging nodes.
- Selecting multiple nodes.
- Deleting nodes.
- Zooming and panning.
- Creating groups.
- Saving and loading projects.
- Undo and redo.
- Connection validation.
- Visual indication of active audio flow.

Each node should have input and output ports.

Connections must represent actual audio streams, not merely visual relationships.

### Required node types

**A. Audio Source Nodes**

1. Application Audio Capture
2. System Audio Capture
3. Microphone Input
4. Physical Line Input
5. Virtual Device Input

Application Audio Capture should allow selecting a specific running process, such as Chrome, OBS, Discord, a game, or a media player.

Where possible, allow selecting a particular process instance rather than capturing every application with the same executable name.

**B. Audio Processing Nodes**

1. Volume Control
2. Gain
3. Audio Mixer
4. Audio Splitter
5. Mute
6. Audio Meter
7. Stereo-to-Mono
8. Mono-to-Stereo
9. Channel Mapping
10. Equalizer

The Audio Splitter must duplicate an incoming audio stream to multiple independent output connections.

Changing the gain of one branch must not affect other branches.

**C. Audio Output Nodes**

1. Physical Speaker Output
2. Headphone Output
3. Virtual Audio Output
4. Virtual Microphone Output
5. File Recording Output

Each output node must allow selecting its corresponding Windows audio endpoint.

## 5. Most Important Feature: Audio Routing

Implement a flexible audio-routing engine.

Example routing configuration:

Chrome Process A
→ Application Capture
→ Audio Splitter

Branch 1:
→ Volume Control
→ Headphones

Branch 2:
→ Equalizer
→ Virtual Microphone 1
→ Chrome Process B

Branch 3:
→ Audio Mixer
→ Virtual Microphone 2
→ Discord

All branches must work simultaneously.

The application must support one-to-many and many-to-one routing.

The user must be able to construct arbitrary valid routing graphs.

## 6. Custom Virtual Audio Driver

This is a major part of the project.

Develop a custom Windows virtual audio driver based on Microsoft's SYSVAD sample.

The driver must expose audio endpoints that are visible in Windows Sound Settings and in applications such as Chrome, OBS, Discord and Audacity.

Required functionality:

- Create virtual playback endpoints.
- Create virtual recording endpoints.
- Transfer PCM audio buffers between the driver and user-mode audio engine.
- Support multiple independently configurable virtual endpoints.
- Support common PCM formats, including 16-bit and 32-bit audio and floating-point PCM.
- Support common sample rates such as 44.1 kHz and 48 kHz.
- Handle audio buffering and synchronization.
- Handle device startup and shutdown.
- Prevent audio glitches and buffer underruns.
- Allow the user-mode engine to control routing dynamically.

Do not implement the virtual driver by pretending that an ordinary WASAPI stream is a new Windows device. A real driver or an appropriate supported virtual-device mechanism is required.

The driver must be developed and tested separately from the main application.

## 7. Audio Engine Architecture

Implement a dedicated audio engine independent of the UI.

The audio engine should contain:

- Device Manager
- Process Capture Manager
- WASAPI Capture Engine
- WASAPI Render Engine
- Audio Graph Manager
- Audio Buffer Manager
- Mixer
- Splitter
- DSP Processing Pipeline
- Virtual Device Communication Layer
- Audio Synchronization Manager

The engine must operate in real time.

### Real-time requirements

- Avoid dynamic memory allocation in real-time audio callbacks.
- Avoid blocking locks in real-time audio callbacks.
- Use preallocated audio buffers.
- Use thread-safe communication between UI and audio engine.
- Prevent buffer underruns and overruns.
- Handle different sample rates and channel configurations.
- Implement resampling where necessary.
- Avoid feedback loops.
- Detect invalid graph connections.
- Handle device disconnections gracefully.

Use a consistent internal audio format, preferably 32-bit floating-point PCM.

## 8. Audio Graph Management

The graph manager must maintain a directed graph.

Every node should contain:

- Unique ID
- Node type
- Input ports
- Output ports
- Configuration
- Audio format
- Processing state

Every connection should contain:

- Source node ID
- Source port ID
- Destination node ID
- Destination port ID

Implement cycle detection and prevent unintended feedback loops.

The graph must support live updates without stopping unrelated audio streams.

For graph changes, use safe graph-state swapping or another real-time-safe strategy.

## 9. Application Capture

Use WASAPI Process Loopback Capture where supported.

Allow users to:

- View running processes.
- Identify audio-producing processes.
- Select an application.
- Capture its audio independently.
- Monitor its audio level.
- Route it to multiple destinations.

Handle applications with multiple process instances.

Also provide a system loopback capture option for capturing the Windows output mix.

Clearly distinguish application capture from system-wide loopback capture.

## 10. User Interface Design

Design a modern, dark-themed professional audio workstation.

### Main layout

**Left sidebar**
- Available audio devices
- Running applications
- Source nodes
- Processing nodes
- Output nodes

**Center**
- Infinite node canvas
- Audio connections
- Zoom and pan
- Node selection and configuration

**Right sidebar**
- Selected node properties
- Device selection
- Audio format
- Volume and gain
- Channel configuration

**Bottom panel**
- Audio level meters
- Engine status
- Active connections
- Device status
- Audio errors and logs

### Visual design

Use a professional audio-engineering aesthetic.

- Dark background.
- Clearly distinguish source, processing and output nodes.
- Animated connection indicators when audio is flowing.
- Smooth node interactions.
- Color-coded audio signal paths.
- Clear input and output ports.
- Real-time audio level meters.

Do not sacrifice performance for visual effects.

## 11. Project Persistence

Allow users to save and load routing configurations.

Use JSON for project files.

Persist:

- Node IDs.
- Node types.
- Node positions.
- Connections.
- Device preferences.
- Node configurations.
- Audio processing parameters.

Handle unavailable devices when reopening projects.

Never silently connect a different device just because the original device is unavailable.

## 12. Development Phases

Develop the project incrementally.

### Phase 1: Windows Audio Device Discovery
- Enumerate capture devices.
- Enumerate render devices.
- Display device names and IDs.
- Detect device connection and disconnection.

### Phase 2: Audio Capture and Playback
- Capture microphone audio.
- Play audio through selected speakers.
- Implement system loopback.
- Implement process-specific loopback capture.
- Show live audio meters.

### Phase 3: Audio Routing Engine
- Implement audio graph.
- Implement splitter.
- Implement mixer.
- Implement gain and volume controls.
- Implement multiple simultaneous outputs.

### Phase 4: Node-Based Interface
- Build node editor.
- Add source, processing and output nodes.
- Connect nodes to real audio engine.
- Implement live graph updates.

### Phase 5: Custom Virtual Audio Driver
- Study SYSVAD.
- Build and install a basic virtual audio device.
- Verify that it appears in Windows.
- Implement playback-to-capture audio transfer.
- Support multiple virtual endpoints.

### Phase 6: Integration
- Connect virtual endpoints to the audio graph.
- Verify that Chrome and OBS can select them.
- Test simultaneous routing.
- Test multiple virtual microphones.

### Phase 7: Optimization
- Reduce latency.
- Improve synchronization.
- Handle device failures.
- Optimize CPU and memory usage.
- Add diagnostics and performance monitoring.

## 13. Testing Requirements

Test the following scenarios:

**Test 1: Audio Splitting**

Chrome A → Splitter → Headphones + Virtual Microphone 1.

Both destinations must receive the same audio simultaneously.

**Test 2: Independent Volume**

Chrome A → Splitter.

Branch 1 volume: 100%.

Branch 2 volume: 30%.

Changing Branch 2 must not affect Branch 1.

**Test 3: Multiple Sources**

Chrome A + Microphone → Mixer → Headphones.

**Test 4: Application-to-Application Routing**

Chrome A → Audio Engine → Virtual Microphone 1 → Chrome B.

Chrome B must recognize the virtual device in its microphone selection menu.

**Test 5: Multiple Virtual Devices**

Chrome A → Virtual Microphone 1.

OBS → Virtual Microphone 2.

Both devices must operate independently.

**Test 6: Dynamic Routing**

Connect and disconnect nodes while audio is playing.

Unrelated active routes must continue working.

## 14. Important Engineering Rules

- Do not build a fake visual-only node editor.
- Every connection must correspond to a functional audio route.
- Do not assume WASAPI alone can register new system-wide audio endpoints.
- Do not require OBS, VB-CABLE or Voicemeeter.
- Do not put audio processing inside the UI thread.
- Keep the driver and audio engine separate.
- Avoid unnecessary dependencies.
- Document all Windows API usage.
- Prioritize functional correctness and low latency over visual polish.

## 15. Expected Final Product

A standalone Windows audio-routing studio where users can visually connect any supported audio source to multiple destinations.

The application should ultimately be capable of replacing the basic routing functionality of OBS audio monitoring, VB-CABLE and Voicemeeter, while offering a more flexible node-based interface.

The user should be able to create, save, load and dynamically modify complex audio-routing graphs without manually configuring Windows audio settings every time.

