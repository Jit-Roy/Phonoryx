#include "dsp_nodes.hpp"
#include "utf_util.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>

namespace af {

namespace {
constexpr float kPi = 3.14159265358979323846f;

void calc_shelf(BiquadState& bq, bool high, float fs, float f0, float db_gain, float slope = 0.707f) noexcept {
    const float A = std::pow(10.0f, db_gain / 40.0f);
    const float w0 = 2.0f * kPi * f0 / static_cast<float>(fs);
    const float cw = std::cos(w0);
    const float sw = std::sin(w0);
    const float alpha = sw / 2.0f * std::sqrt((A + 1.0f / A) * (1.0f / slope - 1.0f) + 2.0f);
    const float two_sqrtA_alpha = 2.0f * std::sqrt(A) * alpha;

    float b0, b1, b2, a0, a1, a2;
    if (!high) {
        b0 = A * ((A + 1.0f) - (A - 1.0f) * cw + two_sqrtA_alpha);
        b1 = 2.0f * A * ((A - 1.0f) - (A + 1.0f) * cw);
        b2 = A * ((A + 1.0f) - (A - 1.0f) * cw - two_sqrtA_alpha);
        a0 = (A + 1.0f) + (A - 1.0f) * cw + two_sqrtA_alpha;
        a1 = -2.0f * ((A - 1.0f) + (A + 1.0f) * cw);
        a2 = (A + 1.0f) + (A - 1.0f) * cw - two_sqrtA_alpha;
    } else {
        b0 = A * ((A + 1.0f) + (A - 1.0f) * cw + two_sqrtA_alpha);
        b1 = -2.0f * A * ((A - 1.0f) + (A + 1.0f) * cw);
        b2 = A * ((A + 1.0f) + (A - 1.0f) * cw - two_sqrtA_alpha);
        a0 = (A + 1.0f) - (A - 1.0f) * cw + two_sqrtA_alpha;
        a1 = 2.0f * ((A - 1.0f) - (A + 1.0f) * cw);
        a2 = (A + 1.0f) - (A - 1.0f) * cw - two_sqrtA_alpha;
    }
    bq.b0 = b0 / a0;
    bq.b1 = b1 / a0;
    bq.b2 = b2 / a0;
    bq.a1 = a1 / a0;
    bq.a2 = a2 / a0;
}

void calc_peaking(BiquadState& bq, float fs, float f0, float db_gain, float q = 1.0f) noexcept {
    const float A = std::pow(10.0f, db_gain / 40.0f);
    const float w0 = 2.0f * kPi * f0 / static_cast<float>(fs);
    const float cw = std::cos(w0);
    const float sw = std::sin(w0);
    const float alpha = sw / (2.0f * q);

    const float b0 = 1.0f + alpha * A;
    const float b1 = -2.0f * cw;
    const float b2 = 1.0f - alpha * A;
    const float a0 = 1.0f + alpha / A;
    const float a1 = -2.0f * cw;
    const float a2 = 1.0f - alpha / A;

    bq.b0 = b0 / a0;
    bq.b1 = b1 / a0;
    bq.b2 = b2 / a0;
    bq.a1 = a1 / a0;
    bq.a2 = a2 / a0;
}
} // namespace

void FiveBandEq::prepare(uint32_t sample_rate) noexcept {
    sample_rate_ = sample_rate > 0 ? sample_rate : 48000;
    for (auto& b : bands_) b.reset();
    dirty_ = true;
}

void FiveBandEq::update_if_needed(const std::array<float, 5>& target_gains_db) noexcept {
    bool changed = dirty_;
    for (size_t i = 0; i < 5; ++i) {
        if (std::abs(target_gains_db[i] - last_gains_[i]) > 0.05f) {
            changed = true;
            break;
        }
    }
    if (!changed) return;

    last_gains_ = target_gains_db;
    dirty_ = false;
    const float fs = static_cast<float>(sample_rate_);
    calc_shelf(bands_[0], false, fs, 100.0f, last_gains_[0]);
    calc_peaking(bands_[1], fs, 350.0f, last_gains_[1], 1.0f);
    calc_peaking(bands_[2], fs, 1000.0f, last_gains_[2], 1.0f);
    calc_peaking(bands_[3], fs, 3500.0f, last_gains_[3], 1.0f);
    calc_shelf(bands_[4], true, fs, 10000.0f, last_gains_[4]);
}

void FiveBandEq::process(float* stereo_buf, size_t frames) noexcept {
    for (size_t i = 0; i < 5; ++i) {
        if (std::abs(last_gains_[i]) > 0.05f) {
            bands_[i].process_stereo(stereo_buf, frames);
        }
    }
}

WavFileRecorder::WavFileRecorder(std::string path, uint32_t sample_rate, LogQueue* logs)
    : path_(std::move(path)), sample_rate_(sample_rate > 0 ? sample_rate : 48000), logs_(logs) {}

WavFileRecorder::~WavFileRecorder() {
    stop();
}

bool WavFileRecorder::start() {
    if (running_.load(std::memory_order_acquire)) return true;
    if (path_.empty()) return false;
    ring_.reset();
    running_.store(true, std::memory_order_release);
    worker_ = std::thread(&WavFileRecorder::writer_loop, this);
    return true;
}

void WavFileRecorder::stop() {
    if (!running_.exchange(false, std::memory_order_acq_rel)) return;
    if (worker_.joinable()) worker_.join();
}

void WavFileRecorder::push_stereo(const float* stereo_buf, size_t frames) noexcept {
    if (!running_.load(std::memory_order_relaxed)) return;
    ring_.write(stereo_buf, frames);
}

void WavFileRecorder::writer_loop() {
    FILE* fp = nullptr;
    std::wstring wpath = utf8_to_wide(path_.c_str());
    if (!wpath.empty()) {
        _wfopen_s(&fp, wpath.c_str(), L"wb");
    }
    if (!fp) {
        if (logs_) {
            char msg[256];
            std::snprintf(msg, sizeof(msg), "Failed to open recording file: %s", path_.c_str());
            logs_->push(2, 0, msg);
        }
        running_.store(false, std::memory_order_release);
        return;
    }

    // Write IEEE float32 stereo WAV header (44 bytes, patched on close)
    uint8_t hdr[44] = {};
    std::memcpy(hdr + 0, "RIFF", 4);
    std::memcpy(hdr + 8, "WAVE", 4);
    std::memcpy(hdr + 12, "fmt ", 4);
    uint32_t fmt_size = 16;
    uint16_t audio_fmt = 3; // WAVE_FORMAT_IEEE_FLOAT
    uint16_t channels = 2;
    uint32_t sr = sample_rate_;
    uint16_t bits = 32;
    uint16_t block_align = channels * (bits / 8);
    uint32_t byte_rate = sr * block_align;
    std::memcpy(hdr + 16, &fmt_size, 4);
    std::memcpy(hdr + 20, &audio_fmt, 2);
    std::memcpy(hdr + 22, &channels, 2);
    std::memcpy(hdr + 24, &sr, 4);
    std::memcpy(hdr + 28, &byte_rate, 4);
    std::memcpy(hdr + 32, &block_align, 2);
    std::memcpy(hdr + 34, &bits, 2);
    std::memcpy(hdr + 36, "data", 4);
    std::fwrite(hdr, 1, sizeof(hdr), fp);

    std::vector<float> chunk(2048 * 2, 0.0f);
    uint64_t total_data_bytes = 0;

    while (running_.load(std::memory_order_acquire) || ring_.available_read() > 0) {
        const size_t avail = ring_.available_read();
        if (avail > 0) {
            const size_t to_read = std::min(avail, size_t{2048});
            ring_.read(chunk.data(), to_read);
            const size_t bytes = to_read * 2 * sizeof(float);
            std::fwrite(chunk.data(), 1, bytes, fp);
            total_data_bytes += bytes;
        } else {
            Sleep(5);
        }
    }

    const uint32_t data_chunk_sz = static_cast<uint32_t>(std::min<uint64_t>(total_data_bytes, 0xFFFFFF00ULL));
    const uint32_t riff_sz = 36 + data_chunk_sz;
    std::fseek(fp, 4, SEEK_SET);
    std::fwrite(&riff_sz, 4, 1, fp);
    std::fseek(fp, 40, SEEK_SET);
    std::fwrite(&data_chunk_sz, 4, 1, fp);
    std::fclose(fp);
}

namespace dsp {

void apply_volume_ramped(float* buf, size_t frames, float target_gain, float& current_gain) noexcept {
    if (frames == 0) return;
    if (std::abs(target_gain - current_gain) < 1e-5f) {
        current_gain = target_gain;
        if (std::abs(current_gain - 1.0f) < 1e-5f) return;
        for (size_t i = 0; i < frames * 2; ++i) {
            buf[i] *= current_gain;
        }
        return;
    }
    const float step = (target_gain - current_gain) / static_cast<float>(frames);
    float g = current_gain;
    for (size_t i = 0; i < frames; ++i) {
        g = (i + 1 == frames) ? target_gain : (g + step);
        buf[i * 2] *= g;
        buf[i * 2 + 1] *= g;
    }
    current_gain = target_gain;
}

void apply_stereo_to_mono(float* buf, size_t frames) noexcept {
    for (size_t i = 0; i < frames; ++i) {
        const float m = 0.5f * (buf[i * 2] + buf[i * 2 + 1]);
        buf[i * 2] = m;
        buf[i * 2 + 1] = m;
    }
}

void apply_mono_to_stereo(float* buf, size_t frames) noexcept {
    for (size_t i = 0; i < frames; ++i) {
        buf[i * 2 + 1] = buf[i * 2];
    }
}

void apply_channel_map(float* buf, size_t frames, int32_t map_l, int32_t map_r) noexcept {
    auto pick = [](float l, float r, int32_t mode) -> float {
        switch (mode) {
            case 0: return l;
            case 1: return r;
            case 2: return 0.5f * (l + r);
            default: return 0.0f;
        }
    };
    for (size_t i = 0; i < frames; ++i) {
        const float l = buf[i * 2];
        const float r = buf[i * 2 + 1];
        buf[i * 2] = pick(l, r, map_l);
        buf[i * 2 + 1] = pick(l, r, map_r);
    }
}

void compute_meters(const float* buf, size_t frames, NodeRuntimeParams& params) noexcept {
    if (frames == 0) return;
    float pk_l = 0.0f, pk_r = 0.0f;
    double sum_l = 0.0, sum_r = 0.0;
    for (size_t i = 0; i < frames; ++i) {
        const float l = buf[i * 2];
        const float r = buf[i * 2 + 1];
        pk_l = std::max(pk_l, std::abs(l));
        pk_r = std::max(pk_r, std::abs(r));
        sum_l += static_cast<double>(l) * l;
        sum_r += static_cast<double>(r) * r;
    }
    const float rms_l = static_cast<float>(std::sqrt(sum_l / static_cast<double>(frames)));
    const float rms_r = static_cast<float>(std::sqrt(sum_r / static_cast<double>(frames)));

    // Smooth decay / instant peak rise for UI responsiveness
    const float prev_pl = params.peak_l.load(std::memory_order_relaxed);
    const float prev_pr = params.peak_r.load(std::memory_order_relaxed);
    params.peak_l.store(std::max(pk_l, prev_pl * 0.85f), std::memory_order_relaxed);
    params.peak_r.store(std::max(pk_r, prev_pr * 0.85f), std::memory_order_relaxed);
    params.rms_l.store(rms_l, std::memory_order_relaxed);
    params.rms_r.store(rms_r, std::memory_order_relaxed);
    params.active.store((pk_l > 1e-4f || pk_r > 1e-4f) ? 1u : 0u, std::memory_order_relaxed);
}

void mix_accumulate(float* dst, const float* src, size_t frames) noexcept {
    for (size_t i = 0; i < frames * 2; ++i) {
        dst[i] += src[i];
    }
}

void apply_soft_limiter(float* buf, size_t frames) noexcept {
    for (size_t i = 0; i < frames * 2; ++i) {
        const float x = buf[i];
        if (x > 1.0f || x < -1.0f) {
            buf[i] = std::tanh(x);
        }
    }
}

} // namespace dsp

} // namespace af
