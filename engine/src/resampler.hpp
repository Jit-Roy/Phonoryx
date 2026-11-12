#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <vector>

namespace af {

/**
 * Preallocated linear/drift-adaptive stereo resampler and PCM format converter.
 * Converts arbitrary Windows WASAPI wave formats (int16, int24, int32, float32; 1..8 channels)
 * into stereo float32 and resamples between source and destination sample rates.
 */
class StereoConverterResampler {
public:
    void configure(uint32_t src_rate, uint32_t dst_rate, size_t max_in_frames = 8192) {
        src_rate_ = src_rate > 0 ? src_rate : 48000;
        dst_rate_ = dst_rate > 0 ? dst_rate : 48000;
        base_ratio_ = static_cast<double>(src_rate_) / static_cast<double>(dst_rate_);
        phase_ = 0.0;
        prev_l_ = 0.0f;
        prev_r_ = 0.0f;
        has_prev_ = false;
        scratch_stereo_.assign(max_in_frames * 2, 0.0f);
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

        if (src_rate_ == dst_rate_ && std::abs(drift_adjust - 1.0) < 1e-6) {
            const size_t n = std::min(in_frames, max_out_frames);
            std::memcpy(out_stereo, in_stereo, n * 2 * sizeof(float));
            prev_l_ = in_stereo[(n - 1) * 2];
            prev_r_ = in_stereo[(n - 1) * 2 + 1];
            has_prev_ = true;
            return n;
        }

        const double step = base_ratio_ * drift_adjust;
        if (!has_prev_) {
            prev_l_ = in_stereo[0];
            prev_r_ = in_stereo[1];
            has_prev_ = true;
        }

        size_t out_count = 0;
        double pos = phase_;
        while (out_count < max_out_frames) {
            const int64_t idx = static_cast<int64_t>(std::floor(pos));
            if (idx >= static_cast<int64_t>(in_frames) - 1) {
                break;
            }
            const double frac = pos - static_cast<double>(idx);
            float l0, r0, l1, r1;
            if (idx < 0) {
                l0 = prev_l_;
                r0 = prev_r_;
                l1 = in_stereo[0];
                r1 = in_stereo[1];
            } else {
                const size_t i0 = static_cast<size_t>(idx);
                l0 = in_stereo[i0 * 2];
                r0 = in_stereo[i0 * 2 + 1];
                l1 = in_stereo[(i0 + 1) * 2];
                r1 = in_stereo[(i0 + 1) * 2 + 1];
            }
            out_stereo[out_count * 2] = static_cast<float>(l0 + (l1 - l0) * frac);
            out_stereo[out_count * 2 + 1] = static_cast<float>(r0 + (r1 - r0) * frac);
            ++out_count;
            pos += step;
        }

        phase_ = pos - static_cast<double>(in_frames);
        if (phase_ < -1.0) phase_ = -1.0;
        if (phase_ > 0.0) phase_ = 0.0;
        prev_l_ = in_stereo[(in_frames - 1) * 2];
        prev_r_ = in_stereo[(in_frames - 1) * 2 + 1];
        return out_count;
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
    uint32_t src_rate_{48000};
    uint32_t dst_rate_{48000};
    double base_ratio_{1.0};
    double phase_{0.0};
    float prev_l_{0.0f};
    float prev_r_{0.0f};
    bool has_prev_{false};
    std::vector<float> scratch_stereo_;
};

} // namespace af

