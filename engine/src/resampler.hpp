#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <vector>

namespace af {

/**
 * Preallocated windowed-sinc / Hermite-cubic stereo resampler and PCM format converter.
 * Converts arbitrary Windows WASAPI wave formats (int16, int24, int32, float32; 1..8 channels)
 * into stereo float32 and resamples between source and destination sample rates with:
 *  - 1:1 direct bypass when sample rates match and drift is zero.
 *  - 4-point C^1 continuous Hermite-cubic interpolation for sub-ppm drift trim.
 *  - 8-point Blackman-windowed sinc with anti-aliasing cutoff for arbitrary rate changes (e.g. 44.1k <-> 48k).
 */
class StereoConverterResampler {
public:
    static constexpr size_t kSincRadius = 4; // 8-point windowed sinc filter (-3..+4)

    void configure(uint32_t src_rate, uint32_t dst_rate, size_t max_in_frames = 8192) {
        src_rate_ = src_rate > 0 ? src_rate : 48000;
        dst_rate_ = dst_rate > 0 ? dst_rate : 48000;
        base_ratio_ = static_cast<double>(src_rate_) / static_cast<double>(dst_rate_);
        phase_ = 0.0;
        reset_history();
        scratch_stereo_.assign(max_in_frames * 2, 0.0f);
    }

    void reset() noexcept {
        phase_ = 0.0;
        reset_history();
    }

    bool is_phase_zero() const noexcept {
        return std::abs(phase_) < 1e-4;
    }

    void reset_history() noexcept {
        for (size_t i = 0; i < kSincRadius; ++i) {
            prev_l_[i] = 0.0f;
            prev_r_[i] = 0.0f;
        }
        has_prev_ = false;
    }

    /**
     * Converts raw interleaved PCM samples (1..N channels) to interleaved stereo float32
     * into preallocated scratch buffer, then resamples to out_stereo.
     * Returns number of output stereo frames written.
     */
    size_t process_raw_to_stereo(
        const uint8_t* raw_bytes,
        size_t in_frames,
        uint16_t src_channels,
        uint16_t bits_per_sample,
        bool is_float,
        float* out_stereo,
        size_t max_out_frames,
        double drift_adjust = 1.0) noexcept
    {
        if (!raw_bytes || in_frames == 0 || src_channels == 0 || !out_stereo || max_out_frames == 0) {
            return 0;
        }
        const size_t clamped_in = std::min(in_frames, scratch_stereo_.size() / 2);
        convert_to_stereo_float(raw_bytes, clamped_in, src_channels, bits_per_sample, is_float, scratch_stereo_.data());
        return resample_stereo(scratch_stereo_.data(), clamped_in, out_stereo, max_out_frames, drift_adjust);
    }

    size_t resample_stereo(
        const float* in_stereo,
        size_t in_frames,
        float* out_stereo,
        size_t max_out_frames,
        double drift_adjust = 1.0) noexcept
    {
        if (in_frames == 0 || max_out_frames == 0) return 0;

        if (src_rate_ == dst_rate_ && std::abs(drift_adjust - 1.0) < 1e-6 && std::abs(phase_) < 1e-4) {
            phase_ = 0.0;
            const size_t n = std::min(in_frames, max_out_frames);
            std::memcpy(out_stereo, in_stereo, n * 2 * sizeof(float));
            update_history(in_stereo, n);
            return n;
        }

        const double step = base_ratio_ * drift_adjust;
        if (!has_prev_) {
            init_history_from_first_frame(in_stereo[0], in_stereo[1]);
        }

        size_t out_count = 0;
        double pos = phase_;
        const bool use_sinc = (src_rate_ != dst_rate_);
        const double lookahead = use_sinc ? 4.0 : 2.0;
        const double max_pos = (static_cast<double>(in_frames) > lookahead)
            ? (static_cast<double>(in_frames) - lookahead)
            : 0.0;
        const float cutoff = use_sinc
            ? ((step > 1.0) ? static_cast<float>(0.95 / step) : 0.95f)
            : 1.0f;

        while (out_count < max_out_frames && pos < max_pos) {
            const int64_t idx = static_cast<int64_t>(std::floor(pos));
            const double frac = pos - static_cast<double>(idx);
            float ol = 0.0f;
            float or_sample = 0.0f;

            if (use_sinc) {
                interpolate_sinc(in_stereo, in_frames, idx, static_cast<float>(frac), cutoff, ol, or_sample);
            } else {
                interpolate_cubic(in_stereo, in_frames, idx, static_cast<float>(frac), ol, or_sample);
            }

            out_stereo[out_count * 2] = ol;
            out_stereo[out_count * 2 + 1] = or_sample;
            ++out_count;
            pos += step;
        }

        update_history(in_stereo, in_frames);
        phase_ = pos - static_cast<double>(in_frames);
        return out_count;
    }

