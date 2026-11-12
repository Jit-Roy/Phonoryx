#pragma once

#ifdef _KERNEL_MODE
  #include <ntddk.h>
  #include <windef.h>
#else
  #include <windows.h>
  #include <winioctl.h>
  #include <stdint.h>
#endif

/* {9A8E2B41-7C3F-4E91-9B14-6D52F1A84010} */
DEFINE_GUID(GUID_DEVINTERFACE_AUDIOFLOW_CONTROL,
    0x9a8e2b41, 0x7c3f, 0x4e91, 0x9b, 0x14, 0x6d, 0x52, 0xf1, 0xa8, 0x40, 0x10);

#define AUDIOFLOW_MAX_VIRTUAL_ENDPOINTS 8
#define AUDIOFLOW_RING_FRAMES           16384
#define AUDIOFLOW_CHANNELS              2

#define FILE_DEVICE_AUDIOFLOW 0x8000u

#define IOCTL_AUDIOFLOW_GET_INFO \
    CTL_CODE(FILE_DEVICE_AUDIOFLOW, 0x800, METHOD_BUFFERED, FILE_ANY_ACCESS)

#define IOCTL_AUDIOFLOW_MAP_ENDPOINT_RING \
    CTL_CODE(FILE_DEVICE_AUDIOFLOW, 0x801, METHOD_BUFFERED, FILE_ANY_ACCESS)

#define IOCTL_AUDIOFLOW_UNMAP_ENDPOINT_RING \
    CTL_CODE(FILE_DEVICE_AUDIOFLOW, 0x802, METHOD_BUFFERED, FILE_ANY_ACCESS)

#define IOCTL_AUDIOFLOW_SET_ENDPOINT_CONFIG \
    CTL_CODE(FILE_DEVICE_AUDIOFLOW, 0x803, METHOD_BUFFERED, FILE_ANY_ACCESS)

#pragma pack(push, 1)

typedef struct _AUDIOFLOW_DRIVER_INFO {
    uint32_t version;
    uint32_t endpoint_count;
    uint32_t sample_rate;
    uint32_t channels;
    uint32_t ring_frames;
} AUDIOFLOW_DRIVER_INFO;

/* Shared kernel<->user ring buffer header placed at page 0 of the MDL-mapped region.
 * Audio frames follow immediately as interleaved float32 stereo samples.
 */
typedef struct _AUDIOFLOW_SHARED_RING {
    volatile uint64_t write_frame_index;
    volatile uint64_t read_frame_index;
    uint32_t frame_capacity;
    uint32_t channels;
    uint32_t sample_rate;
    uint32_t active;
    uint64_t xruns;
    uint8_t reserved[24];
    float samples[1]; /* variable length: frame_capacity * channels */
} AUDIOFLOW_SHARED_RING;

typedef struct _AUDIOFLOW_MAP_REQUEST {
    uint32_t endpoint_index; /* 0..AUDIOFLOW_MAX_VIRTUAL_ENDPOINTS-1 */
    uint32_t is_capture;     /* 0 = virtual render (playback), 1 = virtual capture (mic) */
} AUDIOFLOW_MAP_REQUEST;

typedef struct _AUDIOFLOW_MAP_RESPONSE {
    uint64_t user_address;
    uint32_t byte_size;
    uint32_t endpoint_index;
} AUDIOFLOW_MAP_RESPONSE;

typedef struct _AUDIOFLOW_ENDPOINT_CONFIG {
    uint32_t endpoint_index;
    uint32_t is_capture;
    uint32_t enabled;
    uint32_t sample_rate;
    uint32_t channels;
} AUDIOFLOW_ENDPOINT_CONFIG;

#pragma pack(pop)
