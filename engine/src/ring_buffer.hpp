#pragma once

#include <atomic>
#include <cstdint>
#include <cstring>
#include <vector>
#include <algorithm>
#include <chrono>

#include "phonoryx_capi.h"

namespace af {

/**
 * Lock-free Single-Producer Single-Consumer (SPSC) stereo float32 ring buffer.
 * Preallocates storage at construction; read/write perform zero allocations and zero locks.
 */
class StereoRingBuffer {
public:
    explicit StereoRingBuffer(size_t capacity_frames = 16384) {
        size_t cap = 1;
        while (cap < capacity_frames) cap <<= 1;
        capacity_ = cap;
        mask_ = cap - 1;
        data_.assign(capacity_ * 2, 0.0f);
    }

    size_t capacity() const noexcept { return capacity_; }

    size_t available_read() const noexcept {
        const size_t w = write_idx_.load(std::memory_order_acquire);
        const size_t r = read_idx_.load(std::memory_order_relaxed);
        return w - r;
    }

    size_t available_write() const noexcept {
        const size_t r = read_idx_.load(std::memory_order_acquire);
        const size_t w = write_idx_.load(std::memory_order_relaxed);
        return capacity_ - (w - r);
    }

    size_t write(const float* interleaved_stereo, size_t frames) noexcept {
        const size_t r = read_idx_.load(std::memory_order_acquire);
        const size_t w = write_idx_.load(std::memory_order_relaxed);
        const size_t free_frames = capacity_ - (w - r);
        const size_t to_write = std::min(frames, free_frames);
        if (to_write == 0) {
            overruns_.fetch_add(1, std::memory_order_relaxed);
            return 0;
        }
        const size_t pos = w & mask_;
        const size_t first = std::min(to_write, capacity_ - pos);
        std::memcpy(&data_[pos * 2], interleaved_stereo, first * 2 * sizeof(float));
        const size_t second = to_write - first;
        if (second > 0) {
            std::memcpy(&data_[0], interleaved_stereo + first * 2, second * 2 * sizeof(float));
        }
        write_idx_.store(w + to_write, std::memory_order_release);
        if (to_write < frames) {
            overruns_.fetch_add(1, std::memory_order_relaxed);
        }
        return to_write;
    }

    size_t read(float* out_interleaved_stereo, size_t frames) noexcept {
        const size_t w = write_idx_.load(std::memory_order_acquire);
        const size_t r = read_idx_.load(std::memory_order_relaxed);
        const size_t avail = w - r;
        const size_t to_read = std::min(frames, avail);
        if (to_read > 0) {
            const size_t pos = r & mask_;
            const size_t first = std::min(to_read, capacity_ - pos);
            std::memcpy(out_interleaved_stereo, &data_[pos * 2], first * 2 * sizeof(float));
            const size_t second = to_read - first;
            if (second > 0) {
                std::memcpy(out_interleaved_stereo + first * 2, &data_[0], second * 2 * sizeof(float));
            }
            read_idx_.store(r + to_read, std::memory_order_release);
        }
        if (to_read < frames) {
            std::memset(out_interleaved_stereo + to_read * 2, 0, (frames - to_read) * 2 * sizeof(float));
            underruns_.fetch_add(1, std::memory_order_relaxed);
        }
        return to_read;
    }

    size_t peek(float* out_interleaved_stereo, size_t frames) const noexcept {
        const size_t w = write_idx_.load(std::memory_order_acquire);
        const size_t r = read_idx_.load(std::memory_order_relaxed);
        const size_t avail = w - r;
        const size_t to_read = std::min(frames, avail);
        if (to_read > 0) {
            const size_t pos = r & mask_;
            const size_t first = std::min(to_read, capacity_ - pos);
            std::memcpy(out_interleaved_stereo, &data_[pos * 2], first * 2 * sizeof(float));
            const size_t second = to_read - first;
            if (second > 0) {
                std::memcpy(out_interleaved_stereo + first * 2, &data_[0], second * 2 * sizeof(float));
            }
        }
        return to_read;
    }

