#define INITGUID
#include "driver.h"

AF_ADAPTER_CONTEXT g_af_adapter = {};

static PDRIVER_DISPATCH g_portcls_create = nullptr;
static PDRIVER_DISPATCH g_portcls_close = nullptr;
static PDRIVER_DISPATCH g_portcls_cleanup = nullptr;
static PDRIVER_DISPATCH g_portcls_ioctl = nullptr;
static PDRIVER_UNLOAD   g_portcls_unload = nullptr;

static const WCHAR* kRenderWaveNames[AF_ACTIVE_ENDPOINTS] = {
    L"WaveRender0", L"WaveRender1", L"WaveRender2", L"WaveRender3"
};
static const WCHAR* kRenderTopoNames[AF_ACTIVE_ENDPOINTS] = {
    L"TopoRender0", L"TopoRender1", L"TopoRender2", L"TopoRender3"
};
static const WCHAR* kCaptureWaveNames[AF_ACTIVE_ENDPOINTS] = {
    L"WaveCapture0", L"WaveCapture1", L"WaveCapture2", L"WaveCapture3"
};
static const WCHAR* kCaptureTopoNames[AF_ACTIVE_ENDPOINTS] = {
    L"TopoCapture0", L"TopoCapture1", L"TopoCapture2", L"TopoCapture3"
};

static ULONG CalculateRingBufferBytes() {
    const ULONG header_size = (ULONG)FIELD_OFFSET(AUDIOFLOW_SHARED_RING, samples);
    const ULONG sample_bytes = AUDIOFLOW_RING_FRAMES * AUDIOFLOW_CHANNELS * sizeof(float);
    const ULONG total = header_size + sample_bytes;
    return (total + PAGE_SIZE - 1) & ~(PAGE_SIZE - 1);
}

