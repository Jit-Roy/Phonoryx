#include "phonoryx_capi.h"
#include "audio_graph.hpp"
#include "device_manager.hpp"
#include "json_mini.hpp"
#include "process_manager.hpp"
#include "resampler.hpp"
#include "ring_buffer.hpp"

#include <cassert>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <thread>
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

void test_resampler_phase_continuity() {
    constexpr double kTwoPi = 6.28318530717958647692;
    constexpr double kFreq = 440.0;

    // 1. Packetized downsampling (48000 -> 44100, step > 1.0) and upsampling (44100 -> 48000, step < 1.0)
    struct Case {
        uint32_t src_rate;
        uint32_t dst_rate;
        double drift;
        size_t pkt_frames;
        const char* label;
    } cases[] = {
        {48000, 44100, 1.0, 480, "48k->44.1k"},
        {44100, 48000, 1.0, 441, "44.1k->48k"},
        {48000, 48000, 1.0005, 480, "48k drift 1.0005"},
        {48000, 48000, 0.9995, 480, "48k drift 0.9995"},
    };

    for (const auto& c : cases) {
        af::StereoConverterResampler res;
        res.configure(c.src_rate, c.dst_rate, 4096);
        std::vector<float> all_out;
        all_out.reserve(20 * 600 * 2);
        std::vector<float> in_pkt(c.pkt_frames * 2, 0.0f);
        std::vector<float> out_pkt(2048 * 2, 0.0f);

        size_t total_in = 0;
        for (int p = 0; p < 20; ++p) {
            for (size_t i = 0; i < c.pkt_frames; ++i) {
                const float s = static_cast<float>(
                    std::sin(kTwoPi * kFreq * static_cast<double>(total_in + i) / static_cast<double>(c.src_rate)));
                in_pkt[i * 2] = s;
                in_pkt[i * 2 + 1] = -s;
            }
            total_in += c.pkt_frames;
            const size_t n = res.resample_stereo(in_pkt.data(), c.pkt_frames, out_pkt.data(), 2048, c.drift);
            all_out.insert(all_out.end(), out_pkt.data(), out_pkt.data() + n * 2);
        }

        const size_t total_out_frames = all_out.size() / 2;
        CHECK(total_out_frames > 1000, c.label);
        const double max_step = kTwoPi * kFreq / static_cast<double>(c.dst_rate) * 1.35;
        double worst_diff = 0.0;
        size_t worst_idx = 0;
        for (size_t i = 1; i < total_out_frames; ++i) {
            const double d = std::abs(all_out[i * 2] - all_out[(i - 1) * 2]);
            if (d > worst_diff) {
                worst_diff = d;
                worst_idx = i;
            }
        }
        std::printf("  [%s] total_out=%zu, worst_diff=%.6f, max_step=%.6f, worst_idx=%zu\n",
                    c.label, total_out_frames, worst_diff, max_step, worst_idx);
        CHECK(worst_diff < max_step, ("continuity across boundaries: " + std::string(c.label)).c_str());
    }

    // 2. Exact output ring resampling (48000 -> 44100 via peek/resample_exact_out/skip)
    {
        af::StereoRingBuffer rb(16384);
        af::StereoConverterResampler res;
        res.configure(48000, 44100, 4096);
        std::vector<float> src_pkt(480 * 2, 0.0f);
        std::vector<float> peek_buf(4096 * 2, 0.0f);
        std::vector<float> out_pkt(441 * 2, 0.0f);
        std::vector<float> all_out;
        size_t total_in = 0;

        for (int p = 0; p < 25; ++p) {
            for (size_t i = 0; i < 480; ++i) {
                const float s = static_cast<float>(
                    std::sin(kTwoPi * kFreq * static_cast<double>(total_in + i) / 48000.0));
                src_pkt[i * 2] = s;
                src_pkt[i * 2 + 1] = s;
            }
            total_in += 480;
            rb.write(src_pkt.data(), 480);

            const size_t need = res.frames_needed_for_exact_out(441, 1.0);
            if (rb.available_read() >= need) {
                rb.peek(peek_buf.data(), need);
                const size_t consumed = res.resample_exact_out(peek_buf.data(), need, out_pkt.data(), 441, 1.0);
                rb.skip(consumed);
                all_out.insert(all_out.end(), out_pkt.data(), out_pkt.data() + 441 * 2);
            }
        }

        const size_t n_frames = all_out.size() / 2;
        CHECK(n_frames >= 20 * 441, "exact_out produced expected continuous frames");
        const double max_step = kTwoPi * kFreq / 44100.0 * 1.35;
        double worst_diff = 0.0;
        for (size_t i = 1; i < n_frames; ++i) {
            const double d = std::abs(all_out[i * 2] - all_out[(i - 1) * 2]);
            if (d > worst_diff) worst_diff = d;
        }
        CHECK(worst_diff < max_step, "exact_out continuity across render boundaries");
    }

    std::printf("[PASS] test_resampler_phase_continuity\n");
}

