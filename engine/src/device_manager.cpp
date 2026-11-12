#include "device_manager.hpp"
#include "utf_util.hpp"

#include <audioclient.h>
#include <functiondiscoverykeys_devpkey.h>
#include <propsys.h>

#include <algorithm>
#include <cctype>
#include <cstdio>

namespace af {

// PKEY_AudioEndpoint_FormFactor = {1da5d803-d492-4edd-8c23-e0c0ffee7f0e}, 0
static const PROPERTYKEY AF_PKEY_AudioEndpoint_FormFactor = {
    {0x1da5d803, 0xd492, 0x4edd, {0x8c, 0x23, 0xe0, 0xc0, 0xff, 0xee, 0x7f, 0x0e}},
    0
};

class DeviceManager::NotificationClient final : public IMMNotificationClient {
public:
    NotificationClient(std::atomic<uint64_t>* seq, LogQueue* logs)
        : seq_(seq), logs_(logs) {}

    ULONG STDMETHODCALLTYPE AddRef() override {
        return static_cast<ULONG>(InterlockedIncrement(&ref_count_));
    }

    ULONG STDMETHODCALLTYPE Release() override {
        ULONG rc = static_cast<ULONG>(InterlockedDecrement(&ref_count_));
        if (rc == 0) delete this;
        return rc;
    }

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppvObject) override {
        if (!ppvObject) return E_POINTER;
        if (riid == __uuidof(IUnknown) || riid == __uuidof(IMMNotificationClient)) {
            *ppvObject = static_cast<IMMNotificationClient*>(this);
            AddRef();
            return S_OK;
        }
        *ppvObject = nullptr;
        return E_NOINTERFACE;
    }

