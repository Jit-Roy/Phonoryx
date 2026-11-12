#include "wasapi_endpoint.hpp"
#include "utf_util.hpp"

#include <avrt.h>
#include <audioclientactivationparams.h>
#include <ksmedia.h>
#include <wrl/client.h>
#include <wrl/implements.h>

#include <algorithm>
#include <cstdio>
#include <vector>

namespace af {

namespace {

using ActivateAudioInterfaceAsyncFn = HRESULT(WINAPI*)(
    LPCWSTR deviceInterfacePath,
    REFIID riid,
    PROPVARIANT* activationParams,
    IActivateAudioInterfaceCompletionHandler* completionHandler,
    IActivateAudioInterfaceAsyncOperation** activationOperation);

class ProcessLoopbackCompletionHandler final
    : public Microsoft::WRL::RuntimeClass<
          Microsoft::WRL::RuntimeClassFlags<Microsoft::WRL::ClassicCom>,
          Microsoft::WRL::FtmBase,
          IActivateAudioInterfaceCompletionHandler> {
public:
    ProcessLoopbackCompletionHandler() {
        event_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    }
    ~ProcessLoopbackCompletionHandler() override {
        if (event_) CloseHandle(event_);
    }

    STDMETHOD(ActivateCompleted)(IActivateAudioInterfaceAsyncOperation* operation) override {
        HRESULT hr_act = E_FAIL;
        Microsoft::WRL::ComPtr<IUnknown> punk;
        HRESULT hr = operation->GetActivateResult(&hr_act, &punk);
        if (SUCCEEDED(hr) && SUCCEEDED(hr_act) && punk) {
            hr_ = punk.As(&client_);
        } else {
            hr_ = FAILED(hr) ? hr : hr_act;
        }
        SetEvent(event_);
        return S_OK;
    }

    HANDLE event_handle() const noexcept { return event_; }
    HRESULT result() const noexcept { return hr_; }
    IAudioClient* take_client() noexcept { return client_.Detach(); }

private:
    HANDLE event_{nullptr};
    HRESULT hr_{E_FAIL};
    Microsoft::WRL::ComPtr<IAudioClient> client_;
};

void inspect_wave_format(const WAVEFORMATEX* wfx, uint16_t& bits, bool& is_float) noexcept {
    bits = wfx->wBitsPerSample;
    is_float = (wfx->wFormatTag == WAVE_FORMAT_IEEE_FLOAT);
    if (wfx->wFormatTag == WAVE_FORMAT_EXTENSIBLE && wfx->cbSize >= 22) {
        const auto* ext = reinterpret_cast<const WAVEFORMATEXTENSIBLE*>(wfx);
        is_float = (IsEqualGUID(ext->SubFormat, KSDATAFORMAT_SUBTYPE_IEEE_FLOAT) != 0);
    }
}

WAVEFORMATEXTENSIBLE make_float32_stereo_format(uint32_t sample_rate) noexcept {
    WAVEFORMATEXTENSIBLE fmt{};
    fmt.Format.wFormatTag = WAVE_FORMAT_EXTENSIBLE;
    fmt.Format.nChannels = 2;
    fmt.Format.nSamplesPerSec = sample_rate;
    fmt.Format.wBitsPerSample = 32;
    fmt.Format.nBlockAlign = 8;
    fmt.Format.nAvgBytesPerSec = sample_rate * 8;
    fmt.Format.cbSize = sizeof(WAVEFORMATEXTENSIBLE) - sizeof(WAVEFORMATEX);
    fmt.Samples.wValidBitsPerSample = 32;
    fmt.dwChannelMask = SPEAKER_FRONT_LEFT | SPEAKER_FRONT_RIGHT;
    fmt.SubFormat = KSDATAFORMAT_SUBTYPE_IEEE_FLOAT;
    return fmt;
}

} // namespace

WasapiEndpointWorker::WasapiEndpointWorker(
    uint64_t node_id,
    EndpointMode mode,
    std::string device_id,
    uint32_t target_pid,
    uint32_t engine_sample_rate,
    LogQueue* logs)
    : node_id_(node_id)
    , mode_(mode)
    , device_id_(std::move(device_id))
    , target_pid_(target_pid)
    , engine_sample_rate_(engine_sample_rate > 0 ? engine_sample_rate : 48000)
    , logs_(logs)
{
    stop_event_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
}

WasapiEndpointWorker::~WasapiEndpointWorker() {
    stop();
    if (stop_event_) {
        CloseHandle(stop_event_);
        stop_event_ = nullptr;
    }
}

bool WasapiEndpointWorker::start() {
    if (running_.exchange(true, std::memory_order_acq_rel)) return true;
    ResetEvent(stop_event_);
    ring_.reset();
    rt_capture_primed_ = false;
    rt_capture_fade_in_ = false;
    thread_ = std::thread(&WasapiEndpointWorker::thread_main, this);
    return true;
}

size_t WasapiEndpointWorker::read_capture_block(float* out_stereo, size_t frames) noexcept {
    if (!out_stereo || frames == 0) return 0;

    const size_t prebuffer = std::max<size_t>(
        frames * 2,
        (static_cast<size_t>(engine_sample_rate_) * 25) / 1000);
    const size_t max_backlog = std::max<size_t>(
        prebuffer * 6,
        (static_cast<size_t>(engine_sample_rate_) * 160) / 1000);

    size_t avail = ring_.available_read();
    if (!rt_capture_primed_) {
        if (avail < prebuffer) {
            return 0;
        }
        if (avail > max_backlog) {
            ring_.skip(avail - prebuffer);
        }
        rt_capture_primed_ = true;
        rt_capture_fade_in_ = true;
        avail = ring_.available_read();
    }

    if (avail > max_backlog) {
        ring_.skip(avail - prebuffer);
        rt_capture_fade_in_ = true;
        avail = ring_.available_read();
    }

    if (avail < frames) {
        ring_.record_underrun();
        rt_capture_primed_ = false;
        rt_capture_fade_in_ = true;
        if (avail > 0) {
            ring_.read(out_stereo, avail);
            const size_t fade_len = std::min<size_t>(32, avail);
            for (size_t i = 0; i < fade_len; ++i) {
                const float g = static_cast<float>(fade_len - 1 - i) / static_cast<float>(fade_len);
                const size_t idx = (avail - fade_len + i) * 2;
                out_stereo[idx] *= g;
                out_stereo[idx + 1] *= g;
            }
            std::memset(out_stereo + avail * 2, 0, (frames - avail) * 2 * sizeof(float));
            return avail;
        }
        return 0;
    }

    ring_.read(out_stereo, frames);
    if (rt_capture_fade_in_) {
        const size_t fade_len = std::min<size_t>(64, frames);
        for (size_t i = 0; i < fade_len; ++i) {
            const float g = static_cast<float>(i + 1) / static_cast<float>(fade_len);
            out_stereo[i * 2] *= g;
            out_stereo[i * 2 + 1] *= g;
        }
        rt_capture_fade_in_ = false;
    }
    return frames;
}

void WasapiEndpointWorker::stop() {
    if (!running_.exchange(false, std::memory_order_acq_rel)) return;
    if (stop_event_) SetEvent(stop_event_);
    if (thread_.joinable()) thread_.join();
    connected_.store(false, std::memory_order_release);
}

void WasapiEndpointWorker::thread_main() {
    HRESULT hr_co = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    DWORD task_idx = 0;
    HANDLE avrt_handle = AvSetMmThreadCharacteristicsW(L"Pro Audio", &task_idx);

    while (running_.load(std::memory_order_acquire)) {
        bool retryable = false;
        if (mode_ == EndpointMode::OutputRender) {
            retryable = run_render_session();
        } else {
            retryable = run_capture_session();
        }
        connected_.store(false, std::memory_order_release);
        if (!running_.load(std::memory_order_acquire)) break;
        if (!retryable) break;
        // Wait before reconnecting on device invalidation/hot-plug
        WaitForSingleObject(stop_event_, 1000);
    }

    if (avrt_handle) AvRevertMmThreadCharacteristics(avrt_handle);
    if (SUCCEEDED(hr_co)) CoUninitialize();
}

bool WasapiEndpointWorker::run_capture_session() {
    IAudioClient* client = nullptr;
    WAVEFORMATEX* mix_wfx = nullptr;
    WAVEFORMATEXTENSIBLE proc_wfx{};
    bool free_mix_wfx = false;

    if (mode_ == EndpointMode::ProcessLoopback) {
        if (target_pid_ == 0) {
            if (logs_) logs_->push(2, node_id_, "Process capture requires a valid target PID");
            return false;
        }
        HMODULE mmdev = LoadLibraryW(L"Mmdevapi.dll");
        auto activate_async = mmdev
            ? reinterpret_cast<ActivateAudioInterfaceAsyncFn>(
                  GetProcAddress(mmdev, "ActivateAudioInterfaceAsync"))
            : nullptr;
        if (!activate_async) {
            if (logs_) logs_->push(2, node_id_, "ActivateAudioInterfaceAsync unavailable (requires Windows 10 21H2+)");
            if (mmdev) FreeLibrary(mmdev);
            return false;
        }

        AUDIOCLIENT_ACTIVATION_PARAMS act_params{};
        act_params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
        act_params.ProcessLoopbackParams.TargetProcessId = target_pid_;
        act_params.ProcessLoopbackParams.ProcessLoopbackMode =
            PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE;

        PROPVARIANT pv{};
        pv.vt = VT_BLOB;
        pv.blob.cbSize = sizeof(act_params);
        pv.blob.pBlobData = reinterpret_cast<BYTE*>(&act_params);

        auto handler = Microsoft::WRL::Make<ProcessLoopbackCompletionHandler>();
        IActivateAudioInterfaceAsyncOperation* async_op = nullptr;
        HRESULT hr = activate_async(
            VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
            __uuidof(IAudioClient),
            &pv,
            handler.Get(),
            &async_op);
        if (SUCCEEDED(hr) && async_op) {
            HANDLE waits[2] = {stop_event_, handler->event_handle()};
            DWORD w = WaitForMultipleObjects(2, waits, FALSE, 5000);
            if (w == WAIT_OBJECT_0 + 1 && SUCCEEDED(handler->result())) {
                client = handler->take_client();
            } else {
                if (logs_) logs_->push(2, node_id_, "Process loopback activation failed or timed out");
            }
            async_op->Release();
        } else if (logs_) {
            char msg[256];
            std::snprintf(msg, sizeof(msg), "ActivateAudioInterfaceAsync failed: 0x%08lX", hr);
            logs_->push(2, node_id_, msg);
        }
        if (mmdev) FreeLibrary(mmdev);
        if (!client) return true;

        proc_wfx = make_float32_stereo_format(engine_sample_rate_);
        mix_wfx = reinterpret_cast<WAVEFORMATEX*>(&proc_wfx);
    } else {
        IMMDeviceEnumerator* enumerator = nullptr;
        HRESULT hr = CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
                                      __uuidof(IMMDeviceEnumerator), reinterpret_cast<void**>(&enumerator));
        if (FAILED(hr) || !enumerator) return true;

        IMMDevice* device = nullptr;
        if (!device_id_.empty()) {
            std::wstring wid = utf8_to_wide(device_id_.c_str());
            hr = enumerator->GetDevice(wid.c_str(), &device);
        } else {
            EDataFlow flow = (mode_ == EndpointMode::SystemLoopback) ? eRender : eCapture;
            hr = enumerator->GetDefaultAudioEndpoint(flow, eConsole, &device);
        }
        enumerator->Release();

        if (FAILED(hr) || !device) {
            if (logs_) logs_->push(1, node_id_, "Capture device unavailable; waiting for reconnect");
            return true;
        }

        hr = device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                              reinterpret_cast<void**>(&client));
        device->Release();
        if (FAILED(hr) || !client) return true;