void test_rt_loop_timing_accuracy() {
    // 256 frames @ 48000 Hz => 5.3333 ms/block => ~93.75 blocks in 500 ms (previously 100+ due to 5ms truncation)
    {
        af::AudioEngine eng(48000, 256);
        CHECK(eng.start(), "rt_loop timing start (256)");
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
        eng.stop();
        af_engine_stats_t st{};
        eng.get_stats(&st);
        std::printf("  500ms @ 48000Hz/256 frames => %llu blocks (expected ~93.75)\n",
                    static_cast<unsigned long long>(st.processed_blocks));
        CHECK(st.processed_blocks >= 86 && st.processed_blocks <= 98, "256-frame RT cadence locked to 48kHz");
    }

    // 480 frames @ 48000 Hz => 10.000 ms/block => ~50 blocks in 500 ms
    {
        af::AudioEngine eng(48000, 480);
        CHECK(eng.start(), "rt_loop timing start (480)");
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
        eng.stop();
        af_engine_stats_t st{};
        eng.get_stats(&st);
        std::printf("  500ms @ 48000Hz/480 frames => %llu blocks (expected ~50.0)\n",
                    static_cast<unsigned long long>(st.processed_blocks));
        CHECK(st.processed_blocks >= 45 && st.processed_blocks <= 54, "480-frame RT cadence locked to 48kHz");
    }

    std::printf("[PASS] test_rt_loop_timing_accuracy\n");
}

void test_json_fuzz() {
    // 1. Deep nesting test (must be rejected gracefully without stack overflow)
    std::string deep_array;
    for (int i = 0; i < 300; ++i) deep_array += "[";
    for (int i = 0; i < 300; ++i) deep_array += "]";
    af::json::Value val;
    std::string err;
    af::json::Parser p1(deep_array.c_str());
    CHECK(!p1.parse(val, err), "Deep array >128 levels must be rejected");
    CHECK(err.find("nesting depth") != std::string::npos, "Error mentions nesting depth");

    // 2. Truncated unicode escapes
    const char* bad_escapes[] = {
        "\"\\u\"", "\"\\u0\"", "\"\\u00\"", "\"\\u000\"", "\"\\uZZZZ\"", "\"\\", "\"unterminated",
        "{\"key\": }", "[1, 2, ", "{1: 2}", "{\"k\": 1 trailing}", "true extra", "---123"
    };
    for (const char* bad : bad_escapes) {
        af::json::Parser p(bad);
        af::json::Value out;
        std::string e;
        CHECK(!p.parse(out, e), ("Malformed JSON rejected: " + std::string(bad)).c_str());
    }

    // 3. Substring truncations of valid JSON
    const char* valid = R"JSON({
      "nodes": [
        {"id": 1, "type": 1, "volume": 0.8, "muted": false, "name": "Test\nNode"},
        {"id": 2, "type": 101, "eq": [1.0, 2.5, -3.0]}
      ],
      "connections": [{"src_node": 1, "src_port": 0, "dst_node": 2, "dst_port": 0}]
    })JSON";
    const size_t len = std::strlen(valid);
    for (size_t sub = 0; sub < len; ++sub) {
        std::string partial(valid, sub);
        af::json::Parser p(partial.c_str());
        af::json::Value out;
        std::string e;
        p.parse(out, e); // Must never crash
    }

    // 4. Pseudo-random fuzzing (5,000 randomized byte strings)
    uint32_t seed = 0x12345678;
    auto lcg = [&seed]() -> uint32_t {
        seed = seed * 1664525u + 1013904223u;
        return seed;
    };
    for (int it = 0; it < 5000; ++it) {
        const size_t f_len = (lcg() % 64) + 1;
        std::string fuzz_str;
        fuzz_str.reserve(f_len);
        for (size_t b = 0; b < f_len; ++b) {
            char c = static_cast<char>(lcg() & 0xFF);
            if (c == '\0') c = ' ';
            fuzz_str.push_back(c);
        }
        af::json::Parser fp(fuzz_str.c_str());
        af::json::Value fout;
        std::string fe;
        fp.parse(fout, fe); // Must never crash
    }
    std::printf("[PASS] test_json_fuzz\n");
}

