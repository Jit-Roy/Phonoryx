# AudioFlow Virtual Audio Driver (WDK / PortCls WaveRT)

This directory contains the standalone WDM/KS virtual audio driver for AudioFlow, structured around PortCls (`portcls.sys`) WaveRT + Topology subdevices and an MDL-mapped shared ring buffer control interface (`GUID_DEVINTERFACE_AUDIOFLOW_CONTROL`) modeled after production low-latency virtual audio architectures (SYSVAD / PortCls).

## Architecture

- **`AudioFlowVirtualAudio.inf`**: `Class=MEDIA` driver package (`Root\AudioFlowVirtualAudio`) registering 4 virtual playback (`AudioFlow Virtual Output 1..4`) and 4 virtual capture (`AudioFlow Virtual Microphone 1..4`) endpoints visible in Windows Sound Settings, Chrome, OBS, Discord, and Audacity.
- **`include/audioflow_driver_ioctl.h`**: Shared kernel/user ABI header (`IOCTL_AUDIOFLOW_GET_INFO`, `IOCTL_AUDIOFLOW_MAP_ENDPOINT_RING`, `IOCTL_AUDIOFLOW_UNMAP_ENDPOINT_RING`, `IOCTL_AUDIOFLOW_SET_ENDPOINT_CONFIG`, `AUDIOFLOW_SHARED_RING`).
- **`src/driver.cpp`**: `DriverEntry`, `AddDevice`, `StartDevice`, subdevice registration, non-paged MDL ring allocation, user-mode `MmMapLockedPagesSpecifyCache` zero-copy mapping, and cleanup on handle/process exit.
- **`src/wavert_miniport.cpp`**: `IMiniportWaveRT` + `IMiniportWaveRTStreamNotification` supporting 16-bit PCM, 32-bit int PCM, and 32-bit float PCM at 44.1 kHz / 48 kHz (stereo), driven by a high-resolution `ExAllocateTimer` copy loop with extended floating-point state preservation (`KeSaveExtendedProcessorState`).
- **`src/topology_miniport.cpp`**: `IMiniportTopology` bridge pins (`KSNODETYPE_SPEAKER` and `KSNODETYPE_MICROPHONE`).

## Building (Requires Windows Driver Kit)

> Note: Building `.sys` kernel drivers requires **Windows Driver Kit (WDK) 10/11** matching the installed Windows SDK (`10.0.26100.0`). Install WDK (including `WindowsKernelModeDriver10.0` MSBuild targets and `portcls.h`).

```powershell
# From a VS Developer / EWDK Command Prompt:
msbuild driver\AudioFlowVirtualAudio.vcxproj /p:Configuration=Release /p:Platform=x64
```

## Installing & Testing (Test Signing Mode)

In an elevated Administrator command prompt:

```powershell
# 1. Enable test signing (reboot required once)
bcdedit /set testsigning on

# 2. Create a test certificate & sign the driver package (or use WDK's WDKTestCert)
inf2cat /driver:driver\x64\Release\AudioFlowVirtualAudio /os:10_VB_X64
signtool sign /fd SHA256 /s PrivateCertStore /n "AudioFlowTestCert" driver\x64\Release\AudioFlowVirtualAudio\AudioFlowVirtualAudio.sys

# 3. Install root-enumerated virtual device using devgen (Windows SDK / WDK) + pnputil
pnputil /add-driver driver\AudioFlowVirtualAudio.inf /install
devgen /add /bus ROOT /hardwareid Root\AudioFlowVirtualAudio
```

Once loaded, `af_driver_is_available()` in `audioflow_engine.dll` and the AudioFlow Studio UI automatically detect `GUID_DEVINTERFACE_AUDIOFLOW_CONTROL`, map the shared rings, and expose low-latency driver channels alongside standard Windows endpoints.

