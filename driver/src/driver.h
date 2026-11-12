#pragma once

#define _KERNEL_MODE 1
#include <ntddk.h>
#include <wdf.h>
#include <portcls.h>
#include <ks.h>
#include <ksmedia.h>

#include "../include/audioflow_driver_ioctl.h"

#define AF_POOL_TAG 'lFuA' /* 'AuFl' */
#define AF_ACTIVE_ENDPOINTS 4

typedef struct _AF_ENDPOINT_SLOT {
    AUDIOFLOW_SHARED_RING* ring;
    ULONG ring_bytes;
    PMDL mdl;
    PVOID user_va;
    PEPROCESS owner_process;
    KSPIN_LOCK lock;
    ULONG sample_rate;
    ULONG channels;
    BOOLEAN enabled;
} AF_ENDPOINT_SLOT;

typedef struct _AF_ADAPTER_CONTEXT {
    PDEVICE_OBJECT pdo;
    UNICODE_STRING control_symlink;
    BOOLEAN symlink_created;
    AF_ENDPOINT_SLOT render_slots[AF_ACTIVE_ENDPOINTS];
    AF_ENDPOINT_SLOT capture_slots[AF_ACTIVE_ENDPOINTS];
} AF_ADAPTER_CONTEXT;

extern AF_ADAPTER_CONTEXT g_af_adapter;

NTSTATUS AfCreateWaveRtMiniport(
    _Out_ PUNKNOWN* Unknown,
    _In_ ULONG EndpointIndex,
    _In_ BOOLEAN IsCapture,
    _In_ POOL_TYPE PoolType);

NTSTATUS AfCreateTopologyMiniport(
    _Out_ PUNKNOWN* Unknown,
    _In_ ULONG EndpointIndex,
    _In_ BOOLEAN IsCapture,
    _In_ POOL_TYPE PoolType);

