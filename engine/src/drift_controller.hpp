#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>

namespace af {

/**
 * Shared Proportional Buffer Drift Controller.
 *
 * Regulates audio ring buffer fill between independent hardware clocks or software
 * cadence domains by computing fine-grained drift adjustment ratios (~1.0 +/- 500 ppm).
 *
 * When current fill is within [target - deadband, target + deadband], returns exactly 1.0,
 * avoiding unnecessary resampling jitter and drift hunting. Outside the deadband,
 * applies proportional correction clamped to [-max_drift, +max_drift].
 */
class BufferDriftController {
public:
    constexpr BufferDriftController() noexcept = default;
    constexpr BufferDriftController(
        size_t target_fill,
        size_t deadband,
        double gain = 2e-7,
        double max_drift = 0.0005) noexcept
        : target_fill_(target_fill)
        , deadband_(deadband)
        , gain_(gain)
        , max_drift_(max_drift)
    {}

    void configure(size_t target_fill, size_t deadband, double gain = 2e-7, double max_drift = 0.0005) noexcept {
        target_fill_ = target_fill;
        deadband_ = deadband;
        gain_ = gain;
        max_drift_ = max_drift;
    }

    size_t target_fill() const noexcept { return target_fill_; }
    size_t deadband() const noexcept { return deadband_; }
    double gain() const noexcept { return gain_; }
    double max_drift() const noexcept { return max_drift_; }

    /**
     * Computes the drift ratio factor (centered at 1.0) given current ring buffer fill.
     * When fill is within [target - deadband, target + deadband], returns exactly 1.0.
     * When fill drifts outside deadband, applies proportional trim clamped to [-max_drift, +max_drift].
     */
    double compute_adjustment(size_t current_fill) const noexcept {
        const size_t low_thresh = (target_fill_ > deadband_) ? (target_fill_ - deadband_) : 0;
        const size_t high_thresh = target_fill_ + deadband_;

        if (current_fill >= low_thresh && current_fill <= high_thresh) {
            return 1.0;
        }

        const double err = static_cast<double>(current_fill) - static_cast<double>(target_fill_);
        const double trim = std::clamp(err * gain_, -max_drift_, max_drift_);
        return 1.0 + trim;
    }

private:
    size_t target_fill_{480};
    size_t deadband_{192};
    double gain_{2e-7};
    double max_drift_{0.0005}; // +/- 500 ppm maximum trim
};

} // namespace af

namespace phonoryx {
    using af::BufferDriftController;
}
