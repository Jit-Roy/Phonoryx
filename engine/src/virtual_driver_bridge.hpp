#pragma once

#include <windows.h>
#include <atomic>
#include <cstdint>
#include <string>
#include <vector>

#include "audioflow_driver_ioctl.h"
#include "ring_buffer.hpp"

namespace af {

/**
 * User-mode communication layer for the custom AudioFlow WDK Virtual Audio Driver.
 * Discovers GUID_DEVINTERFACE_AUDIOFLOW_CONTROL, opens the kernel control device,
 * and maps MDL-backed shared-memory ring buffers for low-latency PCM exchange.
 */
class VirtualDriverBridge {
public:
    explicit VirtualDriverBridge(LogQueue* logs = nullptr);
    ~VirtualDriverBridge();

    bool connect();
    void disconnect();

    bool is_connected() const noexcept { return handle_ != INVALID_HANDLE_VALUE; }
    uint32_t endpoint_count() const noexcept { return info_.endpoint_count; }

    bool map_endpoint_ring(uint32_t endpoint_index, bool is_capture, AUDIOFLOW_SHARED_RING** out_ring);
    void unmap_all();

    static size_t write_shared_ring(AUDIOFLOW_SHARED_RING* ring, const float* stereo_in, size_t frames) noexcept;
    static size_t read_shared_ring(AUDIOFLOW_SHARED_RING* ring, float* stereo_out, size_t frames) noexcept;

private:
    struct MappedSlot {
        uint32_t endpoint_index{0};
        bool is_capture{false};
        AUDIOFLOW_SHARED_RING* ptr{nullptr};
    };

    LogQueue* logs_{nullptr};
    HANDLE handle_{INVALID_HANDLE_VALUE};
    AUDIOFLOW_DRIVER_INFO info_{};
    std::vector<MappedSlot> mapped_;
};

} // namespace af