    HRESULT STDMETHODCALLTYPE OnDeviceStateChanged(LPCWSTR pwstrDeviceId, DWORD dwNewState) override {
        seq_->fetch_add(1, std::memory_order_release);
        if (logs_) {
            char buf[256];
            std::string id = wide_to_utf8(pwstrDeviceId);
            std::snprintf(buf, sizeof(buf), "Audio endpoint state changed (0x%08lX): %s",
                          dwNewState, id.c_str());
            logs_->push(0, 0, buf);
        }
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE OnDeviceAdded(LPCWSTR pwstrDeviceId) override {
        seq_->fetch_add(1, std::memory_order_release);
        if (logs_) {
            char buf[256];
            std::string id = wide_to_utf8(pwstrDeviceId);
            std::snprintf(buf, sizeof(buf), "Audio endpoint added: %s", id.c_str());
            logs_->push(0, 0, buf);
        }
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE OnDeviceRemoved(LPCWSTR pwstrDeviceId) override {
        seq_->fetch_add(1, std::memory_order_release);
        if (logs_) {
            char buf[256];
            std::string id = wide_to_utf8(pwstrDeviceId);
            std::snprintf(buf, sizeof(buf), "Audio endpoint removed: %s", id.c_str());
            logs_->push(1, 0, buf);
        }
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE OnDefaultDeviceChanged(EDataFlow, ERole, LPCWSTR) override {
        seq_->fetch_add(1, std::memory_order_release);
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE OnPropertyValueChanged(LPCWSTR, const PROPERTYKEY) override {
        seq_->fetch_add(1, std::memory_order_release);
        return S_OK;
    }

private:
    volatile LONG ref_count_{1};
    std::atomic<uint64_t>* seq_;
    LogQueue* logs_;
};

DeviceManager::DeviceManager(LogQueue* logs) : logs_(logs) {}

DeviceManager::~DeviceManager() {
    shutdown();
}

bool DeviceManager::initialize() {
    if (enumerator_) return true;
    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (SUCCEEDED(hr)) {
        com_owned_ = true;
    }
    hr = CoCreateInstance(
        __uuidof(MMDeviceEnumerator),
        nullptr,
        CLSCTX_ALL,
        __uuidof(IMMDeviceEnumerator),
        reinterpret_cast<void**>(&enumerator_));
    if (FAILED(hr) || !enumerator_) {
        if (logs_) logs_->push(2, 0, "MMDeviceEnumerator creation failed");
        return false;
    }
    client_ = new NotificationClient(&change_seq_, logs_);
    enumerator_->RegisterEndpointNotificationCallback(client_);
    return true;
}

void DeviceManager::shutdown() {
    if (enumerator_ && client_) {
        enumerator_->UnregisterEndpointNotificationCallback(client_);
    }
    if (client_) {
        client_->Release();
        client_ = nullptr;
    }
    if (enumerator_) {
        enumerator_->Release();
        enumerator_ = nullptr;
    }
    if (com_owned_) {
        CoUninitialize();
        com_owned_ = false;
    }
}

bool DeviceManager::is_virtual_device_name(const std::string& name) {
    std::string lower = name;
    std::transform(lower.begin(), lower.end(), lower.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return lower.find("audioflow") != std::string::npos ||
           lower.find("virtual") != std::string::npos ||
           lower.find("cable") != std::string::npos ||
           lower.find("voicemeeter") != std::string::npos ||
           lower.find("wave link") != std::string::npos;
}

static int32_t map_role_hint(EDataFlow flow, DWORD form_factor, bool is_virtual) {
    if (is_virtual) return AF_ROLE_VIRTUAL;
    if (flow == eRender) {
        if (form_factor == Headphones || form_factor == Headset) return AF_ROLE_HEADPHONES;
        return AF_ROLE_SPEAKERS;
    }
    if (form_factor == LineLevel) return AF_ROLE_LINE_IN;
    if (form_factor == Microphone || form_factor == Headset) return AF_ROLE_MICROPHONE;
    return AF_ROLE_UNKNOWN;
}

std::vector<af_device_info_t> DeviceManager::enumerate_devices() {
    std::vector<af_device_info_t> results;
    if (!enumerator_ && !initialize()) {
        return results;
    }

    // Create a local enumerator on calling thread if MTA/STA differs.
    IMMDeviceEnumerator* local_enum = enumerator_;
    bool release_local = false;
    HRESULT hr_co = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (hr_co == RPC_E_CHANGED_MODE) {
        IMMDeviceEnumerator* tmp = nullptr;
        if (SUCCEEDED(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
                                       __uuidof(IMMDeviceEnumerator), reinterpret_cast<void**>(&tmp)) && tmp)) {
            local_enum = tmp;
            release_local = true;
        }
    }

    auto collect_flow = [&](EDataFlow flow) {
        std::string default_id;
        IMMDevice* def_dev = nullptr;
        if (SUCCEEDED(local_enum->GetDefaultAudioEndpoint(flow, eConsole, &def_dev)) && def_dev) {
            LPWSTR wdef = nullptr;
            if (SUCCEEDED(def_dev->GetId(&wdef)) && wdef) {
                default_id = wide_to_utf8(wdef);
                CoTaskMemFree(wdef);
            }
            def_dev->Release();
        }

        IMMDeviceCollection* collection = nullptr;
        HRESULT hr = local_enum->EnumAudioEndpoints(flow, DEVICE_STATE_ACTIVE, &collection);
        if (FAILED(hr) || !collection) return;

        UINT count = 0;
        collection->GetCount(&count);
        for (UINT i = 0; i < count; ++i) {
            IMMDevice* dev = nullptr;
            if (FAILED(collection->Item(i, &dev)) || !dev) continue;

            af_device_info_t info{};
            info.flow = (flow == eRender) ? AF_FLOW_RENDER : AF_FLOW_CAPTURE;

            LPWSTR wid = nullptr;
            if (SUCCEEDED(dev->GetId(&wid)) && wid) {
                std::string id_utf8 = wide_to_utf8(wid);
                copy_utf8_trunc(info.id, sizeof(info.id), id_utf8);
                info.is_default = (!default_id.empty() && default_id == id_utf8) ? 1 : 0;
                CoTaskMemFree(wid);
            }

            DWORD state = 0;
            if (SUCCEEDED(dev->GetState(&state))) {
                info.state = state;
            }

            DWORD form_factor = 0;
            IPropertyStore* props = nullptr;
            if (SUCCEEDED(dev->OpenPropertyStore(STGM_READ, &props)) && props) {
                PROPVARIANT pv;
                PropVariantInit(&pv);
                if (SUCCEEDED(props->GetValue(PKEY_Device_FriendlyName, &pv)) && pv.vt == VT_LPWSTR) {
                    copy_utf8_trunc(info.name, sizeof(info.name), wide_to_utf8(pv.pwszVal));
                }
                PropVariantClear(&pv);

                PropVariantInit(&pv);
                if (SUCCEEDED(props->GetValue(AF_PKEY_AudioEndpoint_FormFactor, &pv)) &&
                    (pv.vt == VT_UI4 || pv.vt == VT_I4)) {
                    form_factor = pv.ulVal;
                }
                PropVariantClear(&pv);
                props->Release();
            }

            info.is_virtual = is_virtual_device_name(info.name) ? 1 : 0;
            info.role_hint = map_role_hint(flow, form_factor, info.is_virtual != 0);

            IAudioClient* client = nullptr;
            if (SUCCEEDED(dev->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                                        reinterpret_cast<void**>(&client))) && client) {
                WAVEFORMATEX* wfx = nullptr;
                if (SUCCEEDED(client->GetMixFormat(&wfx)) && wfx) {
                    info.sample_rate = wfx->nSamplesPerSec;
                    info.channels = wfx->nChannels;
                    CoTaskMemFree(wfx);
                }
                client->Release();
            }

            results.push_back(info);
            dev->Release();
        }
        collection->Release();
    };

    collect_flow(eRender);
    collect_flow(eCapture);

    if (release_local && local_enum) local_enum->Release();
    if (SUCCEEDED(hr_co)) CoUninitialize();
    return results;
}

} // namespace af

