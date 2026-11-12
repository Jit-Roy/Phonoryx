#pragma once

#include <windows.h>
#include <mmdeviceapi.h>
#include <atomic>
#include <string>
#include <vector>

#include "audioflow_capi.h"
#include "ring_buffer.hpp"

namespace af {

/**
 * Windows Core Audio MMDevice API manager.
 * Enumerates active/present capture & render endpoints and tracks live device
 * hot-plug events via IMMNotificationClient.
 */
class DeviceManager {
public:
    explicit DeviceManager(LogQueue* logs = nullptr);
    ~DeviceManager();

    bool initialize();
    void shutdown();

    std::vector<af_device_info_t> enumerate_devices();
    uint64_t change_sequence() const noexcept {
        return change_seq_.load(std::memory_order_acquire);
    }
    uint32_t virtual_endpoint_count() const noexcept {
        return virtual_count_.load(std::memory_order_acquire);
    }

    static bool is_virtual_device_name(const std::string& name);

private:
    class NotificationClient;

    LogQueue* logs_{nullptr};
    IMMDeviceEnumerator* enumerator_{nullptr};
    NotificationClient* client_{nullptr};
    bool com_owned_{false};
    std::atomic<uint64_t> change_seq_{1};
    std::atomic<uint32_t> virtual_count_{0};
};

} // namespace af