    size_t frames_needed_for_exact_out(size_t out_frames, double drift_adjust = 1.0) const noexcept {
        if (out_frames == 0) return 0;
        if (src_rate_ == dst_rate_ && std::abs(drift_adjust - 1.0) < 1e-6 && std::abs(phase_) < 1e-4) {
            return out_frames;
        }
        const double step = base_ratio_ * drift_adjust;
        const double last_pos = phase_ + static_cast<double>(out_frames - 1) * step;
        const int64_t last_idx = static_cast<int64_t>(std::floor(last_pos));
        const size_t min_read = (last_idx >= 0) ? static_cast<size_t>(last_idx + kSincRadius + 1) : kSincRadius;
        const double end_pos = phase_ + static_cast<double>(out_frames) * step;
        const size_t consumed = (end_pos > 0.0) ? static_cast<size_t>(std::floor(end_pos)) : 0;
        return std::max(min_read, consumed);
    }

    size_t resample_exact_out(
        const float* in_stereo,
        size_t in_frames,
        float* out_stereo,
        size_t out_frames,
        double drift_adjust = 1.0) noexcept
    {
        if (out_frames == 0 || in_frames == 0) return 0;
        if (src_rate_ == dst_rate_ && std::abs(drift_adjust - 1.0) < 1e-6 && std::abs(phase_) < 1e-4) {
            phase_ = 0.0;
            const size_t n = std::min(in_frames, out_frames);
            std::memcpy(out_stereo, in_stereo, n * 2 * sizeof(float));
            update_history(in_stereo, n);
            return n;
        }

        const double step = base_ratio_ * drift_adjust;
        if (!has_prev_) {
            init_history_from_first_frame(in_stereo[0], in_stereo[1]);
        }

        const bool use_sinc = (src_rate_ != dst_rate_);
        const float cutoff = use_sinc
            ? ((step > 1.0) ? static_cast<float>(0.95 / step) : 0.95f)
            : 1.0f;

        double pos = phase_;
        for (size_t k = 0; k < out_frames; ++k) {
            const int64_t idx = static_cast<int64_t>(std::floor(pos));
            const double frac = pos - static_cast<double>(idx);
            float ol = 0.0f;
            float or_sample = 0.0f;

            if (use_sinc) {
                interpolate_sinc(in_stereo, in_frames, idx, static_cast<float>(frac), cutoff, ol, or_sample);
            } else {
                interpolate_cubic(in_stereo, in_frames, idx, static_cast<float>(frac), ol, or_sample);
            }

            out_stereo[k * 2] = ol;
            out_stereo[k * 2 + 1] = or_sample;
            pos += step;
        }

        const size_t consumed = (pos > 0.0)
            ? std::min(in_frames, static_cast<size_t>(std::floor(pos)))
            : 0;
        if (consumed > 0) {
            update_history(in_stereo, consumed);
        }
        phase_ = pos - static_cast<double>(consumed);
        return consumed;
    }

