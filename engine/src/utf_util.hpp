#pragma once

#include <windows.h>
#include <cstring>
#include <string>

namespace af {

inline std::string wide_to_utf8(const wchar_t* wstr) {
    if (!wstr || !*wstr) return {};
    int needed = WideCharToMultiByte(CP_UTF8, 0, wstr, -1, nullptr, 0, nullptr, nullptr);
    if (needed <= 1) return {};
    std::string out(static_cast<size_t>(needed - 1), '\0');
    WideCharToMultiByte(CP_UTF8, 0, wstr, -1, out.data(), needed, nullptr, nullptr);
    return out;
}

inline std::wstring utf8_to_wide(const char* str) {
    if (!str || !*str) return {};
    int needed = MultiByteToWideChar(CP_UTF8, 0, str, -1, nullptr, 0);
    if (needed <= 1) return {};
    std::wstring out(static_cast<size_t>(needed - 1), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, str, -1, out.data(), needed);
    return out;
}

inline void copy_utf8_trunc(char* dst, size_t cap, const std::string& src) noexcept {
    if (!dst || cap == 0) return;
    std::memset(dst, 0, cap);
    if (!src.empty()) {
        std::strncpy(dst, src.c_str(), cap - 1);
    }
}

} // namespace af

