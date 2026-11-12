#pragma once

#include <stdint.h>

#ifdef _WIN32
  #if defined(AUDIOFLOW_STATIC_TEST)
    #define AF_API
  #elif defined(AUDIOFLOW_EXPORTS)
    #define AF_API __declspec(dllexport)
  #else
    #define AF_API __declspec(dllimport)
  #endif
#else
  #define AF_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct af_engine_t af_engine_t;

typedef enum af_data_flow_t {
    AF_FLOW_RENDER = 0,
    AF_FLOW_CAPTURE = 1
} af_data_flow_t;

typedef enum af_device_role_hint_t {
    AF_ROLE_UNKNOWN = 0,
    AF_ROLE_SPEAKERS = 1,
    AF_ROLE_HEADPHONES = 2,
    AF_ROLE_MICROPHONE = 3,
    AF_ROLE_LINE_IN = 4,
    AF_ROLE_VIRTUAL = 5
} af_device_role_hint_t;

typedef enum af_node_type_t {
    /* Source nodes */
    AF_NODE_APP_CAPTURE = 1,
    AF_NODE_SYSTEM_CAPTURE = 2,
    AF_NODE_MIC_INPUT = 3,
    AF_NODE_LINE_INPUT = 4,
    AF_NODE_VIRTUAL_INPUT = 5,

    /* Processing nodes */
    AF_NODE_VOLUME = 101,
    AF_NODE_GAIN = 102,
    AF_NODE_MIXER = 103,
    AF_NODE_SPLITTER = 104,
    AF_NODE_MUTE = 105,
    AF_NODE_STEREO_TO_MONO = 107,
    AF_NODE_MONO_TO_STEREO = 108,
    AF_NODE_CHANNEL_MAP = 109,
    AF_NODE_EQUALIZER = 110,

    /* Output nodes */
    AF_NODE_SPEAKER_OUTPUT = 201,
    AF_NODE_VIRTUAL_OUTPUT = 203,
    AF_NODE_FILE_RECORDING = 205
} af_node_type_t;

typedef struct af_device_info_t {
    char id[256];
    char name[256];
    int32_t flow;          /* af_data_flow_t */
    int32_t role_hint;     /* af_device_role_hint_t */
    uint32_t state;        /* DEVICE_STATE_* */
    uint32_t sample_rate;
    uint16_t channels;
    uint16_t is_default;
    uint16_t is_virtual;
    uint16_t reserved;
} af_device_info_t;

typedef struct af_process_info_t {
    uint32_t pid;
    uint32_t parent_pid;
    char exe_name[128];
    char exe_path[260];
    char window_title[256];
    uint16_t has_audio_session;
    uint16_t reserved;
    float session_peak;
} af_process_info_t;

typedef struct af_node_meter_t {
    uint64_t node_id;
    float peak_left;
    float peak_right;
    float rms_left;
    float rms_right;
    uint32_t active;
    uint32_t xruns;
} af_node_meter_t;

typedef struct af_engine_stats_t {
    uint32_t running;
    uint32_t sample_rate;
    uint32_t block_frames;
    uint32_t active_nodes;
    uint32_t active_connections;
    uint32_t virtual_endpoints;
    uint32_t total_xruns;
    float cpu_load_percent;
    uint64_t processed_blocks;
    uint64_t device_change_seq;
} af_engine_stats_t;

typedef struct af_log_entry_t {
    uint64_t timestamp_ms;
    int32_t level; /* 0=info, 1=warn, 2=error */
    uint64_t node_id;
    char message[256];
} af_log_entry_t;

/* Engine lifecycle */
AF_API af_engine_t* af_engine_create(uint32_t sample_rate, uint32_t block_frames);
AF_API void af_engine_destroy(af_engine_t* engine);
AF_API int32_t af_engine_start(af_engine_t* engine);
AF_API void af_engine_stop(af_engine_t* engine);

/* Phase 1: Device Discovery */
AF_API int32_t af_enumerate_devices(
    af_engine_t* engine,
    af_device_info_t* out_devices,
    int32_t max_devices);
AF_API uint64_t af_get_device_change_seq(af_engine_t* engine);

/* Phase 2 / 9: Process & Audio Session Discovery */
AF_API int32_t af_enumerate_processes(
    af_engine_t* engine,
    af_process_info_t* out_processes,
    int32_t max_processes);

/* Phase 3 / 8: Live Graph Compilation & Lock-Free Swap */
/* Graph JSON format:
   {
     "nodes": [
       { "id": 1, "type": 1, "device_id": "...", "pid": 1234, "file_path": "...",
         "volume": 1.0, "gain_db": 0.0, "muted": false,
         "channel_map": [0, 1], "eq_gains_db": [0,0,0,0,0] }
     ],
     "connections": [
       { "src_node": 1, "src_port": 0, "dst_node": 2, "dst_port": 0 }
     ]
   }
*/
AF_API int32_t af_graph_apply_json(
    af_engine_t* engine,
    const char* graph_json,
    char* err_buf,
    int32_t err_cap);

/* Real-time lock-free parameter updates without rebuilding graph */
AF_API int32_t af_node_set_float_param(
    af_engine_t* engine,
    uint64_t node_id,
    const char* param_name,
    float value);

AF_API int32_t af_node_set_int_param(
    af_engine_t* engine,
    uint64_t node_id,
    const char* param_name,
    int32_t value);

/* Phase 2 / 10: Telemetry & Logging */
AF_API int32_t af_get_node_meters(
    af_engine_t* engine,
    af_node_meter_t* out_meters,
    int32_t max_meters);

AF_API int32_t af_get_engine_stats(
    af_engine_t* engine,
    af_engine_stats_t* out_stats);

AF_API int32_t af_poll_logs(
    af_engine_t* engine,
    af_log_entry_t* out_logs,
    int32_t max_logs);

#ifdef __cplusplus
}
#endif

