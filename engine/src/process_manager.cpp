#include "process_manager.hpp"
#include "utf_util.hpp"

#include <windows.h>
#include <tlhelp32.h>
#include <mmdeviceapi.h>
#include <audiopolicy.h>
#include <endpointvolume.h>

#include <algorithm>
#include <unordered_map>

namespace af {

namespace {

struct WindowEnumState {
    std::unordered_map<DWORD, std::string> titles;
};

BOOL CALLBACK enum_windows_cb(HWND hwnd, LPARAM lparam) {
    if (!IsWindowVisible(hwnd)) return TRUE;
    int len = GetWindowTextLengthW(hwnd);
    if (len <= 0) return TRUE;

    DWORD pid = 0;
    GetWindowThreadProcessId(hwnd, &pid);
    if (pid == 0) return TRUE;

    auto* state = reinterpret_cast<WindowEnumState*>(lparam);
    if (state->titles.find(pid) != state->titles.end()) return TRUE;

    std::wstring buf(static_cast<size_t>(len) + 1, L'\0');
    GetWindowTextW(hwnd, buf.data(), len + 1);
    std::string utf8 = wide_to_utf8(buf.c_str());
    if (!utf8.empty()) {
        state->titles[pid] = std::move(utf8);
    }
    return TRUE;
}

struct SessionMetrics {
    bool active{false};
    float peak{0.0f};
};

std::unordered_map<DWORD, SessionMetrics> collect_audio_sessions() {
    std::unordered_map<DWORD, SessionMetrics> sessions;

    HRESULT hr_co = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    IMMDeviceEnumerator* enumerator = nullptr;
    if (FAILED(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
                                __uuidof(IMMDeviceEnumerator), reinterpret_cast<void**>(&enumerator))) ||
        !enumerator) {
        if (SUCCEEDED(hr_co)) CoUninitialize();
        return sessions;
    }

    IMMDeviceCollection* collection = nullptr;
    if (SUCCEEDED(enumerator->EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE, &collection)) && collection) {
        UINT dev_count = 0;
        collection->GetCount(&dev_count);
        for (UINT d = 0; d < dev_count; ++d) {
            IMMDevice* dev = nullptr;
            if (FAILED(collection->Item(d, &dev)) || !dev) continue;

            IAudioSessionManager2* mgr = nullptr;
            if (SUCCEEDED(dev->Activate(__uuidof(IAudioSessionManager2), CLSCTX_ALL, nullptr,
                                        reinterpret_cast<void**>(&mgr))) && mgr) {
                IAudioSessionEnumerator* sess_enum = nullptr;
                if (SUCCEEDED(mgr->GetSessionEnumerator(&sess_enum)) && sess_enum) {
                    int sess_count = 0;
                    sess_enum->GetCount(&sess_count);
                    for (int s = 0; s < sess_count; ++s) {
                        IAudioSessionControl* ctrl = nullptr;
                        if (FAILED(sess_enum->GetSession(s, &ctrl)) || !ctrl) continue;

                        IAudioSessionControl2* ctrl2 = nullptr;
                        if (SUCCEEDED(ctrl->QueryInterface(__uuidof(IAudioSessionControl2),
                                                           reinterpret_cast<void**>(&ctrl2))) && ctrl2) {
                            DWORD pid = 0;
                            if (SUCCEEDED(ctrl2->GetProcessId(&pid)) && pid != 0) {
                                AudioSessionState st = AudioSessionStateInactive;
                                ctrl->GetState(&st);
                                float peak = 0.0f;
                                IAudioMeterInformation* meter = nullptr;
                                if (SUCCEEDED(ctrl->QueryInterface(__uuidof(IAudioMeterInformation),
                                                                   reinterpret_cast<void**>(&meter))) && meter) {
                                    meter->GetPeakValue(&peak);
                                    meter->Release();
                                }
                                auto& entry = sessions[pid];
                                entry.active = entry.active || (st == AudioSessionStateActive) || (peak > 0.0001f);
                                entry.peak = std::max(entry.peak, peak);
                            }
                            ctrl2->Release();
                        }
                        ctrl->Release();
                    }
                    sess_enum->Release();
                }
                mgr->Release();
            }
            dev->Release();
        }
        collection->Release();
    }

    enumerator->Release();
    if (SUCCEEDED(hr_co)) CoUninitialize();
    return sessions;
}

} // namespace

std::vector<af_process_info_t> ProcessManager::enumerate_processes() {
    std::vector<af_process_info_t> out;

    WindowEnumState win_state;
    EnumWindows(enum_windows_cb, reinterpret_cast<LPARAM>(&win_state));

    auto audio_sessions = collect_audio_sessions();

    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) {
        return out;
    }

    PROCESSENTRY32W pe{};
    pe.dwSize = sizeof(pe);
    if (Process32FirstW(snap, &pe)) {
        do {
            if (pe.th32ProcessID == 0) continue;

            af_process_info_t info{};
            info.pid = pe.th32ProcessID;
            info.parent_pid = pe.th32ParentProcessID;
            copy_utf8_trunc(info.exe_name, sizeof(info.exe_name), wide_to_utf8(pe.szExeFile));

            HANDLE proc = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pe.th32ProcessID);
            if (proc) {
                wchar_t path_buf[MAX_PATH] = {};
                DWORD path_len = MAX_PATH;
                if (QueryFullProcessImageNameW(proc, 0, path_buf, &path_len)) {
                    copy_utf8_trunc(info.exe_path, sizeof(info.exe_path), wide_to_utf8(path_buf));
                }
                CloseHandle(proc);
            }

            if (auto it_win = win_state.titles.find(pe.th32ProcessID); it_win != win_state.titles.end()) {
                copy_utf8_trunc(info.window_title, sizeof(info.window_title), it_win->second);
            }

            if (auto it_sess = audio_sessions.find(pe.th32ProcessID); it_sess != audio_sessions.end()) {
                info.has_audio_session = 1;
                info.session_peak = it_sess->second.peak;
            }

            out.push_back(info);
        } while (Process32NextW(snap, &pe));
    }

    CloseHandle(snap);

    // Sort active audio-producing processes by peak first, then other audio sessions, then name & PID
    std::sort(out.begin(), out.end(), [](const af_process_info_t& a, const af_process_info_t& b) {
        if (a.has_audio_session != b.has_audio_session) {
            return a.has_audio_session > b.has_audio_session;
        }
        if (a.has_audio_session && (std::abs(a.session_peak - b.session_peak) > 0.001f)) {
            return a.session_peak > b.session_peak;
        }
        int cmp = _stricmp(a.exe_name, b.exe_name);
        if (cmp != 0) return cmp < 0;
        return a.pid < b.pid;
    });

    return out;
}

} // namespace af

