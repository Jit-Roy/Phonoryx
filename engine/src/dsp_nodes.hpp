#pragma once

#include <array>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include "ring_buffer.hpp"

namespace af {

/**
 * Lock-free atomic meter + parameter state shared between UI/control thread and RT audio thread.
 */
struct NodeRuntimeParams {
    std::atomic<float> volume{1.0f};      // linear 0.0 .. 2.0
    std::atomic<float> gain_db{0.0f};     // -60.0 .. +24.0 dB
    std::atomic<int32_t> muted{0};        // 0 or 1
    std::atomic<int32_t> chan_map_l{0};   // 0=L, 1=R, 2=Mix, -1=Mute
    std::atomic<int32_t> chan_map_r{1};   // 0=L, 1=R, 2=Mix, -1=Mute
    std::array<std::atomic<float>, 5> eq_gains_db{}; // 100Hz, 350Hz, 1kHz, 3.5kHz, 10kHz

    // Telemetry updated on RT thread, read lock-free from UI thread
    std::atomic<float> peak_l{0.0f};
    std::atomic<float> peak_r{0.0f};
    std::atomic<float> rms_l{0.0f};
    std::atomic<float> rms_r{0.0f};
    std::atomic<uint32_t> active{0};
    std::atomic<uint32_t> xruns{0};
};

struct BiquadState {
    float b0{1.0f}, b1{0.0f}, b2{0.0f}, a1{0.0f}, a2{0.0f};
    float z1_l{0.0f}, z2_l{0.0f}, z1_r{0.0f}, z2_r{0.0f};

    void reset() noexcept {
        z1_l = z2_l = z1_r = z2_r = 0.0f;
    }

    void process_stereo(float* buf, size_t frames) noexcept {
        for (size_t i = 0; i < frames; ++i) {
            const float xl = buf[i * 2];
            const float yl = b0 * xl + z1_l;
            z1_l = b1 * xl - a1 * yl + z2_l;
            z2_l = b2 * xl - a2 * yl;
            buf[i * 2] = yl;

            const float xr = buf[i * 2 + 1];
            const float yr = b0 * xr + z1_r;
            z1_r = b1 * xr - a1 * yr + z2_r;
            z2_r = b2 * xr - a2 * yr;
            buf[i * 2 + 1] = yr;
        }
    }
};

class FiveBandEq {
public:
    void prepare(uint32_t sample_rate) noexcept;
    void update_if_needed(const std::array<float, 5>& target_gains_db) noexcept;
    void process(float* stereo_buf, size_t frames) noexcept;

private:
    uint32_t sample_rate_{48000};
    std::array<float, 5> last_gains_{0.0f, 0.0f, 0.0f, 0.0f, 0.0f};
    std::array<BiquadState, 5> bands_{};
    bool dirty_{true};
};

/**
 * Non-real-time WAV file recorder drained from a preallocated lock-free SPSC ring buffer.
 */
class WavFileRecorder {
public:
    explicit WavFileRecorder(std::string path, uint32_t sample_rate, LogQueue* logs = nullptr);
    ~WavFileRecorder();

    bool start();
    void stop();

    // Called on RT thread: zero locks, zero allocations
    void push_stereo(const float* stereo_buf, size_t frames) noexcept;
    uint32_t xruns() const noexcept { return ring_.overruns(); }

private:
    void writer_loop();

    std::string path_;
    uint32_t sample_rate_{48000};
    LogQueue* logs_{nullptr};
    StereoRingBuffer ring_{65536};
    std::atomic<bool> running_{false};
    std::thread worker_;
};

namespace dsp {

void apply_volume_ramped(float* buf, size_t frames, float target_gain, float& current_gain) noexcept;
void apply_stereo_to_mono(float* buf, size_t frames) noexcept;
void apply_mono_to_stereo(float* buf, size_t frames) noexcept;
void apply_channel_map(float* buf, size_t frames, int32_t map_l, int32_t map_r) noexcept;
void compute_meters(const float* buf, size_t frames, NodeRuntimeParams& params) noexcept;
void mix_accumulate(float* dst, const float* src, size_t frames) noexcept;
void apply_soft_limiter(float* buf, size_t frames) noexcept;

inline float db_to_linear(float db) noexcept {
    if (db <= -60.0f) return 0.0f;
    return std::pow(10.0f, db / 20.0f);
}

} // namespace dsp

} // namespace af