    static void convert_to_stereo_float(
        const uint8_t* raw,
        size_t frames,
        uint16_t channels,
        uint16_t bits,
        bool is_float,
        float* out_stereo) noexcept
    {
        const size_t bytes_per_sample = bits / 8;
        const size_t frame_stride = bytes_per_sample * channels;

        for (size_t f = 0; f < frames; ++f) {
            const uint8_t* fp = raw + f * frame_stride;
            float l = 0.0f;
            float r = 0.0f;
            if (is_float && bits == 32) {
                const float* s = reinterpret_cast<const float*>(fp);
                l = s[0];
                r = (channels >= 2) ? s[1] : s[0];
            } else if (!is_float && bits == 16) {
                const int16_t* s = reinterpret_cast<const int16_t*>(fp);
                constexpr float inv16 = 1.0f / 32768.0f;
                l = static_cast<float>(s[0]) * inv16;
                r = (channels >= 2) ? static_cast<float>(s[1]) * inv16 : l;
            } else if (!is_float && bits == 32) {
                const int32_t* s = reinterpret_cast<const int32_t*>(fp);
                constexpr float inv32 = 1.0f / 2147483648.0f;
                l = static_cast<float>(s[0]) * inv32;
                r = (channels >= 2) ? static_cast<float>(s[1]) * inv32 : l;
            } else if (!is_float && bits == 24) {
                auto read24 = [](const uint8_t* p) -> float {
                    int32_t v = (static_cast<int32_t>(p[0]) << 8) |
                                (static_cast<int32_t>(p[1]) << 16) |
                                (static_cast<int32_t>(p[2]) << 24);
                    return static_cast<float>(v >> 8) / 8388608.0f;
                };
                l = read24(fp);
                r = (channels >= 2) ? read24(fp + 3) : l;
            }
            out_stereo[f * 2] = l;
            out_stereo[f * 2 + 1] = r;
        }
    }

    static void convert_stereo_float_to_raw(
        const float* in_stereo,
        size_t frames,
        uint16_t dst_channels,
        uint16_t bits,
        bool is_float,
        uint8_t* out_raw) noexcept
    {
        const size_t bytes_per_sample = bits / 8;
        const size_t frame_stride = bytes_per_sample * dst_channels;
        std::memset(out_raw, 0, frames * frame_stride);

        for (size_t f = 0; f < frames; ++f) {
            const float l = std::clamp(in_stereo[f * 2], -1.0f, 1.0f);
            const float r = std::clamp(in_stereo[f * 2 + 1], -1.0f, 1.0f);
            uint8_t* fp = out_raw + f * frame_stride;

            if (is_float && bits == 32) {
                float* d = reinterpret_cast<float*>(fp);
                d[0] = (dst_channels == 1) ? 0.5f * (l + r) : l;
                if (dst_channels >= 2) d[1] = r;
            } else if (!is_float && bits == 16) {
                int16_t* d = reinterpret_cast<int16_t*>(fp);
                auto f2i16 = [](float x) -> int16_t {
                    return static_cast<int16_t>(std::clamp(std::lrintf(x * 32767.0f), -32768L, 32767L));
                };
                d[0] = (dst_channels == 1) ? f2i16(0.5f * (l + r)) : f2i16(l);
                if (dst_channels >= 2) d[1] = f2i16(r);
            } else if (!is_float && bits == 32) {
                int32_t* d = reinterpret_cast<int32_t*>(fp);
                auto f2i32 = [](float x) -> int32_t {
                    return static_cast<int32_t>(std::clamp(x * 2147483647.0, -2147483648.0, 2147483647.0));
                };
                d[0] = (dst_channels == 1) ? f2i32(0.5f * (l + r)) : f2i32(l);
                if (dst_channels >= 2) d[1] = f2i32(r);
            } else if (!is_float && bits == 24) {
                auto write24 = [](uint8_t* p, float x) {
                    int32_t v = static_cast<int32_t>(std::clamp(std::lrintf(x * 8388607.0f), -8388608L, 8388607L));
                    p[0] = static_cast<uint8_t>(v & 0xFF);
                    p[1] = static_cast<uint8_t>((v >> 8) & 0xFF);
                    p[2] = static_cast<uint8_t>((v >> 16) & 0xFF);
                };
                write24(fp, (dst_channels == 1) ? 0.5f * (l + r) : l);
                if (dst_channels >= 2) write24(fp + 3, r);
            }
        }
    }

private:
    static inline float sinc(float x) noexcept {
        if (std::abs(x) < 1e-6f) return 1.0f;
        constexpr float pi = 3.14159265358979323846f;
        const float px = pi * x;
        return std::sin(px) / px;
    }

    static inline float blackman(float x, float radius) noexcept {
        const float t = x / radius;
        if (std::abs(t) >= 1.0f) return 0.0f;
        constexpr float pi = 3.14159265358979323846f;
        return 0.42f + 0.5f * std::cos(pi * t) + 0.08f * std::cos(2.0f * pi * t);
    }

    static inline float hermite_cubic(float ym1, float y0, float y1, float y2, float t) noexcept {
        const float c0 = y0;
        const float c1 = 0.5f * (y1 - ym1);
        const float c2 = ym1 - 2.5f * y0 + 2.0f * y1 - 0.5f * y2;
        const float c3 = 0.5f * (y2 - ym1) + 1.5f * (y0 - y1);
        return ((c3 * t + c2) * t + c1) * t + c0;
    }

