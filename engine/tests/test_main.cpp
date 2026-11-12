#include "phonoryx_capi.h"
#include "audio_graph.hpp"
#include "device_manager.hpp"
#include "process_manager.hpp"
#include "resampler.hpp"
#include "ring_buffer.hpp"

#include <cassert>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <vector>

namespace {

int g_failures = 0;

#define CHECK(cond, msg)                                                                 \
    do {                                                                                 \
        if (!(cond)) {                                                                   \
            std::fprintf(stderr, "[FAIL] %s:%d: %s\n", __FILE__, __LINE__, (msg));       \
            ++g_failures;                                                                \
        }                                                                                \
    } while (0)

void test_ring_buffer_and_resampler() {
    af::StereoRingBuffer rb(256);
    std::vector<float> in(100 * 2, 0.25f);
    std::vector<float> out(100 * 2, 0.0f);

    CHECK(rb.write(in.data(), 100) == 100, "ring write 100 frames");
    CHECK(rb.available_read() == 100, "ring avail 100");
    CHECK(rb.read(out.data(), 100) == 100, "ring read 100 frames");
    CHECK(std::abs(out[0] - 0.25f) < 1e-6f && std::abs(out[199] - 0.25f) < 1e-6f, "ring samples match");

    af::StereoConverterResampler res;
    res.configure(44100, 48000, 2048);
    std::vector<int16_t> pcm16(441 * 2, 16384); // ~0.5 amplitude
    std::vector<float> rs_out(1024 * 2, 0.0f);
    size_t n_out = res.process_raw_to_stereo(
        reinterpret_cast<const uint8_t*>(pcm16.data()), 441, 2, 16, false,
        rs_out.data(), 1024, 1.0);
    CHECK(n_out >= 470 && n_out <= 485, "44.1k->48k resampler frame count");
    CHECK(std::abs(rs_out[10] - 0.5f) < 0.02f, "int16->float32 resampled amplitude");
    std::printf("[PASS] test_ring_buffer_and_resampler\n");
}

void test_splitting_and_independent_volume() {
    af::AudioEngine engine(48000, 128);
    const char* graph = R"JSON({
      "nodes": [
        {"id": 1, "type": 1, "device_id": "test://src1", "pid": 100},
        {"id": 2, "type": 104},
        {"id": 3, "type": 101, "volume": 1.0},
        {"id": 4, "type": 101, "volume": 0.3},
        {"id": 5, "type": 202, "device_id": "test://out1"},
        {"id": 6, "type": 204, "device_id": "test://out2"}
      ],
      "connections": [
        {"src_node": 1, "src_port": 0, "dst_node": 2, "dst_port": 0},
        {"src_node": 2, "src_port": 0, "dst_node": 3, "dst_port": 0},
        {"src_node": 2, "src_port": 1, "dst_node": 4, "dst_port": 0},
        {"src_node": 3, "src_port": 0, "dst_node": 5, "dst_port": 0},
        {"src_node": 4, "src_port": 0, "dst_node": 6, "dst_port": 0}
      ]
    })JSON";

    std::string err;
    CHECK(engine.apply_graph_json(graph, err), ("apply split graph: " + err).c_str());

    std::vector<float> src(128 * 2, 0.8f);
    CHECK(engine.test_push_source_audio(1, src.data(), 128), "push source frames");
    engine.process_one_tick_for_test();

    std::vector<float> out1(128 * 2, 0.0f);
    std::vector<float> out2(128 * 2, 0.0f);
    CHECK(engine.test_pop_sink_audio(5, out1.data(), 128) == 128, "pop out1 128 frames");
    CHECK(engine.test_pop_sink_audio(6, out2.data(), 128) == 128, "pop out2 128 frames");

    CHECK(std::abs(out1[0] - 0.8f) < 1e-4f, "Branch 1 at 100% stays 0.8");
    CHECK(std::abs(out2[0] - 0.24f) < 1e-4f, "Branch 2 at 30% equals 0.24");

    // Mutate Branch 2 volume to 0.0 and verify Branch 1 stays untouched
    CHECK(engine.set_node_float_param(4, "volume", 0.0f), "set branch 2 volume 0");
    CHECK(engine.test_push_source_audio(1, src.data(), 128), "push source block 2");
    engine.process_one_tick_for_test();
    CHECK(engine.test_pop_sink_audio(5, out1.data(), 128) == 128, "pop out1 block 2");
    CHECK(engine.test_pop_sink_audio(6, out2.data(), 128) == 128, "pop out2 block 2");
    CHECK(std::abs(out1[255] - 0.8f) < 1e-4f, "Branch 1 unaffected by Branch 2 change");
    CHECK(std::abs(out2[255] - 0.0f) < 1e-4f, "Branch 2 ramped to 0");
    std::printf("[PASS] test_splitting_and_independent_volume\n");
}