        hr = client->GetMixFormat(&mix_wfx);
        if (FAILED(hr) || !mix_wfx) {
            client->Release();
            return true;
        }
        free_mix_wfx = true;
    }

    const DWORD stream_flags = (mode_ == EndpointMode::InputCapture)
        ? AUDCLNT_STREAMFLAGS_EVENTCALLBACK
        : (AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK);

    // 500 ms buffer for robust shared capture / loopback
    constexpr REFERENCE_TIME kBufferDuration = 5000000;
    HRESULT hr = client->Initialize(
        AUDCLNT_SHAREMODE_SHARED, stream_flags, kBufferDuration, 0, mix_wfx, nullptr);
    if (FAILED(hr)) {
        char msg[256];
        std::snprintf(msg, sizeof(msg), "IAudioClient::Initialize(capture) failed: 0x%08lX", hr);
        if (logs_) logs_->push(2, node_id_, msg);
        if (free_mix_wfx) CoTaskMemFree(mix_wfx);
        client->Release();
        return true;
    }

    HANDLE sample_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    client->SetEventHandle(sample_event);

    IAudioCaptureClient* capture = nullptr;
    hr = client->GetService(__uuidof(IAudioCaptureClient), reinterpret_cast<void**>(&capture));
    if (FAILED(hr) || !capture) {
        CloseHandle(sample_event);
        if (free_mix_wfx) CoTaskMemFree(mix_wfx);
        client->Release();
        return true;
    }

    const uint32_t src_rate = mix_wfx->nSamplesPerSec;
    const uint16_t src_channels = mix_wfx->nChannels;
    uint16_t bits = 32;
    bool is_float = true;
    inspect_wave_format(mix_wfx, bits, is_float);
    if (free_mix_wfx) CoTaskMemFree(mix_wfx);

    resampler_.configure(src_rate, engine_sample_rate_, 16384);
    std::vector<float> converted_stereo(16384 * 2, 0.0f);
    std::vector<float> zero_block(16384 * 2, 0.0f);

    hr = client->Start();
    if (FAILED(hr)) {
        capture->Release();
        CloseHandle(sample_event);
        client->Release();
        return true;
    }
    connected_.store(true, std::memory_order_release);

    // Loopback/process-loopback uses 10 ms wait timeout because Windows may stop signaling
    // sampleReadyEvent when the captured stream goes silent (pattern verified in OBS win-wasapi).
    const DWORD wait_ms = (mode_ == EndpointMode::InputCapture) ? INFINITE : 10;
    HANDLE handles[2] = {stop_event_, sample_event};
    bool invalidated = false;

    while (running_.load(std::memory_order_acquire)) {
        DWORD w = WaitForMultipleObjects(2, handles, FALSE, wait_ms);
        if (w == WAIT_OBJECT_0) break;

        UINT32 packet_frames = 0;
        while (running_.load(std::memory_order_acquire)) {
            hr = capture->GetNextPacketSize(&packet_frames);
            if (hr == AUDCLNT_E_DEVICE_INVALIDATED) {
                invalidated = true;
                break;
            }
            if (FAILED(hr) || packet_frames == 0) break;

            BYTE* data = nullptr;
            UINT32 num_frames = 0;
            DWORD flags = 0;
            hr = capture->GetBuffer(&data, &num_frames, &flags, nullptr, nullptr);
            if (hr == AUDCLNT_E_DEVICE_INVALIDATED) {
                invalidated = true;
                break;
            }
            if (FAILED(hr)) break;

            if (num_frames > 0) {
                const size_t fill = ring_.available_read();
                const size_t target = std::max<size_t>(480, (static_cast<size_t>(engine_sample_rate_) * 30) / 1000);
                const size_t deadband = std::max<size_t>(480, (static_cast<size_t>(engine_sample_rate_) * 20) / 1000);
                double drift = 1.0;
                if (src_rate != engine_sample_rate_ ||
                    fill < (target > deadband ? target - deadband : 0) ||
                    fill > target + deadband) {
                    const double err = static_cast<double>(fill) - static_cast<double>(target);
                    drift = 1.0 + std::clamp(err * 2e-7, -0.0005, 0.0005);
                }

                if ((flags & AUDCLNT_BUFFERFLAGS_SILENT) != 0 || !data) {
                    const size_t silent_in = std::min<size_t>(num_frames, zero_block.size() / 2);
                    if (src_rate == engine_sample_rate_ && std::abs(drift - 1.0) < 1e-6 && resampler_.is_phase_zero()) {
                        ring_.write(zero_block.data(), silent_in);
                    } else {
                        const size_t produced = resampler_.resample_stereo(
                            zero_block.data(), silent_in, converted_stereo.data(),
                            converted_stereo.size() / 2, drift);
                        if (produced > 0) ring_.write(converted_stereo.data(), produced);
                    }
                } else {
                    const size_t produced = resampler_.process_raw_to_stereo(
                        data, num_frames, src_channels, bits, is_float,
                        converted_stereo.data(), converted_stereo.size() / 2, drift);
                    if (produced > 0) ring_.write(converted_stereo.data(), produced);
                }
            }
            capture->ReleaseBuffer(num_frames);
        }

        if (invalidated) {
            if (logs_) logs_->push(1, node_id_, "Capture endpoint invalidated; reconnecting");
            break;
        }
    }

    client->Stop();
    capture->Release();
    CloseHandle(sample_event);
    client->Release();
    return running_.load(std::memory_order_acquire);
}

