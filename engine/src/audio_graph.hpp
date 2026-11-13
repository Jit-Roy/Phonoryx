#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include "phonoryx_capi.h"
#include "device_manager.hpp"
#include "dsp_nodes.hpp"
#include "ring_buffer.hpp"
#include "wasapi_endpoint.hpp"

namespace af {

struct GraphNodeSpec {
    uint64_t id{0};
    int32_t type{0};
    std::string device_id;
    uint32_t pid{0};
    std::string file_path;
    float volume{1.0f};
    float gain_db{0.0f};
    bool muted{false};
    int32_t chan_map_l{0};
    int32_t chan_map_r{1};
    std::array<float, 5> eq_gains_db{0.0f, 0.0f, 0.0f, 0.0f, 0.0f};
};

struct GraphConnectionSpec {
    uint64_t src_node{0};
    uint32_t src_port{0};
    uint64_t dst_node{0};
    uint32_t dst_port{0};
};

struct CompiledNode {
    uint64_t id{0};
    int32_t type{0};
    std::shared_ptr<NodeRuntimeParams> params;
    std::shared_ptr<WasapiEndpointWorker> endpoint;
    std::shared_ptr<WavFileRecorder> recorder;

    // DSP state owned exclusively by RT thread for this snapshot
    float current_gain{1.0f};
    FiveBandEq eq;

    // Preallocated indices into snapshot->edge_buffers
    std::vector<size_t> in_edges;
    std::vector<size_t> out_edges;

    // Optional test injection / capture ring for deterministic unit tests
    std::shared_ptr<StereoRingBuffer> test_in_ring;
    std::shared_ptr<StereoRingBuffer> test_out_ring;
};

struct CompiledGraphSnapshot {
    uint32_t sample_rate{48000};
    uint32_t block_frames{480};
    std::vector<CompiledNode> topo_nodes;
    std::vector<std::vector<float>> edge_buffers;
    std::vector<float> scratch_mix;
    uint32_t connection_count{0};
};

/**
 * Master real-time Phonoryx engine & graph manager.
 */
class AudioEngine {
public:
    AudioEngine(uint32_t sample_rate, uint32_t block_frames);
    ~AudioEngine();

    bool start();
    void stop();

    DeviceManager& devices() noexcept { return device_mgr_; }
    LogQueue& logs() noexcept { return logs_; }

    bool apply_graph_json(const char* json_text, std::string& err_out);
    bool set_node_float_param(uint64_t node_id, const char* name, float value);
    bool set_node_int_param(uint64_t node_id, const char* name, int32_t value);

    int32_t get_node_meters(af_node_meter_t* out, int32_t max_count);
    void get_stats(af_engine_stats_t* out);

    // Slaved Primary Output Clocking telemetry & inspection
    bool is_master_clock_active() const noexcept {
        return master_clock_active_.load(std::memory_order_acquire);
    }
    uint64_t master_render_node_id() const noexcept {
        return master_render_node_id_;
    }
    HANDLE master_tick_event() const noexcept {
        return master_tick_event_;
    }

    // Deterministic test helpers (also exercises exact RT execution path)
    void process_single_block(CompiledGraphSnapshot* snap);
    bool test_push_source_audio(uint64_t node_id, const float* stereo_in, size_t frames);
    size_t test_pop_sink_audio(uint64_t node_id, float* stereo_out, size_t frames);
    void process_one_tick_for_test();

private:
    struct ManagedNodeResources {
        GraphNodeSpec spec;
        std::shared_ptr<NodeRuntimeParams> params;
        std::shared_ptr<WasapiEndpointWorker> endpoint;
        std::shared_ptr<WavFileRecorder> recorder;
        std::shared_ptr<StereoRingBuffer> test_in_ring;
        std::shared_ptr<StereoRingBuffer> test_out_ring;
    };

    void rt_loop();
    void retire_old_snapshots();
    static bool is_source_node(int32_t type) noexcept;
    static bool is_output_node(int32_t type) noexcept;
    static bool parse_graph_json(
        const char* json_text,
        std::vector<GraphNodeSpec>& nodes,
        std::vector<GraphConnectionSpec>& conns,
        std::string& err);

    uint32_t sample_rate_{48000};
    uint32_t block_frames_{480};

    LogQueue logs_;
    DeviceManager device_mgr_;

    std::mutex ctrl_mutex_;
    std::unordered_map<uint64_t, ManagedNodeResources> resources_;
    std::atomic<CompiledGraphSnapshot*> active_snapshot_{nullptr};
    std::atomic<uint32_t> rt_in_flight_{0};
    std::vector<std::unique_ptr<CompiledGraphSnapshot>> retired_snapshots_;

    std::atomic<bool> running_{false};
    std::atomic<uint64_t> processed_blocks_{0};
    std::atomic<float> cpu_load_percent_{0.0f};
    std::thread rt_thread_;
    HANDLE stop_event_{nullptr};
    HANDLE master_tick_event_{nullptr};
    std::atomic<bool> master_clock_active_{false};
    uint64_t master_render_node_id_{0};
};

} // namespace af

namespace phonoryx {
    using af::AudioEngine;
    using af::GraphNodeSpec;
    using af::GraphConnectionSpec;
    using af::CompiledNode;
    using af::CompiledGraphSnapshot;
}