    size_t skip(size_t frames) noexcept {
        const size_t w = write_idx_.load(std::memory_order_acquire);
        const size_t r = read_idx_.load(std::memory_order_relaxed);
        const size_t avail = w - r;
        const size_t to_skip = std::min(frames, avail);
        if (to_skip > 0) {
            read_idx_.store(r + to_skip, std::memory_order_release);
        }
        return to_skip;
    }

    void record_underrun() noexcept {
        underruns_.fetch_add(1, std::memory_order_relaxed);
    }

    void reset() noexcept {
        read_idx_.store(0, std::memory_order_release);
        write_idx_.store(0, std::memory_order_release);
    }

    uint32_t underruns() const noexcept { return underruns_.load(std::memory_order_relaxed); }
    uint32_t overruns() const noexcept { return overruns_.load(std::memory_order_relaxed); }

private:
    size_t capacity_{0};
    size_t mask_{0};
    std::vector<float> data_;
    alignas(64) std::atomic<size_t> read_idx_{0};
    alignas(64) std::atomic<size_t> write_idx_{0};
    std::atomic<uint32_t> underruns_{0};
    std::atomic<uint32_t> overruns_{0};
};

/**
 * Thread-safe bounded ring queue for diagnostics/log messages pushed from RT or worker threads
 * and drained by the UI thread without blocking RT callbacks.
 */
class LogQueue {
public:
    static constexpr size_t kCapacity = 256;

    void push(int32_t level, uint64_t node_id, const char* msg) noexcept {
        const uint32_t ticket = write_seq_.fetch_add(1, std::memory_order_acq_rel);
        Slot& slot = slots_[ticket % kCapacity];
        // Version seqlock: odd = write in progress, even = commit completed.
        slot.version.store(ticket * 2 + 1, std::memory_order_release);

        slot.entry.timestamp_ms = static_cast<uint64_t>(
            std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::system_clock::now().time_since_epoch()).count());
        slot.entry.level = level;
        slot.entry.node_id = node_id;
        std::memset(slot.entry.message, 0, sizeof(slot.entry.message));
        if (msg) {
            std::strncpy(slot.entry.message, msg, sizeof(slot.entry.message) - 1);
        }

        slot.version.store(ticket * 2 + 2, std::memory_order_release);
        slot.ready.store(true, std::memory_order_release);
    }

    int32_t pop_batch(af_log_entry_t* out, int32_t max_count) noexcept {
        if (!out || max_count <= 0) return 0;
        int32_t count = 0;
        uint32_t w = write_seq_.load(std::memory_order_acquire);
        if (w - read_seq_ > kCapacity) {
            read_seq_ = w - static_cast<uint32_t>(kCapacity);
        }
        while (read_seq_ < w && count < max_count) {
            Slot& slot = slots_[read_seq_ % kCapacity];
            if (!slot.ready.load(std::memory_order_acquire)) {
                break;
            }
            const uint32_t expected_ver = read_seq_ * 2 + 2;
            const uint32_t v1 = slot.version.load(std::memory_order_acquire);
            if (v1 != expected_ver) {
                // Overwritten by newer ticket or write in progress
                ++read_seq_;
                continue;
            }
            af_log_entry_t candidate = slot.entry;
            const uint32_t v2 = slot.version.load(std::memory_order_acquire);
            if (v1 == v2 && (v2 % 2 == 0)) {
                out[count++] = candidate;
            }
            ++read_seq_;
        }
        return count;
    }

private:
    struct Slot {
        std::atomic<bool> ready{false};
        std::atomic<uint32_t> version{0};
        af_log_entry_t entry{};
    };
    Slot slots_[kCapacity]{};
    alignas(64) std::atomic<uint32_t> write_seq_{0};
    uint32_t read_seq_{0};
};

} // namespace af

namespace phonoryx {
    using af::StereoRingBuffer;
    using af::LogQueue;
}