void test_mixer_multiple_sources() {
    af::AudioEngine engine(48000, 128);
    const char* graph = R"JSON({
      "nodes": [
        {"id": 1, "type": 1, "device_id": "test://a"},
        {"id": 2, "type": 3, "device_id": "test://b"},
        {"id": 3, "type": 103},
        {"id": 4, "type": 202, "device_id": "test://hp"}
      ],
      "connections": [
        {"src_node": 1, "src_port": 0, "dst_node": 3, "dst_port": 0},
        {"src_node": 2, "src_port": 0, "dst_node": 3, "dst_port": 1},
        {"src_node": 3, "src_port": 0, "dst_node": 4, "dst_port": 0}
      ]
    })JSON";
    std::string err;
    CHECK(engine.apply_graph_json(graph, err), ("apply mixer graph: " + err).c_str());

    std::vector<float> a(128 * 2, 0.25f);
    std::vector<float> b(128 * 2, 0.35f);
    CHECK(engine.test_push_source_audio(1, a.data(), 128), "push src A");
    CHECK(engine.test_push_source_audio(2, b.data(), 128), "push src B");
    engine.process_one_tick_for_test();

    std::vector<float> out(128 * 2, 0.0f);
    CHECK(engine.test_pop_sink_audio(4, out.data(), 128) == 128, "pop mixed out");
    CHECK(std::abs(out[0] - 0.60f) < 1e-4f, "Mixer sums 0.25 + 0.35 = 0.60");
    std::printf("[PASS] test_mixer_multiple_sources\n");
}

void test_cycle_and_validation_rejection() {
    af::AudioEngine engine(48000, 128);
    const char* cyclic = R"JSON({
      "nodes": [
        {"id": 1, "type": 101},
        {"id": 2, "type": 102}
      ],
      "connections": [
        {"src_node": 1, "src_port": 0, "dst_node": 2, "dst_port": 0},
        {"src_node": 2, "src_port": 0, "dst_node": 1, "dst_port": 0}
      ]
    })JSON";
    std::string err;
    CHECK(!engine.apply_graph_json(cyclic, err), "cyclic graph must be rejected");

    const char* bad_multi_in = R"JSON({
      "nodes": [
        {"id": 1, "type": 1, "device_id": "test://a"},
        {"id": 2, "type": 3, "device_id": "test://b"},
        {"id": 3, "type": 101}
      ],
      "connections": [
        {"src_node": 1, "src_port": 0, "dst_node": 3, "dst_port": 0},
        {"src_node": 2, "src_port": 0, "dst_node": 3, "dst_port": 0}
      ]
    })JSON";
    CHECK(!engine.apply_graph_json(bad_multi_in, err), "non-mixer multi-input must be rejected");
    std::printf("[PASS] test_cycle_and_validation_rejection\n");
}