static NTSTATUS InitEndpointSlot(AF_ENDPOINT_SLOT* slot) {
    KeInitializeSpinLock(&slot->lock);
    slot->sample_rate = 48000;
    slot->channels = AUDIOFLOW_CHANNELS;
    slot->enabled = TRUE;
    slot->ring_bytes = CalculateRingBufferBytes();

    slot->ring = (AUDIOFLOW_SHARED_RING*)ExAllocatePool2(
        POOL_FLAG_NON_PAGEABLE, slot->ring_bytes, AF_POOL_TAG);
    if (!slot->ring) {
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    RtlZeroMemory(slot->ring, slot->ring_bytes);
    slot->ring->frame_capacity = AUDIOFLOW_RING_FRAMES;
    slot->ring->channels = AUDIOFLOW_CHANNELS;
    slot->ring->sample_rate = 48000;
    slot->ring->active = 0;

    slot->mdl = IoAllocateMdl(slot->ring, slot->ring_bytes, FALSE, FALSE, nullptr);
    if (!slot->mdl) {
        ExFreePoolWithTag(slot->ring, AF_POOL_TAG);
        slot->ring = nullptr;
        return STATUS_INSUFFICIENT_RESOURCES;
    }
    MmBuildMdlForNonPagedPool(slot->mdl);
    slot->user_va = nullptr;
    slot->owner_process = nullptr;
    return STATUS_SUCCESS;
}

static VOID FreeEndpointSlot(AF_ENDPOINT_SLOT* slot) {
    KIRQL irql;
    KeAcquireSpinLock(&slot->lock, &irql);
    PMDL mdl = slot->mdl;
    PVOID user_va = slot->user_va;
    AUDIOFLOW_SHARED_RING* ring = slot->ring;
    slot->user_va = nullptr;
    slot->owner_process = nullptr;
    slot->mdl = nullptr;
    slot->ring = nullptr;
    KeReleaseSpinLock(&slot->lock, irql);

    if (user_va && mdl) {
        MmUnmapLockedPages(user_va, mdl);
    }
    if (mdl) {
        IoFreeMdl(mdl);
    }
    if (ring) {
        ExFreePoolWithTag(ring, AF_POOL_TAG);
    }
}

static AF_ENDPOINT_SLOT* GetSlot(ULONG endpoint_index, BOOLEAN is_capture) {
    if (endpoint_index >= AF_ACTIVE_ENDPOINTS) {
        return nullptr;
    }
    return is_capture ? &g_af_adapter.capture_slots[endpoint_index]
                      : &g_af_adapter.render_slots[endpoint_index];
}

static VOID UnmapAllOwnedByProcess(PEPROCESS proc) {
    for (ULONG i = 0; i < AF_ACTIVE_ENDPOINTS; ++i) {
        AF_ENDPOINT_SLOT* slots[2] = {
            &g_af_adapter.render_slots[i],
            &g_af_adapter.capture_slots[i]
        };
        for (ULONG s = 0; s < 2; ++s) {
            AF_ENDPOINT_SLOT* slot = slots[s];
            if (!slot->mdl) continue;
            if (slot->user_va && slot->owner_process == proc) {
                MmUnmapLockedPages(slot->user_va, slot->mdl);
                slot->user_va = nullptr;
                slot->owner_process = nullptr;
                if (slot->ring) {
                    slot->ring->active = 0;
                }
            }
        }
    }
}

static NTSTATUS HandleAudioFlowIoctl(PIRP Irp, PIO_STACK_LOCATION irp_sp) {
    ULONG code = irp_sp->Parameters.DeviceIoControl.IoControlCode;
    ULONG in_len = irp_sp->Parameters.DeviceIoControl.InputBufferLength;
    ULONG out_len = irp_sp->Parameters.DeviceIoControl.OutputBufferLength;
    PVOID sys_buf = Irp->AssociatedIrp.SystemBuffer;

    switch (code) {
    case IOCTL_AUDIOFLOW_GET_INFO: {
        if (out_len < sizeof(AUDIOFLOW_DRIVER_INFO) || !sys_buf) {
            Irp->IoStatus.Information = 0;
            return STATUS_BUFFER_TOO_SMALL;
        }
        auto* info = (AUDIOFLOW_DRIVER_INFO*)sys_buf;
        info->version = 0x00010000;
        info->endpoint_count = AF_ACTIVE_ENDPOINTS;
        info->sample_rate = 48000;
        info->channels = AUDIOFLOW_CHANNELS;
        info->ring_frames = AUDIOFLOW_RING_FRAMES;
        Irp->IoStatus.Information = sizeof(AUDIOFLOW_DRIVER_INFO);
        return STATUS_SUCCESS;
    }

    case IOCTL_AUDIOFLOW_MAP_ENDPOINT_RING: {
        if (in_len < sizeof(AUDIOFLOW_MAP_REQUEST) ||
            out_len < sizeof(AUDIOFLOW_MAP_RESPONSE) || !sys_buf) {
            Irp->IoStatus.Information = 0;
            return STATUS_BUFFER_TOO_SMALL;
        }
        AUDIOFLOW_MAP_REQUEST req = *(AUDIOFLOW_MAP_REQUEST*)sys_buf;
        AF_ENDPOINT_SLOT* slot = GetSlot(req.endpoint_index, req.is_capture != 0);
        if (!slot || !slot->mdl) {
            Irp->IoStatus.Information = 0;
            return STATUS_INVALID_PARAMETER;
        }
        PEPROCESS current_proc = PsGetCurrentProcess();
        if (slot->user_va && slot->owner_process == current_proc) {
            auto* resp = (AUDIOFLOW_MAP_RESPONSE*)sys_buf;
            resp->user_address = (uint64_t)(ULONG_PTR)slot->user_va;
            resp->byte_size = slot->ring_bytes;
            resp->endpoint_index = req.endpoint_index;
            Irp->IoStatus.Information = sizeof(AUDIOFLOW_MAP_RESPONSE);
            return STATUS_SUCCESS;
        }
        if (slot->user_va != nullptr) {
            Irp->IoStatus.Information = 0;
            return STATUS_DEVICE_BUSY;
        }

        PVOID mapped = nullptr;
        __try {
            mapped = MmMapLockedPagesSpecifyCache(
                slot->mdl,
                UserMode,
                MmCached,
                nullptr,
                FALSE,
                NormalPagePriority);
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            mapped = nullptr;
        }
        if (!mapped) {
            Irp->IoStatus.Information = 0;
            return STATUS_INSUFFICIENT_RESOURCES;
        }
        slot->user_va = mapped;
        slot->owner_process = current_proc;
        if (slot->ring) {
            slot->ring->active = 1;
        }

        auto* resp = (AUDIOFLOW_MAP_RESPONSE*)sys_buf;
        resp->user_address = (uint64_t)(ULONG_PTR)mapped;
        resp->byte_size = slot->ring_bytes;
        resp->endpoint_index = req.endpoint_index;
        Irp->IoStatus.Information = sizeof(AUDIOFLOW_MAP_RESPONSE);
        return STATUS_SUCCESS;
    }

    case IOCTL_AUDIOFLOW_UNMAP_ENDPOINT_RING: {
        if (in_len < sizeof(AUDIOFLOW_MAP_REQUEST) || !sys_buf) {
            Irp->IoStatus.Information = 0;
            return STATUS_INVALID_PARAMETER;
        }
        AUDIOFLOW_MAP_REQUEST req = *(AUDIOFLOW_MAP_REQUEST*)sys_buf;
        AF_ENDPOINT_SLOT* slot = GetSlot(req.endpoint_index, req.is_capture != 0);
        if (!slot || !slot->mdl) {
            Irp->IoStatus.Information = 0;
            return STATUS_INVALID_PARAMETER;
        }
        if (slot->user_va && slot->owner_process == PsGetCurrentProcess()) {
            MmUnmapLockedPages(slot->user_va, slot->mdl);
            slot->user_va = nullptr;
            slot->owner_process = nullptr;
            if (slot->ring) {
                slot->ring->active = 0;
            }
        }
        Irp->IoStatus.Information = 0;
        return STATUS_SUCCESS;
    }

    case IOCTL_AUDIOFLOW_SET_ENDPOINT_CONFIG: {
        if (in_len < sizeof(AUDIOFLOW_ENDPOINT_CONFIG) || !sys_buf) {
            Irp->IoStatus.Information = 0;
            return STATUS_INVALID_PARAMETER;
        }
        auto* cfg = (AUDIOFLOW_ENDPOINT_CONFIG*)sys_buf;
        AF_ENDPOINT_SLOT* slot = GetSlot(cfg->endpoint_index, cfg->is_capture != 0);
        if (!slot) {
            Irp->IoStatus.Information = 0;
            return STATUS_INVALID_PARAMETER;
        }
        KIRQL irql;
        KeAcquireSpinLock(&slot->lock, &irql);
        slot->enabled = (cfg->enabled != 0);
        if (cfg->sample_rate == 44100 || cfg->sample_rate == 48000) {
            slot->sample_rate = cfg->sample_rate;
            if (slot->ring) {
                slot->ring->sample_rate = cfg->sample_rate;
            }
        }
        if (cfg->channels >= 1 && cfg->channels <= AUDIOFLOW_CHANNELS) {
            slot->channels = cfg->channels;
            if (slot->ring) {
                slot->ring->channels = cfg->channels;
            }
        }
        KeReleaseSpinLock(&slot->lock, irql);
        Irp->IoStatus.Information = 0;
        return STATUS_SUCCESS;
    }

    default:
        return STATUS_NOT_SUPPORTED;
    }
}

static NTSTATUS AfDispatchCreate(PDEVICE_OBJECT DeviceObject, PIRP Irp) {
    return g_portcls_create ? g_portcls_create(DeviceObject, Irp)
                            : PcDispatchIrp(DeviceObject, Irp);
}

static NTSTATUS AfDispatchCleanup(PDEVICE_OBJECT DeviceObject, PIRP Irp) {
    UnmapAllOwnedByProcess(PsGetCurrentProcess());
    return g_portcls_cleanup ? g_portcls_cleanup(DeviceObject, Irp)
                             : PcDispatchIrp(DeviceObject, Irp);
}

static NTSTATUS AfDispatchClose(PDEVICE_OBJECT DeviceObject, PIRP Irp) {
    UnmapAllOwnedByProcess(PsGetCurrentProcess());
    return g_portcls_close ? g_portcls_close(DeviceObject, Irp)
                           : PcDispatchIrp(DeviceObject, Irp);
}

static NTSTATUS AfDispatchDeviceControl(PDEVICE_OBJECT DeviceObject, PIRP Irp) {
    PIO_STACK_LOCATION irp_sp = IoGetCurrentIrpStackLocation(Irp);
    ULONG code = irp_sp->Parameters.DeviceIoControl.IoControlCode;
    if (DEVICE_TYPE_FROM_CTL_CODE(code) == FILE_DEVICE_AUDIOFLOW) {
        NTSTATUS st = HandleAudioFlowIoctl(Irp, irp_sp);
        if (st != STATUS_NOT_SUPPORTED) {
            Irp->IoStatus.Status = st;
            IoCompleteRequest(Irp, IO_NO_INCREMENT);
            return st;
        }
    }
    return g_portcls_ioctl ? g_portcls_ioctl(DeviceObject, Irp)
                           : PcDispatchIrp(DeviceObject, Irp);
}

static VOID AfDriverUnload(PDRIVER_OBJECT DriverObject) {
    if (g_af_adapter.symlink_created && g_af_adapter.control_symlink.Buffer) {
        IoSetDeviceInterfaceState(&g_af_adapter.control_symlink, FALSE);
        RtlFreeUnicodeString(&g_af_adapter.control_symlink);
        g_af_adapter.symlink_created = FALSE;
    }
    for (ULONG i = 0; i < AF_ACTIVE_ENDPOINTS; ++i) {
        FreeEndpointSlot(&g_af_adapter.render_slots[i]);
        FreeEndpointSlot(&g_af_adapter.capture_slots[i]);
    }
    if (g_portcls_unload) {
        g_portcls_unload(DriverObject);
    }
}

static NTSTATUS RegisterEndpointPair(
    PDEVICE_OBJECT DeviceObject,
    ULONG Index,
    BOOLEAN IsCapture,
    const WCHAR* WaveName,
    const WCHAR* TopoName)
{
    PUNKNOWN unk_wave = nullptr;
    PUNKNOWN unk_topo = nullptr;

    NTSTATUS st = AfCreateWaveRtMiniport(&unk_wave, Index, IsCapture, NonPagedPoolNx);
    if (!NT_SUCCESS(st)) {
        return st;
    }
    st = AfCreateTopologyMiniport(&unk_topo, Index, IsCapture, NonPagedPoolNx);
    if (!NT_SUCCESS(st)) {
        unk_wave->Release();
        return st;
    }

    PPORT port_wave = nullptr;
    PPORT port_topo = nullptr;
    st = PcNewPort(&port_wave, CLSID_PortWaveRT);
    if (NT_SUCCESS(st)) {
        st = port_wave->Init(DeviceObject, nullptr, unk_wave, nullptr, nullptr);
    }
    if (NT_SUCCESS(st)) {
        st = PcRegisterSubdevice(DeviceObject, WaveName, port_wave);
    }
    if (NT_SUCCESS(st)) {
        st = PcNewPort(&port_topo, CLSID_PortTopology);
    }
    if (NT_SUCCESS(st)) {
        st = port_topo->Init(DeviceObject, nullptr, unk_topo, nullptr, nullptr);
    }
    if (NT_SUCCESS(st)) {
        st = PcRegisterSubdevice(DeviceObject, TopoName, port_topo);
    }

    if (NT_SUCCESS(st)) {
        // Connect WaveRT bridge pin 1 <-> Topology bridge pin 0/1
        if (IsCapture) {
            PcRegisterPhysicalConnection(DeviceObject, port_topo, 1, port_wave, 1);
        } else {
            PcRegisterPhysicalConnection(DeviceObject, port_wave, 1, port_topo, 0);
        }
    }

    if (port_wave) port_wave->Release();
    if (port_topo) port_topo->Release();
    unk_wave->Release();
    unk_topo->Release();
    return st;
}

extern "C" NTSTATUS StartDevice(
    _In_ PDEVICE_OBJECT DeviceObject,
    _In_ PIRP Irp,
    _In_ PRESOURCELIST ResourceList)
{
    UNREFERENCED_PARAMETER(Irp);
    UNREFERENCED_PARAMETER(ResourceList);

    for (ULONG i = 0; i < AF_ACTIVE_ENDPOINTS; ++i) {
        NTSTATUS st = RegisterEndpointPair(
            DeviceObject, i, FALSE, kRenderWaveNames[i], kRenderTopoNames[i]);
        if (!NT_SUCCESS(st)) return st;

        st = RegisterEndpointPair(
            DeviceObject, i, TRUE, kCaptureWaveNames[i], kCaptureTopoNames[i]);
        if (!NT_SUCCESS(st)) return st;
    }

    if (g_af_adapter.pdo && !g_af_adapter.symlink_created) {
        NTSTATUS st = IoRegisterDeviceInterface(
            g_af_adapter.pdo,
            &GUID_DEVINTERFACE_AUDIOFLOW_CONTROL,
            nullptr,
            &g_af_adapter.control_symlink);
        if (NT_SUCCESS(st)) {
            g_af_adapter.symlink_created = TRUE;
            IoSetDeviceInterfaceState(&g_af_adapter.control_symlink, TRUE);
        }
    }

    return STATUS_SUCCESS;
}

extern "C" NTSTATUS AddDevice(
    _In_ PDRIVER_OBJECT DriverObject,
    _In_ PDEVICE_OBJECT PhysicalDeviceObject)
{
    g_af_adapter.pdo = PhysicalDeviceObject;
    return PcAddAdapterDevice(
        DriverObject,
        PhysicalDeviceObject,
        StartDevice,
        AF_ACTIVE_ENDPOINTS * 4,
        0);
}

extern "C" NTSTATUS DriverEntry(
    _In_ PDRIVER_OBJECT DriverObject,
    _In_ PUNICODE_STRING RegistryPathName)
{
    for (ULONG i = 0; i < AF_ACTIVE_ENDPOINTS; ++i) {
        NTSTATUS st = InitEndpointSlot(&g_af_adapter.render_slots[i]);
        if (!NT_SUCCESS(st)) return st;
        st = InitEndpointSlot(&g_af_adapter.capture_slots[i]);
        if (!NT_SUCCESS(st)) return st;
    }

    NTSTATUS st = PcInitializeAdapterDriver(
        DriverObject,
        RegistryPathName,
        AddDevice);
    if (!NT_SUCCESS(st)) {
        return st;
    }

    // Intercept control IOCTLs and cleanup while delegating PortCls KS IRPs
    g_portcls_create  = DriverObject->MajorFunction[IRP_MJ_CREATE];
    g_portcls_cleanup = DriverObject->MajorFunction[IRP_MJ_CLEANUP];
    g_portcls_close   = DriverObject->MajorFunction[IRP_MJ_CLOSE];
    g_portcls_ioctl   = DriverObject->MajorFunction[IRP_MJ_DEVICE_CONTROL];
    g_portcls_unload  = DriverObject->DriverUnload;

    DriverObject->MajorFunction[IRP_MJ_CREATE]         = AfDispatchCreate;
    DriverObject->MajorFunction[IRP_MJ_CLEANUP]        = AfDispatchCleanup;
    DriverObject->MajorFunction[IRP_MJ_CLOSE]          = AfDispatchClose;
    DriverObject->MajorFunction[IRP_MJ_DEVICE_CONTROL] = AfDispatchDeviceControl;
    DriverObject->DriverUnload                         = AfDriverUnload;

    return STATUS_SUCCESS;
}

