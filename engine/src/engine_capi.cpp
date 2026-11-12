#include "phonoryx_capi.h"
#include "audio_graph.hpp"
#include "process_manager.hpp"

#include <algorithm>
#include <cstring>
#include <string>

struct af_engine_t {
    af::AudioEngine impl;
    af_engine_t(uint32_t sr, uint32_t bf) : impl(sr, bf) {}
};

extern "C" {

AF_API af_engine_t* af_engine_create(uint32_t sample_rate, uint32_t block_frames) {
    return new af_engine_t(sample_rate, block_frames);
}

AF_API void af_engine_destroy(af_engine_t* engine) {
    delete engine;
}

AF_API int32_t af_engine_start(af_engine_t* engine) {
    if (!engine) return -1;
    return engine->impl.start() ? 0 : -1;
}

AF_API void af_engine_stop(af_engine_t* engine) {
    if (engine) engine->impl.stop();
}

AF_API int32_t af_enumerate_devices(
    af_engine_t* engine,
    af_device_info_t* out_devices,
    int32_t max_devices)
{
    if (!engine || !out_devices || max_devices <= 0) return 0;
    auto list = engine->impl.devices().enumerate_devices();
    const int32_t n = static_cast<int32_t>(std::min<size_t>(list.size(), static_cast<size_t>(max_devices)));
    for (int32_t i = 0; i < n; ++i) {
        out_devices[i] = list[static_cast<size_t>(i)];
    }
    return n;
}

AF_API uint64_t af_get_device_change_seq(af_engine_t* engine) {
    return engine ? engine->impl.devices().change_sequence() : 0;
}

AF_API int32_t af_enumerate_processes(
    af_engine_t* engine,
    af_process_info_t* out_processes,
    int32_t max_processes)
{
    (void)engine;
    if (!out_processes || max_processes <= 0) return 0;
    auto list = af::ProcessManager::enumerate_processes();
    const int32_t n = static_cast<int32_t>(std::min<size_t>(list.size(), static_cast<size_t>(max_processes)));
    for (int32_t i = 0; i < n; ++i) {
        out_processes[i] = list[static_cast<size_t>(i)];
    }
    return n;
}

AF_API int32_t af_graph_apply_json(
    af_engine_t* engine,
    const char* graph_json,
    char* err_buf,
    int32_t err_cap)
{
    if (!engine || !graph_json) {
        if (err_buf && err_cap > 0) std::strncpy(err_buf, "Null argument", static_cast<size_t>(err_cap) - 1);
        return -1;
    }
    std::string err;
    if (!engine->impl.apply_graph_json(graph_json, err)) {
        if (err_buf && err_cap > 0) {
            std::memset(err_buf, 0, static_cast<size_t>(err_cap));
            std::strncpy(err_buf, err.c_str(), static_cast<size_t>(err_cap) - 1);
        }
        return -1;
    }
    return 0;
}

AF_API int32_t af_node_set_float_param(
    af_engine_t* engine,
    uint64_t node_id,
    const char* param_name,
    float value)
{
    if (!engine) return -1;
    return engine->impl.set_node_float_param(node_id, param_name, value) ? 0 : -1;
}

AF_API int32_t af_node_set_int_param(
    af_engine_t* engine,
    uint64_t node_id,
    const char* param_name,
    int32_t value)
{
    if (!engine) return -1;
    return engine->impl.set_node_int_param(node_id, param_name, value) ? 0 : -1;
}

AF_API int32_t af_get_node_meters(
    af_engine_t* engine,
    af_node_meter_t* out_meters,
    int32_t max_meters)
{
    if (!engine) return 0;
    return engine->impl.get_node_meters(out_meters, max_meters);
}

AF_API int32_t af_get_engine_stats(
    af_engine_t* engine,
    af_engine_stats_t* out_stats)
{
    if (!engine || !out_stats) return -1;
    engine->impl.get_stats(out_stats);
    return 0;
}

AF_API int32_t af_poll_logs(
    af_engine_t* engine,
    af_log_entry_t* out_logs,
    int32_t max_logs)
{
    if (!engine) return 0;
    return engine->impl.logs().pop_batch(out_logs, max_logs);
}

} // extern "C"

