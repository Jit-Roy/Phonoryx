#pragma once

#include <vector>
#include "audioflow_capi.h"

namespace af {

/**
 * Enumerates running Windows processes alongside active WASAPI IAudioSessionManager2
 * audio sessions so individual audio-producing process instances (PIDs) can be targeted.
 */
class ProcessManager {
public:
    static std::vector<af_process_info_t> enumerate_processes();
};

} // namespace af