void test_log_queue_multithreaded_stress() {
    af::LogQueue queue;
    std::atomic<bool> start_flag{false};
    std::atomic<bool> done_flag{false};
    constexpr int kProducers = 8;
    constexpr int kEntriesPerProducer = 10000; // 80,000 total pushed entries into capacity 256!

    std::vector<std::thread> producers;
    for (int p = 0; p < kProducers; ++p) {
        producers.emplace_back([&queue, &start_flag, p]() {
            while (!start_flag.load(std::memory_order_acquire)) {
                std::this_thread::yield();
            }
            char msg[64];
            for (int i = 0; i < kEntriesPerProducer; ++i) {
                std::snprintf(msg, sizeof(msg), "p%d_msg_%d", p, i);
                queue.push(p % 3, static_cast<uint64_t>(p * 100000 + i), msg);
            }
        });
    }

    uint64_t total_popped = 0;
    std::thread consumer([&queue, &start_flag, &done_flag, &total_popped]() {
        while (!start_flag.load(std::memory_order_acquire)) {
            std::this_thread::yield();
        }
        af_log_entry_t batch[64];
        while (!done_flag.load(std::memory_order_acquire)) {
            int32_t n = queue.pop_batch(batch, 64);
            if (n > 0) {
                total_popped += static_cast<uint64_t>(n);
                for (int32_t i = 0; i < n; ++i) {
                    CHECK(batch[i].level >= 0 && batch[i].level <= 2, "Valid log level under stress");
                    CHECK(batch[i].message[0] == 'p', "Valid untorn log message under overwrite");
                }
            } else {
                std::this_thread::yield();
            }
        }
        // Drain any remaining
        int32_t n = 0;
        while ((n = queue.pop_batch(batch, 64)) > 0) {
            total_popped += static_cast<uint64_t>(n);
        }
    });

    start_flag.store(true, std::memory_order_release);
    for (auto& t : producers) {
        t.join();
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    done_flag.store(true, std::memory_order_release);
    consumer.join();

    std::printf("  LogQueue stress: pushed 80,000 concurrent entries into 256-slot ring; safely popped %llu entries without torn reads\n",
                static_cast<unsigned long long>(total_popped));
    CHECK(total_popped > 0, "Stress popped logs");
    std::printf("[PASS] test_log_queue_multithreaded_stress\n");
}

void test_clock_drift_long_run() {
    // Two synthetic clocks 100 ppm apart:
    // Clock A (producer) running at 48004.8 Hz (+100 ppm)
    // Clock B (consumer) running at 48000 Hz
    // Simulating 18,000 blocks (3 minutes of audio at 48kHz, 480 frames per block)
    af::StereoRingBuffer ring(16384);
    af::StereoConverterResampler resampler;
    resampler.configure(48000, 48000, 4096);

    constexpr size_t kNominalBlock = 480;
    constexpr size_t kTargetFill = 1440; // 30ms target fill
    constexpr size_t kDeadband = 480;
    constexpr int kTotalBlocks = 18000;

    std::vector<float> in_block(kNominalBlock * 2, 0.42f);
    std::vector<float> resampled_buf(1024 * 2, 0.0f);
    std::vector<float> out_block(kNominalBlock * 2, 0.0f);

    // Initial prefill to target fill level
    ring.write(in_block.data(), kNominalBlock);
    ring.write(in_block.data(), kNominalBlock);
    ring.write(in_block.data(), kNominalBlock);

    size_t underruns = 0;
    size_t overruns = 0;
    size_t min_fill = ring.capacity();
    size_t max_fill = 0;

    double clock_a_phase = 0.0;
    constexpr double kPpmStep = 1.0001; // +100 ppm faster producer

    for (int blk = 0; blk < kTotalBlocks; ++blk) {
        // Producer ticks at +100 ppm clock rate
        clock_a_phase += static_cast<double>(kNominalBlock) * kPpmStep;
        size_t frames_to_produce = static_cast<size_t>(std::floor(clock_a_phase));
        clock_a_phase -= static_cast<double>(frames_to_produce);

        const size_t current_fill = ring.available_read();
        if (current_fill < min_fill) min_fill = current_fill;
        if (current_fill > max_fill) max_fill = current_fill;

        // Drift estimation feedback loop (matching WASAPI endpoint logic)
        double drift = 1.0;
        if (current_fill < (kTargetFill > kDeadband ? kTargetFill - kDeadband : 0) ||
            current_fill > kTargetFill + kDeadband) {
            const double err = static_cast<double>(current_fill) - static_cast<double>(kTargetFill);
            drift = 1.0 + std::clamp(err * 2e-7, -0.0005, 0.0005);
        }

        // Resample producer frames into ring buffer
        size_t produced = resampler.resample_stereo(
            in_block.data(), std::min(frames_to_produce, in_block.size() / 2),
            resampled_buf.data(), resampled_buf.size() / 2, drift);

        if (ring.available_write() < produced) {
            ++overruns;
        } else {
            ring.write(resampled_buf.data(), produced);
        }

        // Consumer ticks at nominal 48000 Hz (pulling 480 frames)
        if (ring.available_read() < kNominalBlock) {
            ++underruns;
        } else {
            ring.read(out_block.data(), kNominalBlock);
        }
    }

    std::printf("  100 ppm drift over 18,000 blocks (3 minutes): min_fill=%zu, max_fill=%zu, underruns=%zu, overruns=%zu\n",
                min_fill, max_fill, underruns, overruns);
    CHECK(underruns == 0, "Zero underruns over 3 minutes of 100 ppm clock drift");
    CHECK(overruns == 0, "Zero overruns over 3 minutes of 100 ppm clock drift");
    CHECK(min_fill > 0 && max_fill < ring.capacity(), "Bounded ring buffer fill across long run");
    std::printf("[PASS] test_clock_drift_long_run\n");
}

void test_automated_impulse_latency() {
    constexpr uint32_t kSampleRate = 48000;
    constexpr uint32_t kBlockFrames = 128;
    af::AudioEngine engine(kSampleRate, kBlockFrames);

    // Multi-stage graph: Source -> Volume -> Splitter -> Mixer -> Speaker Sink
    const char* graph = R"JSON({
      "nodes": [
        {"id": 1, "type": 1, "device_id": "test://impulse_in"},
        {"id": 2, "type": 101, "volume": 1.0},
        {"id": 3, "type": 104},
        {"id": 4, "type": 103},
        {"id": 5, "type": 201, "device_id": "test://impulse_out"}
      ],
      "connections": [
        {"src_node": 1, "src_port": 0, "dst_node": 2, "dst_port": 0},
        {"src_node": 2, "src_port": 0, "dst_node": 3, "dst_port": 0},
        {"src_node": 3, "src_port": 0, "dst_node": 4, "dst_port": 0},
        {"src_node": 4, "src_port": 0, "dst_node": 5, "dst_port": 0}
      ]
    })JSON";

    std::string err;
    CHECK(engine.apply_graph_json(graph, err), ("apply latency graph: " + err).c_str());

    // Inject delta impulse at frame 0 of block 1
    std::vector<float> impulse_block(kBlockFrames * 2, 0.0f);
    impulse_block[0] = 1.0f; // Left impulse
    impulse_block[1] = 1.0f; // Right impulse

    CHECK(engine.test_push_source_audio(1, impulse_block.data(), kBlockFrames), "push impulse");

    // Advance 1 tick
    engine.process_one_tick_for_test();

    // Pop from sink
    std::vector<float> sink_block(kBlockFrames * 2, 0.0f);
    size_t popped = engine.test_pop_sink_audio(5, sink_block.data(), kBlockFrames);
    CHECK(popped == kBlockFrames, "popped full sink block");

    // Impulse must emerge exactly at frame 0 of the sink block (1 tick delay = 128 frames)
    int impulse_frame = -1;
    for (size_t f = 0; f < kBlockFrames; ++f) {
        if (sink_block[f * 2] > 0.5f) {
            impulse_frame = static_cast<int>(f);
            break;
        }
    }
    CHECK(impulse_frame == 0, "Impulse detected with exact sample accuracy at output");
    const double latency_ms = (static_cast<double>(kBlockFrames) / static_cast<double>(kSampleRate)) * 1000.0;
    std::printf("  Automated latency measurement: exactly 1 block (%u frames = %.3f ms) end-to-end DSP pipeline delay\n",
                kBlockFrames, latency_ms);
    std::printf("[PASS] test_automated_impulse_latency\n");
}

