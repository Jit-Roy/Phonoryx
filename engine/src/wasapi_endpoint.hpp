#pragma once

#include <windows.h>
#include <audioclient.h>
#include <mmdeviceapi.h>
#include <atomic>
#include <memory>
#include <string>
#include <thread>

#include "drift_controller.hpp"
#include "resampler.hpp"
#include "ring_buffer.hpp"

namespace af {

enum class EndpointMode {
    InputCapture = 0,     // Mic / Line-In / Virtual Input
    SystemLoopback = 1,   // Render endpoint opened with AUDCLNT_STREAMFLAGS_LOOPBACK
    ProcessLoopback = 2,  // ActivateAudioInterfaceAsync("VAD\\Process_Loopback")
    OutputRender = 3      // Speakers / Headphones / Virtual Output / Virtual Mic bridge
};

/**
 * Manages a single real-time WASAPI audio endpoint (capture, system loopback,
 * per-process loopback, or render) on a dedicated MMCSS Pro Audio thread,
 * communicating with the audio graph exclusively via lock-free SPSC ring buffers.
 */
class WasapiEndpointWorker {
public:
    WasapiEndpointWorker(
        uint64_t node_id,
        EndpointMode mode,
        std::string device_id,
        uint32_t target_pid,
        uint32_t engine_sample_rate,
        LogQueue* logs);
    ~WasapiEndpointWorker();

    bool start();
    void stop();

    StereoRingBuffer& ring() noexcept { return ring_; }
    size_t read_capture_block(float* out_stereo, size_t frames) noexcept;
    bool is_connected() const noexcept { return connected_.load(std::memory_order_acquire); }
    uint32_t xruns() const noexcept {
        return ring_.underruns() + ring_.overruns();
    }

    void set_master_clock(HANDLE tick_event) noexcept {
        master_tick_event_ = tick_event;
        is_master_clock_.store(tick_event != nullptr, std::memory_order_release);
    }
    bool is_master_clock() const noexcept {
        return is_master_clock_.load(std::memory_order_acquire);
    }
    uint32_t device_period_us() const noexcept {
        return device_period_us_.load(std::memory_order_relaxed);
    }
    uint32_t device_sample_rate() const noexcept {
        return device_sample_rate_.load(std::memory_order_relaxed);
    }

private:
    void thread_main();
    bool run_capture_session();
    bool run_render_session();

    uint64_t node_id_{0};
    EndpointMode mode_{EndpointMode::InputCapture};
    std::string device_id_;
    uint32_t target_pid_{0};
    uint32_t engine_sample_rate_{48000};
    LogQueue* logs_{nullptr};

    StereoRingBuffer ring_{32768};
    StereoConverterResampler resampler_;

    HANDLE stop_event_{nullptr};
    HANDLE master_tick_event_{nullptr};
    std::atomic<bool> is_master_clock_{false};
    std::atomic<uint32_t> device_period_us_{10000};
    std::atomic<uint32_t> device_sample_rate_{48000};
    std::atomic<bool> running_{false};
    std::atomic<bool> connected_{false};
    bool rt_capture_primed_{false};
    bool rt_capture_fade_in_{false};
    std::thread thread_;
};

} // namespace af