bool WasapiEndpointWorker::run_render_session() {
    IMMDeviceEnumerator* enumerator = nullptr;
    HRESULT hr = CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
                                  __uuidof(IMMDeviceEnumerator), reinterpret_cast<void**>(&enumerator));
    if (FAILED(hr) || !enumerator) return true;

    IMMDevice* device = nullptr;
    if (!device_id_.empty()) {
        std::wstring wid = utf8_to_wide(device_id_.c_str());
        hr = enumerator->GetDevice(wid.c_str(), &device);
    } else {
        hr = enumerator->GetDefaultAudioEndpoint(eRender, eConsole, &device);
    }
    enumerator->Release();

    if (FAILED(hr) || !device) {
        if (logs_) logs_->push(1, node_id_, "Render device unavailable; waiting for reconnect");
        return true;
    }

    IAudioClient* client = nullptr;
    hr = device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr, reinterpret_cast<void**>(&client));
    device->Release();
    if (FAILED(hr) || !client) return true;

    WAVEFORMATEX* mix_wfx = nullptr;
    hr = client->GetMixFormat(&mix_wfx);
    if (FAILED(hr) || !mix_wfx) {
        client->Release();
        return true;
    }

    // 40 ms shared-mode buffer for low latency + glitch resilience
    constexpr REFERENCE_TIME kRenderBufferDuration = 400000;
    hr = client->Initialize(
        AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_EVENTCALLBACK, kRenderBufferDuration, 0, mix_wfx, nullptr);
    if (FAILED(hr)) {
        char msg[256];
        std::snprintf(msg, sizeof(msg), "IAudioClient::Initialize(render) failed: 0x%08lX", hr);
        if (logs_) logs_->push(2, node_id_, msg);
        CoTaskMemFree(mix_wfx);
        client->Release();
        return true;
    }

    UINT32 buffer_frames = 0;
    client->GetBufferSize(&buffer_frames);

    HANDLE sample_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    client->SetEventHandle(sample_event);

    IAudioRenderClient* render = nullptr;
    hr = client->GetService(__uuidof(IAudioRenderClient), reinterpret_cast<void**>(&render));
    if (FAILED(hr) || !render) {
        CloseHandle(sample_event);
        CoTaskMemFree(mix_wfx);
        client->Release();
        return true;
    }

    const uint32_t dst_rate = mix_wfx->nSamplesPerSec;
    const uint16_t dst_channels = mix_wfx->nChannels;
    uint16_t bits = 32;
    bool is_float = true;
    inspect_wave_format(mix_wfx, bits, is_float);
    CoTaskMemFree(mix_wfx);

    const size_t buf_cap = std::max<size_t>(16384, static_cast<size_t>(buffer_frames) * 4);
    resampler_.configure(engine_sample_rate_, dst_rate, buf_cap);
    std::vector<float> pull_buf(buf_cap * 2, 0.0f);
    std::vector<float> resampled_buf(buf_cap * 2, 0.0f);

    const size_t prebuffer = std::max<size_t>(
        buffer_frames / 2,
        (static_cast<size_t>(engine_sample_rate_) * 25) / 1000);
    const size_t max_backlog = std::max<size_t>(
        prebuffer * 6,
        (static_cast<size_t>(engine_sample_rate_) * 160) / 1000);
    const size_t target = std::max<size_t>(
        buffer_frames / 2,
        (static_cast<size_t>(engine_sample_rate_) * 30) / 1000);
    const size_t deadband = std::max<size_t>(
        480,
        (static_cast<size_t>(engine_sample_rate_) * 20) / 1000);

    // Pre-fill initial buffer with silence
    BYTE* init_data = nullptr;
    if (SUCCEEDED(render->GetBuffer(buffer_frames, &init_data)) && init_data) {
        std::memset(init_data, 0, static_cast<size_t>(buffer_frames) * dst_channels * (bits / 8));
        render->ReleaseBuffer(buffer_frames, AUDCLNT_BUFFERFLAGS_SILENT);
    }

    hr = client->Start();
    if (FAILED(hr)) {
        render->Release();
        CloseHandle(sample_event);
        client->Release();
        return true;
    }
    connected_.store(true, std::memory_order_release);

    HANDLE handles[2] = {stop_event_, sample_event};
    bool invalidated = false;
    bool primed = false;
    bool fade_in = false;

    while (running_.load(std::memory_order_acquire)) {
        DWORD w = WaitForMultipleObjects(2, handles, FALSE, 100);
        if (w == WAIT_OBJECT_0) break;

        UINT32 padding = 0;
        hr = client->GetCurrentPadding(&padding);
        if (hr == AUDCLNT_E_DEVICE_INVALIDATED) {
            invalidated = true;
            break;
        }
        if (FAILED(hr) || padding >= buffer_frames) continue;

        const UINT32 frames_to_write = buffer_frames - padding;
        if (frames_to_write == 0) continue;

        BYTE* out_data = nullptr;
        hr = render->GetBuffer(frames_to_write, &out_data);
        if (hr == AUDCLNT_E_DEVICE_INVALIDATED) {
            invalidated = true;
            break;
        }
        if (FAILED(hr) || !out_data) continue;

        size_t avail = ring_.available_read();
        if (!primed) {
            const size_t start_threshold = std::max<size_t>(prebuffer, static_cast<size_t>(frames_to_write) + 32);
            if (avail < start_threshold) {
                render->ReleaseBuffer(frames_to_write, AUDCLNT_BUFFERFLAGS_SILENT);
                continue;
            }
            if (avail > max_backlog) {
                ring_.skip(avail - prebuffer);
            }
            resampler_.reset();
            primed = true;
            fade_in = true;
            avail = ring_.available_read();
        }

        if (avail > max_backlog) {
            ring_.skip(avail - prebuffer);
            resampler_.reset();
            fade_in = true;
            avail = ring_.available_read();
        }

        double drift = 1.0;
        if (dst_rate != engine_sample_rate_ ||
            avail < (target > deadband ? target - deadband : 0) ||
            avail > target + deadband) {
            const double err = static_cast<double>(avail) - static_cast<double>(target);
            drift = 1.0 + std::clamp(err * 2e-7, -0.0005, 0.0005);
        }

        const size_t need_in = resampler_.frames_needed_for_exact_out(frames_to_write, drift);
        if (avail < need_in) {
            ring_.record_underrun();
            primed = false;
            fade_in = true;
            if (avail > 0) {
                const size_t pull_n = std::min<size_t>(avail, pull_buf.size() / 2);
                ring_.read(pull_buf.data(), pull_n);
                const size_t produced = resampler_.resample_stereo(
                    pull_buf.data(), pull_n, resampled_buf.data(), frames_to_write, drift);
                if (produced > 0) {
                    const size_t fade_len = std::min<size_t>(32, produced);
                    for (size_t i = 0; i < fade_len; ++i) {
                        const float g = static_cast<float>(fade_len - 1 - i) / static_cast<float>(fade_len);
                        const size_t idx = (produced - fade_len + i) * 2;
                        resampled_buf[idx] *= g;
                        resampled_buf[idx + 1] *= g;
                    }
                }
                if (produced < frames_to_write) {
                    std::memset(
                        resampled_buf.data() + produced * 2, 0,
                        (frames_to_write - produced) * 2 * sizeof(float));
                }
                StereoConverterResampler::convert_stereo_float_to_raw(
                    resampled_buf.data(), frames_to_write, dst_channels, bits, is_float, out_data);
                resampler_.reset();
                render->ReleaseBuffer(frames_to_write, 0);
            } else {
                resampler_.reset();
                render->ReleaseBuffer(frames_to_write, AUDCLNT_BUFFERFLAGS_SILENT);
            }
            continue;
        }

        const size_t peek_n = std::min<size_t>(need_in, pull_buf.size() / 2);
        ring_.peek(pull_buf.data(), peek_n);
        const size_t consumed = resampler_.resample_exact_out(
            pull_buf.data(), peek_n, resampled_buf.data(), frames_to_write, drift);
        if (consumed > 0) {
            ring_.skip(consumed);
        }

        if (fade_in) {
            const size_t fade_len = std::min<size_t>(64, frames_to_write);
            for (size_t i = 0; i < fade_len; ++i) {
                const float g = static_cast<float>(i + 1) / static_cast<float>(fade_len);
                resampled_buf[i * 2] *= g;
                resampled_buf[i * 2 + 1] *= g;
            }
            fade_in = false;
        }

        StereoConverterResampler::convert_stereo_float_to_raw(
            resampled_buf.data(), frames_to_write, dst_channels, bits, is_float, out_data);
        render->ReleaseBuffer(frames_to_write, 0);
    }

    if (invalidated && logs_) {
        logs_->push(1, node_id_, "Render endpoint invalidated; reconnecting");
    }

    client->Stop();
    render->Release();
    CloseHandle(sample_event);
    client->Release();
    return running_.load(std::memory_order_acquire);
}

} // namespace af

