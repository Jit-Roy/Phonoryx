#include "audio_graph.hpp"
#include "json_mini.hpp"

#include <avrt.h>
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <queue>
#include <unordered_set>

namespace af {

bool AudioEngine::is_source_node(int32_t type) noexcept {
    return type >= AF_NODE_APP_CAPTURE && type <= AF_NODE_VIRTUAL_INPUT;
}

bool AudioEngine::is_output_node(int32_t type) noexcept {
    return type >= AF_NODE_SPEAKER_OUTPUT && type <= AF_NODE_FILE_RECORDING;
}

AudioEngine::AudioEngine(uint32_t sample_rate, uint32_t block_frames)
    : sample_rate_(sample_rate > 0 ? sample_rate : 48000)
    , block_frames_(block_frames > 0 ? block_frames : 480)
    , device_mgr_(&logs_)
{
    stop_event_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    device_mgr_.initialize();
    device_mgr_.enumerate_devices();
}

AudioEngine::~AudioEngine() {
    stop();
    {
        std::lock_guard<std::mutex> lock(ctrl_mutex_);
        auto* snap = active_snapshot_.exchange(nullptr, std::memory_order_acq_rel);
        delete snap;
        retired_snapshots_.clear();
        for (auto& [id, res] : resources_) {
            if (res.endpoint) res.endpoint->stop();
            if (res.recorder) res.recorder->stop();
        }
        resources_.clear();
    }
    device_mgr_.shutdown();
    if (stop_event_) {
        CloseHandle(stop_event_);
        stop_event_ = nullptr;
    }
}

bool AudioEngine::start() {
    if (running_.exchange(true, std::memory_order_acq_rel)) return true;
    ResetEvent(stop_event_);
    rt_thread_ = std::thread(&AudioEngine::rt_loop, this);
    logs_.push(0, 0, "AudioFlow real-time engine started");
    return true;
}

void AudioEngine::stop() {
    if (!running_.exchange(false, std::memory_order_acq_rel)) return;
    if (stop_event_) SetEvent(stop_event_);
    if (rt_thread_.joinable()) rt_thread_.join();
    logs_.push(0, 0, "AudioFlow real-time engine stopped");
}

bool AudioEngine::parse_graph_json(
    const char* json_text,
    std::vector<GraphNodeSpec>& nodes,
    std::vector<GraphConnectionSpec>& conns,
    std::string& err)
{
    json::Value root;
    json::Parser parser(json_text);
    if (!parser.parse(root, err)) return false;
    if (!root.is_object()) {
        err = "Graph root must be a JSON object";
        return false;
    }

    const auto* nodes_val = root.get("nodes");
    if (nodes_val) {
        if (!nodes_val->is_array()) {
            err = "'nodes' must be an array";
            return false;
        }
        for (const auto& nv : std::get<json::Array>(nodes_val->data)) {
            if (!nv.is_object()) {
                err = "Each node entry must be an object";
                return false;
            }
            GraphNodeSpec spec;
            const auto* id_v = nv.get("id");
            const auto* type_v = nv.get("type");
            if (!id_v || !type_v) {
                err = "Node missing 'id' or 'type'";
                return false;
            }
            spec.id = id_v->as_uint64();
            spec.type = static_cast<int32_t>(type_v->as_int64());
            if (const auto* v = nv.get("device_id")) spec.device_id = v->as_string();
            if (const auto* v = nv.get("pid")) spec.pid = static_cast<uint32_t>(v->as_uint64());
            if (const auto* v = nv.get("file_path")) spec.file_path = v->as_string();
            if (const auto* v = nv.get("volume")) spec.volume = static_cast<float>(v->as_double(1.0));
            if (const auto* v = nv.get("gain_db")) spec.gain_db = static_cast<float>(v->as_double(0.0));
            if (const auto* v = nv.get("muted")) spec.muted = v->as_bool(false);
            if (const auto* v = nv.get("channel_map"); v && v->is_array()) {
                const auto& arr = std::get<json::Array>(v->data);
                if (arr.size() >= 1) spec.chan_map_l = static_cast<int32_t>(arr[0].as_int64(0));
                if (arr.size() >= 2) spec.chan_map_r = static_cast<int32_t>(arr[1].as_int64(1));
            }
            if (const auto* v = nv.get("eq_gains_db"); v && v->is_array()) {
                const auto& arr = std::get<json::Array>(v->data);
                for (size_t i = 0; i < std::min<size_t>(5, arr.size()); ++i) {
                    spec.eq_gains_db[i] = static_cast<float>(arr[i].as_double(0.0));
                }
            }
            nodes.push_back(std::move(spec));
        }
    }

    const auto* conns_val = root.get("connections");
    if (conns_val) {
        if (!conns_val->is_array()) {
            err = "'connections' must be an array";
            return false;
        }
        for (const auto& cv : std::get<json::Array>(conns_val->data)) {
            if (!cv.is_object()) {
                err = "Each connection entry must be an object";
                return false;
            }
            GraphConnectionSpec c;
            const auto* sn = cv.get("src_node");
            const auto* dn = cv.get("dst_node");
            if (!sn || !dn) {
                err = "Connection missing 'src_node' or 'dst_node'";
                return false;
            }
            c.src_node = sn->as_uint64();
            c.dst_node = dn->as_uint64();
            if (const auto* sp = cv.get("src_port")) c.src_port = static_cast<uint32_t>(sp->as_uint64());
            if (const auto* dp = cv.get("dst_port")) c.dst_port = static_cast<uint32_t>(dp->as_uint64());
            conns.push_back(c);
        }
    }

    return true;
}

bool AudioEngine::apply_graph_json(const char* json_text, std::string& err_out) {
    std::vector<GraphNodeSpec> specs;
    std::vector<GraphConnectionSpec> conns;
    if (!parse_graph_json(json_text, specs, conns, err_out)) {
        logs_.push(2, 0, err_out.c_str());
        return false;
    }

    std::unordered_map<uint64_t, size_t> id_to_idx;
    for (size_t i = 0; i < specs.size(); ++i) {
        if (specs[i].id == 0) {
            err_out = "Node ID 0 is reserved";
            return false;
        }
        if (!id_to_idx.emplace(specs[i].id, i).second) {
            err_out = "Duplicate node ID in graph";
            return false;
        }
    }

    // Validate endpoints & port semantics + build adjacency for cycle detection
    std::vector<std::vector<size_t>> adj(specs.size());
    std::vector<size_t> in_degree(specs.size(), 0);
    std::vector<size_t> out_degree(specs.size(), 0);

    for (const auto& c : conns) {
        auto it_src = id_to_idx.find(c.src_node);
        auto it_dst = id_to_idx.find(c.dst_node);
        if (it_src == id_to_idx.end() || it_dst == id_to_idx.end()) {
            err_out = "Connection references unknown node ID";
            return false;
        }
        if (c.src_node == c.dst_node) {
            err_out = "Self-loop connection rejected";
            return false;
        }
        const size_t si = it_src->second;
        const size_t di = it_dst->second;
        if (is_output_node(specs[si].type)) {
            err_out = "Output node cannot be a connection source";
            return false;
        }
        if (is_source_node(specs[di].type)) {
            err_out = "Source node cannot be a connection destination";
            return false;
        }
        adj[si].push_back(di);
        in_degree[di] += 1;
        out_degree[si] += 1;

        if (specs[di].type != AF_NODE_MIXER && in_degree[di] > 1) {
            err_out = "Only Audio Mixer nodes may accept multiple inputs";
            return false;
        }
    }

    // Kahn's topological sort for cycle detection
    std::vector<size_t> temp_indeg = in_degree;
    std::queue<size_t> q;
    for (size_t i = 0; i < specs.size(); ++i) {
        if (temp_indeg[i] == 0) q.push(i);
    }
    std::vector<size_t> topo_order;
    topo_order.reserve(specs.size());
    while (!q.empty()) {
        const size_t u = q.front();
        q.pop();
        topo_order.push_back(u);
        for (size_t v : adj[u]) {
            if (--temp_indeg[v] == 0) q.push(v);
        }
    }
    if (topo_order.size() != specs.size()) {
        err_out = "Cycle detected in audio graph (feedback loops are not allowed)";
        logs_.push(2, 0, err_out.c_str());
        return false;
    }

    std::lock_guard<std::mutex> lock(ctrl_mutex_);
    retire_old_snapshots();

    std::unordered_set<uint64_t> active_ids;
    for (const auto& s : specs) active_ids.insert(s.id);

    // Stop & erase resources for deleted nodes
    for (auto it = resources_.begin(); it != resources_.end(); ) {
        if (active_ids.find(it->first) == active_ids.end()) {
            if (it->second.endpoint) it->second.endpoint->stop();
            if (it->second.recorder) it->second.recorder->stop();
            it = resources_.erase(it);
        } else {
            ++it;
        }
    }

    auto next_snap = std::make_unique<CompiledGraphSnapshot>();
    next_snap->sample_rate = sample_rate_;
    next_snap->block_frames = block_frames_;
    next_snap->scratch_mix.assign(block_frames_ * 2, 0.0f);
    next_snap->edge_buffers.assign(conns.size(), std::vector<float>(block_frames_ * 2, 0.0f));
    next_snap->connection_count = static_cast<uint32_t>(conns.size());

    // Map old spec index -> topo position
    std::vector<size_t> idx_to_topo(specs.size(), 0);
    for (size_t t = 0; t < topo_order.size(); ++t) {
        idx_to_topo[topo_order[t]] = t;
    }

    next_snap->topo_nodes.resize(topo_order.size());

    for (size_t t = 0; t < topo_order.size(); ++t) {
        const auto& s = specs[topo_order[t]];
        auto& res = resources_[s.id];

        const bool endpoint_changed =
            (res.spec.id == 0) ||
            (res.spec.type != s.type) ||
            (res.spec.device_id != s.device_id) ||
            (res.spec.pid != s.pid);
        const bool file_changed =
            (res.spec.id == 0) ||
            (res.spec.type != s.type) ||
            (res.spec.file_path != s.file_path);

        if (!res.params) {
            res.params = std::make_shared<NodeRuntimeParams>();
        }
        res.params->volume.store(s.volume, std::memory_order_relaxed);
        res.params->gain_db.store(s.gain_db, std::memory_order_relaxed);
        res.params->muted.store(s.muted ? 1 : 0, std::memory_order_relaxed);
        res.params->chan_map_l.store(s.chan_map_l, std::memory_order_relaxed);
        res.params->chan_map_r.store(s.chan_map_r, std::memory_order_relaxed);
        for (size_t b = 0; b < 5; ++b) {
            res.params->eq_gains_db[b].store(s.eq_gains_db[b], std::memory_order_relaxed);
        }

        if (!res.test_in_ring && is_source_node(s.type)) {
            res.test_in_ring = std::make_shared<StereoRingBuffer>(32768);
        }
        if (!res.test_out_ring && is_output_node(s.type)) {
            res.test_out_ring = std::make_shared<StereoRingBuffer>(32768);
        }

        // Only recreate WASAPI worker if endpoint identity changed (preserves active streams!)
        if (endpoint_changed) {
            if (res.endpoint) {
                res.endpoint->stop();
                res.endpoint.reset();
            }
            const bool is_test_uri = (s.device_id.rfind("test://", 0) == 0);
            if (!is_test_uri) {
                if (s.type == AF_NODE_APP_CAPTURE) {
                    res.endpoint = std::make_shared<WasapiEndpointWorker>(
                        s.id, EndpointMode::ProcessLoopback, "", s.pid, sample_rate_, &logs_);
                    res.endpoint->start();
                } else if (s.type == AF_NODE_SYSTEM_CAPTURE) {
                    res.endpoint = std::make_shared<WasapiEndpointWorker>(
                        s.id, EndpointMode::SystemLoopback, s.device_id, 0, sample_rate_, &logs_);
                    res.endpoint->start();
                } else if (s.type == AF_NODE_MIC_INPUT || s.type == AF_NODE_LINE_INPUT || s.type == AF_NODE_VIRTUAL_INPUT) {
                    if (!s.device_id.empty()) {
                        res.endpoint = std::make_shared<WasapiEndpointWorker>(
                            s.id, EndpointMode::InputCapture, s.device_id, 0, sample_rate_, &logs_);
                        res.endpoint->start();
                    }
                } else if (s.type == AF_NODE_SPEAKER_OUTPUT || s.type == AF_NODE_HEADPHONE_OUTPUT ||
                           s.type == AF_NODE_VIRTUAL_OUTPUT || s.type == AF_NODE_VIRTUAL_MIC_OUTPUT) {
                    if (!s.device_id.empty()) {
                        res.endpoint = std::make_shared<WasapiEndpointWorker>(
                            s.id, EndpointMode::OutputRender, s.device_id, 0, sample_rate_, &logs_);
                        res.endpoint->start();
                    }
                }
            }
        }

        if (file_changed) {
            if (res.recorder) {
                res.recorder->stop();
                res.recorder.reset();
            }
            if (s.type == AF_NODE_FILE_RECORDING && !s.file_path.empty()) {
                res.recorder = std::make_shared<WavFileRecorder>(s.file_path, sample_rate_, &logs_);
                res.recorder->start();
            }
        }

        res.spec = s;

        CompiledNode& cn = next_snap->topo_nodes[t];
        cn.id = s.id;
        cn.type = s.type;
        cn.params = res.params;
        cn.endpoint = res.endpoint;
        cn.recorder = res.recorder;
        cn.test_in_ring = res.test_in_ring;
        cn.test_out_ring = res.test_out_ring;
        cn.current_gain = s.muted ? 0.0f : (s.volume * dsp::db_to_linear(s.gain_db));
        cn.eq.prepare(sample_rate_);
        cn.eq.update_if_needed(s.eq_gains_db);
    }

    for (size_t e = 0; e < conns.size(); ++e) {
        const size_t si = idx_to_topo[id_to_idx[conns[e].src_node]];
        const size_t di = idx_to_topo[id_to_idx[conns[e].dst_node]];
        next_snap->topo_nodes[si].out_edges.push_back(e);
        next_snap->topo_nodes[di].in_edges.push_back(e);
    }

    // Lock-free atomic snapshot swap
    auto* prev = active_snapshot_.exchange(next_snap.release(), std::memory_order_acq_rel);
    if (prev) {
        retired_snapshots_.emplace_back(prev);
    }
    return true;
}

void AudioEngine::retire_old_snapshots() {
    if (retired_snapshots_.empty()) return;
    // Wait briefly until RT loop is not holding a raw pointer to an older snapshot.
    while (rt_in_flight_.load(std::memory_order_acquire) != 0) {
        Sleep(1);
    }
    retired_snapshots_.clear();
}

bool AudioEngine::set_node_float_param(uint64_t node_id, const char* name, float value) {
    if (!name) return false;
    std::lock_guard<std::mutex> lock(ctrl_mutex_);
    auto it = resources_.find(node_id);
    if (it == resources_.end() || !it->second.params) return false;
    auto& p = *it->second.params;
    if (std::strcmp(name, "volume") == 0) {
        p.volume.store(std::clamp(value, 0.0f, 2.0f), std::memory_order_relaxed);
        return true;
    }
    if (std::strcmp(name, "gain_db") == 0) {
        p.gain_db.store(std::clamp(value, -60.0f, 24.0f), std::memory_order_relaxed);
        return true;
    }
    if (std::strncmp(name, "eq_band_", 8) == 0) {
        const int idx = std::atoi(name + 8);
        if (idx >= 0 && idx < 5) {
            p.eq_gains_db[static_cast<size_t>(idx)].store(
                std::clamp(value, -18.0f, 18.0f), std::memory_order_relaxed);
            return true;
        }
    }
    return false;
}

bool AudioEngine::set_node_int_param(uint64_t node_id, const char* name, int32_t value) {
    if (!name) return false;
    std::lock_guard<std::mutex> lock(ctrl_mutex_);
    auto it = resources_.find(node_id);
    if (it == resources_.end() || !it->second.params) return false;
    auto& p = *it->second.params;
    if (std::strcmp(name, "muted") == 0) {
        p.muted.store(value != 0 ? 1 : 0, std::memory_order_relaxed);
        return true;
    }
    if (std::strcmp(name, "chan_map_l") == 0) {
        p.chan_map_l.store(value, std::memory_order_relaxed);
        return true;
    }
    if (std::strcmp(name, "chan_map_r") == 0) {
        p.chan_map_r.store(value, std::memory_order_relaxed);
        return true;
    }
    return false;
}

int32_t AudioEngine::get_node_meters(af_node_meter_t* out, int32_t max_count) {
    if (!out || max_count <= 0) return 0;
    std::lock_guard<std::mutex> lock(ctrl_mutex_);
    int32_t n = 0;
    for (const auto& [id, res] : resources_) {
        if (n >= max_count) break;
        if (!res.params) continue;
        af_node_meter_t& m = out[n++];
        m.node_id = id;
        m.peak_left = res.params->peak_l.load(std::memory_order_relaxed);
        m.peak_right = res.params->peak_r.load(std::memory_order_relaxed);
        m.rms_left = res.params->rms_l.load(std::memory_order_relaxed);
        m.rms_right = res.params->rms_r.load(std::memory_order_relaxed);
        m.active = res.params->active.load(std::memory_order_relaxed);
        uint32_t xr = 0;
        if (res.endpoint) xr += res.endpoint->xruns();
        if (res.recorder) xr += res.recorder->xruns();
        m.xruns = xr;
    }
    return n;
}

void AudioEngine::get_stats(af_engine_stats_t* out) {
    if (!out) return;
    std::memset(out, 0, sizeof(*out));
    out->running = running_.load(std::memory_order_acquire) ? 1u : 0u;
    out->sample_rate = sample_rate_;
    out->block_frames = block_frames_;
    out->virtual_endpoints = device_mgr_.virtual_endpoint_count();
    out->cpu_load_percent = cpu_load_percent_.load(std::memory_order_relaxed);
    out->processed_blocks = processed_blocks_.load(std::memory_order_relaxed);
    out->device_change_seq = device_mgr_.change_sequence();

    auto* snap = active_snapshot_.load(std::memory_order_acquire);
    if (snap) {
        out->active_nodes = static_cast<uint32_t>(snap->topo_nodes.size());
        out->active_connections = snap->connection_count;
    }
}

void AudioEngine::process_single_block(CompiledGraphSnapshot* snap) {
    if (!snap) return;
    const size_t frames = snap->block_frames;
    float* work = snap->scratch_mix.data();

    for (auto& node : snap->topo_nodes) {
        std::memset(work, 0, frames * 2 * sizeof(float));

        if (is_source_node(node.type)) {
            if (node.test_in_ring && node.test_in_ring->available_read() >= frames) {
                node.test_in_ring->read(work, frames);
            } else if (node.endpoint && node.endpoint->ring().available_read() >= frames) {
                node.endpoint->ring().read(work, frames);
            }

            // Optional source gain/mute
            const float target = node.params->muted.load(std::memory_order_relaxed)
                ? 0.0f
                : node.params->volume.load(std::memory_order_relaxed);
            dsp::apply_volume_ramped(work, frames, target, node.current_gain);
            dsp::compute_meters(work, frames, *node.params);

            for (size_t e : node.out_edges) {
                std::memcpy(snap->edge_buffers[e].data(), work, frames * 2 * sizeof(float));
            }
            continue;
        }

        // Gather input(s)
        if (node.type == AF_NODE_MIXER) {
            for (size_t e : node.in_edges) {
                dsp::mix_accumulate(work, snap->edge_buffers[e].data(), frames);
            }
            dsp::apply_soft_limiter(work, frames);
        } else if (!node.in_edges.empty()) {
            std::memcpy(work, snap->edge_buffers[node.in_edges[0]].data(), frames * 2 * sizeof(float));
        }

        // Apply node processing (zero allocation)
        switch (node.type) {
            case AF_NODE_VOLUME: {
                const float target = node.params->muted.load(std::memory_order_relaxed)
                    ? 0.0f
                    : node.params->volume.load(std::memory_order_relaxed);
                dsp::apply_volume_ramped(work, frames, target, node.current_gain);
                break;
            }
            case AF_NODE_GAIN: {
                const float db = node.params->gain_db.load(std::memory_order_relaxed);
                const float target = node.params->muted.load(std::memory_order_relaxed)
                    ? 0.0f
                    : dsp::db_to_linear(db);
                dsp::apply_volume_ramped(work, frames, target, node.current_gain);
                break;
            }
            case AF_NODE_MUTE: {
                const float target = node.params->muted.load(std::memory_order_relaxed) ? 0.0f : 1.0f;
                dsp::apply_volume_ramped(work, frames, target, node.current_gain);
                break;
            }
            case AF_NODE_STEREO_TO_MONO:
                dsp::apply_stereo_to_mono(work, frames);
                break;
            case AF_NODE_MONO_TO_STEREO:
                dsp::apply_mono_to_stereo(work, frames);
                break;
            case AF_NODE_CHANNEL_MAP:
                dsp::apply_channel_map(
                    work,
                    frames,
                    node.params->chan_map_l.load(std::memory_order_relaxed),
                    node.params->chan_map_r.load(std::memory_order_relaxed));
                break;
            case AF_NODE_EQUALIZER: {
                std::array<float, 5> gains{};
                for (size_t b = 0; b < 5; ++b) {
                    gains[b] = node.params->eq_gains_db[b].load(std::memory_order_relaxed);
                }
                node.eq.update_if_needed(gains);
                node.eq.process(work, frames);
                break;
            }
            case AF_NODE_SPLITTER:
            case AF_NODE_METER:
            case AF_NODE_MIXER:
            default:
                break;
        }

        if (is_output_node(node.type)) {
            const float target = node.params->muted.load(std::memory_order_relaxed)
                ? 0.0f
                : node.params->volume.load(std::memory_order_relaxed);
            dsp::apply_volume_ramped(work, frames, target, node.current_gain);

            if (node.test_out_ring) {
                node.test_out_ring->write(work, frames);
            }
            if (node.endpoint) {
                node.endpoint->ring().write(work, frames);
            }
            if (node.recorder) {
                node.recorder->push_stereo(work, frames);
            }
        }

        dsp::compute_meters(work, frames, *node.params);

        // Fan out to outgoing edges (for Splitter, each branch gets an independent copy)
        for (size_t e : node.out_edges) {
            std::memcpy(snap->edge_buffers[e].data(), work, frames * 2 * sizeof(float));
        }
    }
}

void AudioEngine::rt_loop() {
    DWORD task_idx = 0;
    HANDLE avrt = AvSetMmThreadCharacteristicsW(L"Pro Audio", &task_idx);

    HANDLE timer = CreateWaitableTimerExW(nullptr, nullptr, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
    if (!timer) {
        timer = CreateWaitableTimerW(nullptr, FALSE, nullptr);
    }

    const int64_t block_100ns = (static_cast<int64_t>(block_frames_) * 10000000LL) / static_cast<int64_t>(sample_rate_);
    const double block_us = (static_cast<double>(block_frames_) * 1e6) / static_cast<double>(sample_rate_);
    LARGE_INTEGER due{};
    due.QuadPart = -block_100ns;
    const LONG period_ms = std::max<LONG>(1, static_cast<LONG>((block_frames_ * 1000) / sample_rate_));
    if (timer) {
        SetWaitableTimer(timer, &due, period_ms, nullptr, nullptr, FALSE);
    }

    HANDLE waits[2] = {stop_event_, timer};
    while (running_.load(std::memory_order_acquire)) {
        if (timer) {
            DWORD w = WaitForMultipleObjects(2, waits, FALSE, 50);
            if (w == WAIT_OBJECT_0) break;
        } else {
            if (WaitForSingleObject(stop_event_, period_ms) == WAIT_OBJECT_0) break;
        }

        const auto t0 = std::chrono::steady_clock::now();
        rt_in_flight_.fetch_add(1, std::memory_order_acquire);
        auto* snap = active_snapshot_.load(std::memory_order_acquire);
        if (snap) {
            process_single_block(snap);
        }
        rt_in_flight_.fetch_sub(1, std::memory_order_release);
        const auto t1 = std::chrono::steady_clock::now();

        const double elapsed_us = std::chrono::duration<double, std::micro>(t1 - t0).count();
        const float load = static_cast<float>(std::clamp((elapsed_us / block_us) * 100.0, 0.0, 100.0));
        cpu_load_percent_.store(load, std::memory_order_relaxed);
        processed_blocks_.fetch_add(1, std::memory_order_relaxed);
    }

    if (timer) CloseHandle(timer);
    if (avrt) AvRevertMmThreadCharacteristics(avrt);
}

bool AudioEngine::test_push_source_audio(uint64_t node_id, const float* stereo_in, size_t frames) {
    std::lock_guard<std::mutex> lock(ctrl_mutex_);
    auto it = resources_.find(node_id);
    if (it == resources_.end() || !it->second.test_in_ring) return false;
    return it->second.test_in_ring->write(stereo_in, frames) == frames;
}

size_t AudioEngine::test_pop_sink_audio(uint64_t node_id, float* stereo_out, size_t frames) {
    std::lock_guard<std::mutex> lock(ctrl_mutex_);
    auto it = resources_.find(node_id);
    if (it == resources_.end() || !it->second.test_out_ring) return 0;
    const size_t avail = it->second.test_out_ring->available_read();
    const size_t n = std::min(frames, avail);
    if (n > 0) it->second.test_out_ring->read(stereo_out, n);
    return n;
}

void AudioEngine::process_one_tick_for_test() {
    rt_in_flight_.fetch_add(1, std::memory_order_acquire);
    auto* snap = active_snapshot_.load(std::memory_order_acquire);
    if (snap) process_single_block(snap);
    rt_in_flight_.fetch_sub(1, std::memory_order_release);
    processed_blocks_.fetch_add(1, std::memory_order_relaxed);
}

} // namespace af

