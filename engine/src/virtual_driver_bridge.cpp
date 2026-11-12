#include <windows.h>
#include <initguid.h>
#include "virtual_driver_bridge.hpp"

#include <cfgmgr32.h>
#include <algorithm>
#include <cstring>

namespace af {

VirtualDriverBridge::VirtualDriverBridge(LogQueue* logs) : logs_(logs) {}

VirtualDriverBridge::~VirtualDriverBridge() {
    disconnect();
}

bool VirtualDriverBridge::connect() {
    if (handle_ != INVALID_HANDLE_VALUE) return true;

    ULONG list_len = 0;
    CONFIGRET cr = CM_Get_Device_Interface_List_SizeW(
        &list_len,
        const_cast<LPGUID>(&GUID_DEVINTERFACE_AUDIOFLOW_CONTROL),
        nullptr,
        CM_GET_DEVICE_INTERFACE_LIST_PRESENT);
    if (cr != CR_SUCCESS || list_len <= 1) {
        return false;
    }

    std::wstring buf(list_len, L'\0');
    cr = CM_Get_Device_Interface_ListW(
        const_cast<LPGUID>(&GUID_DEVINTERFACE_AUDIOFLOW_CONTROL),
        nullptr,
        buf.data(),
        list_len,
        CM_GET_DEVICE_INTERFACE_LIST_PRESENT);
    if (cr != CR_SUCCESS || buf[0] == L'\0') {
        return false;
    }

    HANDLE h = CreateFileW(
        buf.c_str(),
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        nullptr,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        if (logs_) logs_->push(1, 0, "AudioFlow virtual driver interface found, but CreateFile failed");
        return false;
    }

    AUDIOFLOW_DRIVER_INFO info{};
    DWORD bytes_ret = 0;
    if (!DeviceIoControl(h, IOCTL_AUDIOFLOW_GET_INFO, nullptr, 0, &info, sizeof(info), &bytes_ret, nullptr)) {
        CloseHandle(h);
        return false;
    }

    handle_ = h;
    info_ = info;
    if (logs_) logs_->push(0, 0, "Connected to AudioFlow Virtual Audio Driver");
    return true;
}

void VirtualDriverBridge::disconnect() {
    unmap_all();
    if (handle_ != INVALID_HANDLE_VALUE) {
        CloseHandle(handle_);
        handle_ = INVALID_HANDLE_VALUE;
    }
    info_ = {};
}

bool VirtualDriverBridge::map_endpoint_ring(uint32_t endpoint_index, bool is_capture, AUDIOFLOW_SHARED_RING** out_ring) {
    if (!out_ring) return false;
    *out_ring = nullptr;
    if (handle_ == INVALID_HANDLE_VALUE && !connect()) return false;

    for (const auto& s : mapped_) {
        if (s.endpoint_index == endpoint_index && s.is_capture == is_capture) {
            *out_ring = s.ptr;
            return true;
        }
    }

    AUDIOFLOW_MAP_REQUEST req{};
    req.endpoint_index = endpoint_index;
    req.is_capture = is_capture ? 1u : 0u;
    AUDIOFLOW_MAP_RESPONSE resp{};
    DWORD bytes_ret = 0;

    if (!DeviceIoControl(handle_, IOCTL_AUDIOFLOW_MAP_ENDPOINT_RING,
                         &req, sizeof(req), &resp, sizeof(resp), &bytes_ret, nullptr)) {
        return false;
    }

    auto* ring = reinterpret_cast<AUDIOFLOW_SHARED_RING*>(static_cast<uintptr_t>(resp.user_address));
    if (!ring) return false;

    mapped_.push_back(MappedSlot{endpoint_index, is_capture, ring});
    *out_ring = ring;
    return true;
}

void VirtualDriverBridge::unmap_all() {
    if (handle_ != INVALID_HANDLE_VALUE) {
        for (const auto& s : mapped_) {
            AUDIOFLOW_MAP_REQUEST req{s.endpoint_index, s.is_capture ? 1u : 0u};
            DWORD bytes_ret = 0;
            DeviceIoControl(handle_, IOCTL_AUDIOFLOW_UNMAP_ENDPOINT_RING,
                            &req, sizeof(req), nullptr, 0, &bytes_ret, nullptr);
        }
    }
    mapped_.clear();
}

size_t VirtualDriverBridge::write_shared_ring(AUDIOFLOW_SHARED_RING* ring, const float* stereo_in, size_t frames) noexcept {
    if (!ring || !stereo_in || frames == 0 || ring->frame_capacity == 0) return 0;
    const uint64_t cap = ring->frame_capacity;
    const uint64_t r = ring->read_frame_index;
    const uint64_t w = ring->write_frame_index;
    const uint64_t free_frames = (w - r < cap) ? (cap - (w - r)) : 0;
    const size_t to_write = static_cast<size_t>(std::min<uint64_t>(frames, free_frames));
    if (to_write == 0) {
        ring->xruns += 1;
        return 0;
    }
    const size_t pos = static_cast<size_t>(w % cap);
    const size_t first = std::min(to_write, static_cast<size_t>(cap) - pos);
    std::memcpy(&ring->samples[pos * 2], stereo_in, first * 2 * sizeof(float));
    const size_t second = to_write - first;
    if (second > 0) {
        std::memcpy(&ring->samples[0], stereo_in + first * 2, second * 2 * sizeof(float));
    }
    MemoryBarrier();
    ring->write_frame_index = w + to_write;
    return to_write;
}

size_t VirtualDriverBridge::read_shared_ring(AUDIOFLOW_SHARED_RING* ring, float* stereo_out, size_t frames) noexcept {
    if (!ring || !stereo_out || frames == 0 || ring->frame_capacity == 0) return 0;
    const uint64_t cap = ring->frame_capacity;
    const uint64_t w = ring->write_frame_index;
    const uint64_t r = ring->read_frame_index;
    MemoryBarrier();
    const uint64_t avail = (w > r) ? (w - r) : 0;
    const size_t to_read = static_cast<size_t>(std::min<uint64_t>(frames, avail));
    if (to_read > 0) {
        const size_t pos = static_cast<size_t>(r % cap);
        const size_t first = std::min(to_read, static_cast<size_t>(cap) - pos);
        std::memcpy(stereo_out, &ring->samples[pos * 2], first * 2 * sizeof(float));
        const size_t second = to_read - first;
        if (second > 0) {
            std::memcpy(stereo_out + first * 2, &ring->samples[0], second * 2 * sizeof(float));
        }
        MemoryBarrier();
        ring->read_frame_index = r + to_read;
    }
    if (to_read < frames) {
        std::memset(stereo_out + to_read * 2, 0, (frames - to_read) * 2 * sizeof(float));
    }
    return to_read;
}

} // namespace af