void test_dynamic_routing_preserves_active_route_and_wav() {
    auto wav_path = std::filesystem::temp_directory_path() / "phonoryx_test_rec.wav";
    std::filesystem::remove(wav_path);

    af::AudioEngine engine(48000, 128);
    std::string g1 = std::string(R"JSON({
      "nodes": [
        {"id": 10, "type": 1, "device_id": "test://active_src"},
        {"id": 20, "type": 202, "device_id": "test://active_out"}
      ],
      "connections": [
        {"src_node": 10, "src_port": 0, "dst_node": 20, "dst_port": 0}
      ]
    })JSON");
    std::string err;
    CHECK(engine.apply_graph_json(g1.c_str(), err), ("apply g1: " + err).c_str());

    // Preload 256 frames on Route A, process 128 frames, then live-add Route B (including WAV recorder)
    std::vector<float> sig(256 * 2, 0.42f);
    CHECK(engine.test_push_source_audio(10, sig.data(), 256), "push 256 frames to active route");
    engine.process_one_tick_for_test();

    std::string wav_esc = wav_path.string();
    for (size_t pos = 0; (pos = wav_esc.find('\\', pos)) != std::string::npos; pos += 2) {
        wav_esc.replace(pos, 1, "\\\\");
    }
    std::string g2 = std::string(R"JSON({
      "nodes": [
        {"id": 10, "type": 1, "device_id": "test://active_src"},
        {"id": 20, "type": 202, "device_id": "test://active_out"},
        {"id": 30, "type": 3, "device_id": "test://new_src"},
        {"id": 31, "type": 110, "eq_gains_db": [3.0, 0.0, 0.0, 0.0, 0.0]},
        {"id": 32, "type": 205, "file_path": ")JSON") + wav_esc + R"JSON("}
      ],
      "connections": [
        {"src_node": 10, "src_port": 0, "dst_node": 20, "dst_port": 0},
        {"src_node": 30, "src_port": 0, "dst_node": 31, "dst_port": 0},
        {"src_node": 31, "src_port": 0, "dst_node": 32, "dst_port": 0}
      ]
    })JSON";
    CHECK(engine.apply_graph_json(g2.c_str(), err), ("apply g2: " + err).c_str());

    std::vector<float> sig2(128 * 2, 0.5f);
    CHECK(engine.test_push_source_audio(30, sig2.data(), 128), "push frames to new route");
    engine.process_one_tick_for_test();

    std::vector<float> out_a(256 * 2, 0.0f);
    CHECK(engine.test_pop_sink_audio(20, out_a.data(), 256) == 256, "Route A continued without dropping frames");
    CHECK(std::abs(out_a[255] - 0.42f) < 1e-4f, "Route A second block intact after live swap");

    Sleep(30); // Let WAV writer flush
    // Re-apply empty graph to close WAV file cleanly
    CHECK(engine.apply_graph_json("{\"nodes\":[],\"connections\":[]}", err), "clear graph");
    CHECK(std::filesystem::exists(wav_path), "WAV output file created");
    CHECK(std::filesystem::file_size(wav_path) >= 44 + 128 * 2 * sizeof(float), "WAV file has audio payload");
    std::filesystem::remove(wav_path);
    std::printf("[PASS] test_dynamic_routing_preserves_active_route_and_wav\n");
}

void test_windows_device_and_process_discovery() {
    af_engine_t* eng = af_engine_create(48000, 480);
    CHECK(eng != nullptr, "af_engine_create");

    std::vector<af_device_info_t> devs(64);
    int32_t dev_n = af_enumerate_devices(eng, devs.data(), 64);
    std::printf("  Enumerated %d Windows audio endpoints\n", dev_n);
    CHECK(dev_n >= 0, "device enumeration succeeded");

    std::vector<af_process_info_t> procs(512);
    int32_t proc_n = af_enumerate_processes(eng, procs.data(), 512);
    std::printf("  Enumerated %d Windows processes\n", proc_n);
    CHECK(proc_n > 0, "process enumeration returned running processes");

    af_engine_stats_t stats{};
    CHECK(af_get_engine_stats(eng, &stats) == 0, "af_get_engine_stats");
    CHECK(stats.sample_rate == 48000, "engine sample rate 48000");

    af_engine_destroy(eng);
    std::printf("[PASS] test_windows_device_and_process_discovery\n");
}

} // namespace

int main() {
    test_ring_buffer_and_resampler();
    test_splitting_and_independent_volume();
    test_mixer_multiple_sources();
    test_cycle_and_validation_rejection();
    test_dynamic_routing_preserves_active_route_and_wav();
    test_windows_device_and_process_discovery();

    if (g_failures > 0) {
        std::fprintf(stderr, "Phonoryx C++ tests FAILED (%d failures)\n", g_failures);
        return 1;
    }
    std::printf("All Phonoryx C++ engine tests PASSED!\n");
    return 0;
}