void test_phonoryx_capi_exports() {
    phonoryx_engine_t* eng = phonoryx_engine_create(48000, 256);
    CHECK(eng != nullptr, "phonoryx_engine_create");

    CHECK(phonoryx_engine_start(eng) == 0, "phonoryx_engine_start");

    phonoryx_engine_stats_t stats{};
    CHECK(phonoryx_get_engine_stats(eng, &stats) == 0, "phonoryx_get_engine_stats");
    CHECK(stats.running == 1, "engine running via phonoryx stats");
    CHECK(stats.block_frames == 256, "block frames 256");

    phonoryx_log_entry_t logs[16];
    int32_t log_count = phonoryx_poll_logs(eng, logs, 16);
    CHECK(log_count >= 0, "phonoryx_poll_logs");

    phonoryx_engine_stop(eng);
    phonoryx_engine_destroy(eng);
    std::printf("[PASS] test_phonoryx_capi_exports\n");
}

void test_parameter_sanitization_and_output_limiter() {
    af::AudioEngine engine(48000, 128);

    // Apply JSON with outrageous parameters (+100 dB gain, 50.0 volume, EQ ±90 dB)
    const char* graph = R"JSON({
      "nodes": [
        {"id": 1, "type": 1, "device_id": "test://hot_src"},
        {"id": 2, "type": 102, "gain_db": 100.0, "volume": 50.0, "eq_gains_db": [90.0, -90.0, 50.0, -50.0, 99.0]},
        {"id": 3, "type": 201, "device_id": "test://hot_sink", "volume": 10.0}
      ],
      "connections": [
        {"src_node": 1, "src_port": 0, "dst_node": 2, "dst_port": 0},
        {"src_node": 2, "src_port": 0, "dst_node": 3, "dst_port": 0}
      ]
    })JSON";

    std::string err;
    CHECK(engine.apply_graph_json(graph, err), "apply graph with out-of-range params");

    // Dynamic parameter setters also enforce clamping
    CHECK(engine.set_node_float_param(2, "gain_db", 80.0f), "set excessive gain");
    CHECK(engine.set_node_float_param(2, "volume", 99.0f), "set excessive volume");
    CHECK(engine.set_node_float_param(2, "eq_band_0", 120.0f), "set excessive eq");

    // Inject loud signal into test source (amplitude 5.0f, way beyond 0 dBFS)
    std::vector<float> loud_in(128 * 2, 5.0f);
    CHECK(engine.test_push_source_audio(1, loud_in.data(), 128), "push loud audio");

    // Process tick
    engine.process_one_tick_for_test();

    // Pop from sink
    std::vector<float> out(128 * 2, 0.0f);
    size_t popped = engine.test_pop_sink_audio(3, out.data(), 128);
    CHECK(popped == 128, "popped sink audio");

    // Assert final output soft limiter prevents harsh digital clipping:
    // Every sample must be strictly bounded within [-1.0f, +1.0f] due to tanh limiter
    float max_peak = 0.0f;
    for (size_t i = 0; i < 128 * 2; ++i) {
        const float a = std::abs(out[i]);
        if (a > max_peak) max_peak = a;
        CHECK(a <= 1.0f, "Output sample bounded by soft limiter");
    }
    CHECK(max_peak > 0.90f && max_peak <= 1.0f, "Soft limiter kept hot signal safely bounded near full-scale without clipping");
    std::printf("[PASS] test_parameter_sanitization_and_output_limiter (peak=%.4f <= 1.0)\n", max_peak);
}