    void init_history_from_first_frame(float l, float r) noexcept {
        for (size_t i = 0; i < kSincRadius; ++i) {
            prev_l_[i] = l;
            prev_r_[i] = r;
        }
        has_prev_ = true;
    }

    void update_history(const float* in_stereo, size_t count) noexcept {
        if (count == 0) return;
        if (count >= kSincRadius) {
            for (size_t i = 0; i < kSincRadius; ++i) {
                const size_t src_idx = count - kSincRadius + i;
                prev_l_[i] = in_stereo[src_idx * 2];
                prev_r_[i] = in_stereo[src_idx * 2 + 1];
            }
        } else {
            const size_t shift = kSincRadius - count;
            for (size_t i = 0; i < shift; ++i) {
                prev_l_[i] = prev_l_[i + count];
                prev_r_[i] = prev_r_[i + count];
            }
            for (size_t i = 0; i < count; ++i) {
                prev_l_[shift + i] = in_stereo[i * 2];
                prev_r_[shift + i] = in_stereo[i * 2 + 1];
            }
        }
        has_prev_ = true;
    }

    inline void get_sample(
        const float* in_stereo,
        size_t in_frames,
        int64_t idx,
        float& out_l,
        float& out_r) const noexcept
    {
        if (idx < 0) {
            const int64_t h_idx = static_cast<int64_t>(kSincRadius) + idx;
            const size_t safe_h = (h_idx >= 0) ? static_cast<size_t>(h_idx) : 0;
            out_l = prev_l_[std::min(safe_h, kSincRadius - 1)];
            out_r = prev_r_[std::min(safe_h, kSincRadius - 1)];
        } else if (idx >= static_cast<int64_t>(in_frames)) {
            const size_t last = in_frames - 1;
            out_l = in_stereo[last * 2];
            out_r = in_stereo[last * 2 + 1];
        } else {
            const size_t u_idx = static_cast<size_t>(idx);
            out_l = in_stereo[u_idx * 2];
            out_r = in_stereo[u_idx * 2 + 1];
        }
    }

    inline void interpolate_cubic(
        const float* in_stereo,
        size_t in_frames,
        int64_t idx,
        float frac,
        float& out_l,
        float& out_r) const noexcept
    {
        float lm1, rm1, l0, r0, l1, r1, l2, r2;
        get_sample(in_stereo, in_frames, idx - 1, lm1, rm1);
        get_sample(in_stereo, in_frames, idx, l0, r0);
        get_sample(in_stereo, in_frames, idx + 1, l1, r1);
        get_sample(in_stereo, in_frames, idx + 2, l2, r2);
        out_l = hermite_cubic(lm1, l0, l1, l2, frac);
        out_r = hermite_cubic(rm1, r0, r1, r2, frac);
    }

    inline void interpolate_sinc(
        const float* in_stereo,
        size_t in_frames,
        int64_t idx,
        float frac,
        float cutoff,
        float& out_l,
        float& out_r) const noexcept
    {
        float sum_l = 0.0f;
        float sum_r = 0.0f;
        float weight_sum = 0.0f;

        for (int j = -3; j <= 4; ++j) {
            const float x = static_cast<float>(j) - frac;
            const float w = sinc(x * cutoff) * blackman(x, 4.0f) * cutoff;
            float sl, sr;
            get_sample(in_stereo, in_frames, idx + j, sl, sr);
            sum_l += w * sl;
            sum_r += w * sr;
            weight_sum += w;
        }

        if (std::abs(weight_sum) > 1e-6f) {
            const float inv_w = 1.0f / weight_sum;
            out_l = sum_l * inv_w;
            out_r = sum_r * inv_w;
        } else {
            get_sample(in_stereo, in_frames, idx, out_l, out_r);
        }
    }

    uint32_t src_rate_{48000};
    uint32_t dst_rate_{48000};
    double base_ratio_{1.0};
    double phase_{0.0};
    float prev_l_[kSincRadius]{0.0f};
    float prev_r_[kSincRadius]{0.0f};
    bool has_prev_{false};
    std::vector<float> scratch_stereo_;
};

} // namespace af

namespace phonoryx {
    using af::StereoConverterResampler;
}