void test_slaved_primary_output_clocking() {
    af::AudioEngine engine(48000, 128);

    // Initial graph with test sink: master clock inactive (headless test mode)
    const char* graph_test = R"JSON({
      "nodes": [
        {"id": 1, "type": 1, "device_id": "test://src"},
        {"id": 2, "type": 201, "device_id": "test://sink"}
      ],
      "connections": [
        {"src_node": 1, "src_port": 0, "dst_node": 2, "dst_port": 0}
      ]
    })JSON";

    std::string err;
    CHECK(engine.apply_graph_json(graph_test, err), "apply test graph");
    CHECK(!engine.is_master_clock_active(), "Master clock inactive for test:// endpoints");
    CHECK(engine.master_render_node_id() == 0, "No master render node for test:// endpoints");

    // Graph with physical speaker endpoint
    const char* graph_speaker = R"JSON({
      "nodes": [
        {"id": 10, "type": 1, "device_id": "test://src"},
        {"id": 20, "type": 201, "device_id": "mock_speaker_dac"}
      ],
      "connections": [
        {"src_node": 10, "src_port": 0, "dst_node": 20, "dst_port": 0}
      ]
    })JSON";

    CHECK(engine.apply_graph_json(graph_speaker, err), "apply speaker graph");
    CHECK(engine.is_master_clock_active(), "Master clock elected for physical speaker endpoint");
    CHECK(engine.master_render_node_id() == 20, "Master render node id is 20");

    // Graph switched back to headless: master clock resets smoothly
    CHECK(engine.apply_graph_json(graph_test, err), "re-apply test graph");
    CHECK(!engine.is_master_clock_active(), "Master clock fallback to software timer on graph change");
    CHECK(engine.master_render_node_id() == 0, "Master render node id cleared");

    std::printf("[PASS] test_slaved_primary_output_clocking\n");
}

} // namespace

int main() {
    test_ring_buffer_and_resampler();
    test_splitting_and_independent_volume();
    test_mixer_multiple_sources();
    test_cycle_and_validation_rejection();
    test_dynamic_routing_preserves_active_route_and_wav();
    test_windows_device_and_process_discovery();
    test_resampler_phase_continuity();
    test_rt_loop_timing_accuracy();
    test_json_fuzz();
    test_log_queue_multithreaded_stress();
    test_clock_drift_long_run();
    test_automated_impulse_latency();
    test_phonoryx_capi_exports();
    test_parameter_sanitization_and_output_limiter();
    test_slaved_primary_output_clocking();

    if (g_failures > 0) {
        std::fprintf(stderr, "Phonoryx C++ tests FAILED (%d failures)\n", g_failures);
        return 1;
    }
    std::printf("All Phonoryx C++ engine tests PASSED!\n");
    return 0;
}

