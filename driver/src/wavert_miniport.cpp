#include "driver.h"

#define AF_DEFAULT_CHANNELS 2
#define AF_MIN_SR 44100
#define AF_MAX_SR 48000

static KSDATARANGE_AUDIO g_wave_data_ranges[] = {
    {
        {
            sizeof(KSDATARANGE_AUDIO),
            0,
            0,
            0,
            STATICGUIDOF(KSDATAFORMAT_TYPE_AUDIO),
            STATICGUIDOF(KSDATAFORMAT_SUBTYPE_PCM),
            STATICGUIDOF(KSDATAFORMAT_SPECIFIER_WAVEFORMATEX)
        },
        AF_DEFAULT_CHANNELS,
        16,
        32,
        AF_MIN_SR,
        AF_MAX_SR
    },
    {
        {
            sizeof(KSDATARANGE_AUDIO),
            0,
            0,
            0,
            STATICGUIDOF(KSDATAFORMAT_TYPE_AUDIO),
            STATICGUIDOF(KSDATAFORMAT_SUBTYPE_IEEE_FLOAT),
            STATICGUIDOF(KSDATAFORMAT_SPECIFIER_WAVEFORMATEX)
        },
        AF_DEFAULT_CHANNELS,
        32,
        32,
        AF_MIN_SR,
        AF_MAX_SR
    }
};

static PKSDATARANGE g_wave_range_ptrs[] = {
    (PKSDATARANGE)&g_wave_data_ranges[0],
    (PKSDATARANGE)&g_wave_data_ranges[1]
};

static KSDATARANGE g_bridge_range = {
    sizeof(KSDATARANGE),
    0,
    0,
    0,
    STATICGUIDOF(KSDATAFORMAT_TYPE_AUDIO),
    STATICGUIDOF(KSDATAFORMAT_SUBTYPE_ANALOG),
    STATICGUIDOF(KSDATAFORMAT_SPECIFIER_NONE)
};

static PKSDATARANGE g_bridge_range_ptrs[] = { &g_bridge_range };

static PCPIN_DESCRIPTOR g_wave_render_pins[] = {
    {
        1, 1, 0, nullptr,
        {
            0, nullptr, 0, nullptr,
            SIZEOF_ARRAY(g_wave_range_ptrs), g_wave_range_ptrs,
            KSPIN_DATAFLOW_IN,
            KSPIN_COMMUNICATION_SINK,
            &KSCATEGORY_AUDIO,
            nullptr,
            0
        }
    },
    {
        0, 0, 0, nullptr,
        {
            0, nullptr, 0, nullptr,
            SIZEOF_ARRAY(g_bridge_range_ptrs), g_bridge_range_ptrs,
            KSPIN_DATAFLOW_OUT,
            KSPIN_COMMUNICATION_NONE,
            &KSCATEGORY_AUDIO,
            nullptr,
            0
        }
    }
};

static PCPIN_DESCRIPTOR g_wave_capture_pins[] = {
    {
        1, 1, 0, nullptr,
        {
            0, nullptr, 0, nullptr,
            SIZEOF_ARRAY(g_wave_range_ptrs), g_wave_range_ptrs,
            KSPIN_DATAFLOW_OUT,
            KSPIN_COMMUNICATION_SINK,
            &KSCATEGORY_AUDIO,
            nullptr,
            0
        }
    },
    {
        0, 0, 0, nullptr,
        {
            0, nullptr, 0, nullptr,
            SIZEOF_ARRAY(g_bridge_range_ptrs), g_bridge_range_ptrs,
            KSPIN_DATAFLOW_IN,
            KSPIN_COMMUNICATION_NONE,
            &KSCATEGORY_AUDIO,
            nullptr,
            0
        }
    }
};

static PCCONNECTION_DESCRIPTOR g_wave_render_conns[] = {
    { PCFILTER_NODE, 0, PCFILTER_NODE, 1 }
};

static PCCONNECTION_DESCRIPTOR g_wave_capture_conns[] = {
    { PCFILTER_NODE, 1, PCFILTER_NODE, 0 }
};

static PCFILTER_DESCRIPTOR g_wave_render_filter = {
    0, nullptr,
    sizeof(PCPIN_DESCRIPTOR), SIZEOF_ARRAY(g_wave_render_pins), g_wave_render_pins,
    0, 0, nullptr,
    SIZEOF_ARRAY(g_wave_render_conns), g_wave_render_conns,
    0, nullptr
};

static PCFILTER_DESCRIPTOR g_wave_capture_filter = {
    0, nullptr,
    sizeof(PCPIN_DESCRIPTOR), SIZEOF_ARRAY(g_wave_capture_pins), g_wave_capture_pins,
    0, 0, nullptr,
    SIZEOF_ARRAY(g_wave_capture_conns), g_wave_capture_conns,
    0, nullptr
};

class CAfWaveRtStream : public IMiniportWaveRTStreamNotification, public CUnknown {
public:
    CAfWaveRtStream(
        _In_ AF_ENDPOINT_SLOT* slot,
        _In_ BOOLEAN is_capture,
        _In_ PPORTWAVERTSTREAM port_stream,
        _In_ ULONG sample_rate,
        _In_ ULONG channels,
        _In_ ULONG bits_per_sample,
        _In_ BOOLEAN is_float)
        : CUnknown(nullptr)
        , slot_(slot)
        , is_capture_(is_capture)
        , port_stream_(port_stream)
        , sample_rate_(sample_rate)
        , channels_(channels)
        , bits_per_sample_(bits_per_sample)
        , is_float_(is_float)
    {
        if (port_stream_) port_stream_->AddRef();
        KeInitializeSpinLock(&timer_lock_);
        timer_ = ExAllocateTimer(TimerCallback, this, EX_TIMER_HIGH_RESOLUTION);
    }

    ~CAfWaveRtStream() override {
        SetState(KSSTATE_STOP);
        if (timer_) {
            ExDeleteTimer(timer_, TRUE, TRUE, nullptr);
            timer_ = nullptr;
        }
        if (dma_buffer_mdl_) {
            port_stream_->FreePagesFromMdl(dma_buffer_mdl_);
            dma_buffer_mdl_ = nullptr;
        }
        if (port_stream_) {
            port_stream_->Release();
            port_stream_ = nullptr;
        }
    }

    STDMETHODIMP NonDelegatingQueryInterface(REFIID iid, PVOID* ppv) override {
        if (IsEqualIID(iid, IID_IUnknown) ||
            IsEqualIID(iid, IID_IMiniportWaveRTStream) ||
            IsEqualIID(iid, IID_IMiniportWaveRTStreamNotification)) {
            *ppv = static_cast<IMiniportWaveRTStreamNotification*>(this);
            AddRef();
            return STATUS_SUCCESS;
        }
        *ppv = nullptr;
        return STATUS_INVALID_PARAMETER;
    }

    STDMETHODIMP_(ULONG) AddRef() override { return CUnknown::NonDelegatingAddRef(); }
    STDMETHODIMP_(ULONG) Release() override { return CUnknown::NonDelegatingRelease(); }

    STDMETHODIMP AllocateAudioBuffer(
        _In_ ULONG RequestedSize,
        _Out_ PMDL* AudioBufferMdl,
        _Out_ ULONG* ActualSize,
        _Out_ ULONG* OffsetFromFirstPage,
        _Out_ MEMORY_CACHING_TYPE* CacheType) override
    {
        ULONG frame_bytes = (bits_per_sample_ / 8) * channels_;
        ULONG aligned_size = (RequestedSize / frame_bytes) * frame_bytes;
        if (aligned_size < frame_bytes * 64) {
            aligned_size = frame_bytes * 64;
        }

        PHYSICAL_ADDRESS low = { 0 };
        PHYSICAL_ADDRESS high = { 0xFFFFFFFFFFFFFFFFULL };
        PHYSICAL_ADDRESS zero = { 0 };

        PMDL mdl = port_stream_->AllocatePagesForMdl(high, aligned_size);
        if (!mdl) {
            return STATUS_INSUFFICIENT_RESOURCES;
        }
        PVOID va = port_stream_->MapAllocatedPages(mdl, MmCached);
        if (!va) {
            port_stream_->FreePagesFromMdl(mdl);
            return STATUS_INSUFFICIENT_RESOURCES;
        }
        RtlZeroMemory(va, aligned_size);

        dma_buffer_mdl_ = mdl;
        dma_buffer_ = (uint8_t*)va;
        dma_buffer_size_ = aligned_size;
        dma_frame_pos_ = 0;

        *AudioBufferMdl = mdl;
        *ActualSize = aligned_size;
        *OffsetFromFirstPage = 0;
        *CacheType = MmCached;
        UNREFERENCED_PARAMETER(low);
        UNREFERENCED_PARAMETER(zero);
        return STATUS_SUCCESS;
    }

    STDMETHODIMP_(VOID) FreeAudioBuffer(_In_opt_ PMDL AudioBufferMdl, _In_ ULONG BufferSize) override {
        UNREFERENCED_PARAMETER(BufferSize);
        if (dma_buffer_ && dma_buffer_mdl_) {
            port_stream_->UnmapAllocatedPages(dma_buffer_, dma_buffer_mdl_);
            dma_buffer_ = nullptr;
        }
        if (AudioBufferMdl) {
            port_stream_->FreePagesFromMdl(AudioBufferMdl);
            if (AudioBufferMdl == dma_buffer_mdl_) {
                dma_buffer_mdl_ = nullptr;
            }
        }
    }

    STDMETHODIMP_(VOID) GetHWLatency(_Out_ KSRTAUDIO_HWLATENCY* HwLatency) override {
        HwLatency->FifoSize = (bits_per_sample_ / 8) * channels_ * 64;
        HwLatency->ChipsetDelay = 0;
        HwLatency->CodecDelay = 0;
    }

    STDMETHODIMP GetPositionRegister(_Out_ KSRTAUDIO_HWREGISTER* Register) override {
        UNREFERENCED_PARAMETER(Register);
        return STATUS_NOT_SUPPORTED;
    }

    STDMETHODIMP GetClockRegister(_Out_ KSRTAUDIO_HWREGISTER* Register) override {
        UNREFERENCED_PARAMETER(Register);
        return STATUS_NOT_SUPPORTED;
    }

    STDMETHODIMP GetPosition(_Out_ KSAUDIO_POSITION* Position) override {
        Position->PlayOffset = current_byte_pos_;
        Position->WriteOffset = current_byte_pos_;
        return STATUS_SUCCESS;
    }

    STDMETHODIMP SetFormat(_In_ PKSDATAFORMAT DataFormat) override {
        UNREFERENCED_PARAMETER(DataFormat);
        return STATUS_SUCCESS;
    }

    STDMETHODIMP SetState(_In_ KSSTATE State) override {
        state_ = State;
        if (State == KSSTATE_RUN) {
            last_qpc_ = KeQueryPerformanceCounter(&qpc_freq_);
            if (timer_) {
                EXT_SET_PARAMETERS params;
                ExInitializeSetTimerParameters(&params);
                ExSetTimer(timer_, -50000LL, 5000LL, &params); /* 5ms period */
            }
        } else {
            if (timer_) {
                ExCancelTimer(timer_, nullptr);
            }
            if (State == KSSTATE_STOP) {
                current_byte_pos_ = 0;
                dma_frame_pos_ = 0;
            }
        }
        return STATUS_SUCCESS;
    }

    STDMETHODIMP AllocateBufferWithNotification(
        _In_ ULONG NotificationCount,
        _In_ ULONG RequestedSize,
        _Out_ PMDL* AudioBufferMdl,
        _Out_ ULONG* ActualSize,
        _Out_ ULONG* OffsetFromFirstPage,
        _Out_ MEMORY_CACHING_TYPE* CacheType) override
    {
        notify_count_ = NotificationCount ? NotificationCount : 2;
        return AllocateAudioBuffer(RequestedSize, AudioBufferMdl, ActualSize, OffsetFromFirstPage, CacheType);
    }

    STDMETHODIMP_(VOID) FreeBufferWithNotification(
        _In_ PMDL AudioBufferMdl,
        _In_ ULONG BufferSize) override
    {
        FreeAudioBuffer(AudioBufferMdl, BufferSize);
    }

    STDMETHODIMP RegisterNotificationEvent(_In_ PKEVENT NotificationEvent) override {
        notify_event_ = NotificationEvent;
        return STATUS_SUCCESS;
    }

    STDMETHODIMP UnregisterNotificationEvent(_In_ PKEVENT NotificationEvent) override {
        if (notify_event_ == NotificationEvent) {
            notify_event_ = nullptr;
        }
        return STATUS_SUCCESS;
    }

private:
    static VOID TimerCallback(_In_ PEX_TIMER Timer, _In_opt_ PVOID Context) {
        UNREFERENCED_PARAMETER(Timer);
        if (Context) {
            static_cast<CAfWaveRtStream*>(Context)->OnTick();
        }
    }

    VOID OnTick() {
        if (state_ != KSSTATE_RUN || !dma_buffer_ || dma_buffer_size_ == 0 || !slot_ || !slot_->ring) {
            return;
        }

        LARGE_INTEGER now = KeQueryPerformanceCounter(nullptr);
        LONGLONG elapsed_qpc = now.QuadPart - last_qpc_.QuadPart;
        last_qpc_ = now;
        if (elapsed_qpc <= 0 || qpc_freq_.QuadPart <= 0) {
            return;
        }

        ULONG frames_due = (ULONG)((elapsed_qpc * (LONGLONG)sample_rate_) / qpc_freq_.QuadPart);
        if (frames_due == 0) frames_due = sample_rate_ / 200;
        ULONG frame_bytes = (bits_per_sample_ / 8) * channels_;
        ULONG total_dma_frames = dma_buffer_size_ / frame_bytes;
        if (frames_due > total_dma_frames / 2) {
            frames_due = total_dma_frames / 2;
        }

        XSTATE_SAVE save_state;
        NTSTATUS fp_st = KeSaveExtendedProcessorState(XSTATE_MASK_LEGACY_FLOATING_POINT, &save_state);

        AUDIOFLOW_SHARED_RING* ring = slot_->ring;
        for (ULONG f = 0; f < frames_due; ++f) {
            ULONG dma_idx = dma_frame_pos_ % total_dma_frames;
            uint8_t* sample_ptr = dma_buffer_ + dma_idx * frame_bytes;

            if (!is_capture_) {
                /* Render endpoint: KS DMA -> Shared Ring (readable by AudioFlow user engine) */
                float l = 0.0f, r = 0.0f;
                ReadDmaSample(sample_ptr, l, r);
                uint64_t w = ring->write_frame_index;
                uint64_t r_idx = ring->read_frame_index;
                if (w - r_idx < ring->frame_capacity) {
                    ULONG slot_idx = (ULONG)(w % ring->frame_capacity);
                    ring->samples[slot_idx * 2 + 0] = l;
                    ring->samples[slot_idx * 2 + 1] = r;
                    MemoryBarrier();
                    ring->write_frame_index = w + 1;
                } else {
                    ring->xruns++;
                }
            } else {
                /* Capture endpoint: Shared Ring (written by AudioFlow user engine) -> KS DMA */
                float l = 0.0f, r = 0.0f;
                uint64_t r_idx = ring->read_frame_index;
                uint64_t w_idx = ring->write_frame_index;
                if (w_idx > r_idx) {
                    ULONG slot_idx = (ULONG)(r_idx % ring->frame_capacity);
                    l = ring->samples[slot_idx * 2 + 0];
                    r = ring->samples[slot_idx * 2 + 1];
                    MemoryBarrier();
                    ring->read_frame_index = r_idx + 1;
                }
                WriteDmaSample(sample_ptr, l, r);
            }

            dma_frame_pos_ = (dma_frame_pos_ + 1) % total_dma_frames;
        }

        if (NT_SUCCESS(fp_st)) {
            KeRestoreExtendedProcessorState(&save_state);
        }

        ULONG prev_pos = current_byte_pos_;
        current_byte_pos_ = (dma_frame_pos_ * frame_bytes) % dma_buffer_size_;
        if (notify_event_ && notify_count_ > 0) {
            ULONG notify_block = dma_buffer_size_ / notify_count_;
            if (notify_block > 0 && (prev_pos / notify_block) != (current_byte_pos_ / notify_block)) {
                KeSetEvent(notify_event_, 0, FALSE);
            }
        }
    }

    FORCEINLINE VOID ReadDmaSample(const uint8_t* ptr, float& l, float& r) {
        if (is_float_ && bits_per_sample_ == 32) {
            const float* fp = (const float*)ptr;
            l = fp[0];
            r = channels_ > 1 ? fp[1] : l;
        } else if (bits_per_sample_ == 32) {
            const int32_t* ip = (const int32_t*)ptr;
            l = (float)ip[0] / 2147483648.0f;
            r = channels_ > 1 ? ((float)ip[1] / 2147483648.0f) : l;
        } else {
            const int16_t* sp = (const int16_t*)ptr;
            l = (float)sp[0] / 32768.0f;
            r = channels_ > 1 ? ((float)sp[1] / 32768.0f) : l;
        }
    }

    FORCEINLINE VOID WriteDmaSample(uint8_t* ptr, float l, float r) {
        if (l > 1.0f) l = 1.0f; else if (l < -1.0f) l = -1.0f;
        if (r > 1.0f) r = 1.0f; else if (r < -1.0f) r = -1.0f;
        if (is_float_ && bits_per_sample_ == 32) {
            float* fp = (float*)ptr;
            fp[0] = l;
            if (channels_ > 1) fp[1] = r;
        } else if (bits_per_sample_ == 32) {
            int32_t* ip = (int32_t*)ptr;
            ip[0] = (int32_t)(l * 2147483647.0f);
            if (channels_ > 1) ip[1] = (int32_t)(r * 2147483647.0f);
        } else {
            int16_t* sp = (int16_t*)ptr;
            sp[0] = (int16_t)(l * 32767.0f);
            if (channels_ > 1) sp[1] = (int16_t)(r * 32767.0f);
        }
    }

    AF_ENDPOINT_SLOT* slot_;
    BOOLEAN is_capture_;
    PPORTWAVERTSTREAM port_stream_;
    ULONG sample_rate_;
    ULONG channels_;
    ULONG bits_per_sample_;
    BOOLEAN is_float_;

    PMDL dma_buffer_mdl_ = nullptr;
    uint8_t* dma_buffer_ = nullptr;
    ULONG dma_buffer_size_ = 0;
    ULONG dma_frame_pos_ = 0;
    ULONG current_byte_pos_ = 0;
    ULONG notify_count_ = 2;
    PKEVENT notify_event_ = nullptr;

    KSSTATE state_ = KSSTATE_STOP;
    PEX_TIMER timer_ = nullptr;
    KSPIN_LOCK timer_lock_;
    LARGE_INTEGER last_qpc_ = {};
    LARGE_INTEGER qpc_freq_ = {};
};

class CAfWaveRtMiniport : public IMiniportWaveRT, public CUnknown {
public:
    CAfWaveRtMiniport(ULONG endpoint_index, BOOLEAN is_capture)
        : CUnknown(nullptr)
        , endpoint_index_(endpoint_index)
        , is_capture_(is_capture) {}

    ~CAfWaveRtMiniport() override {
        if (port_) {
            port_->Release();
            port_ = nullptr;
        }
    }

    STDMETHODIMP NonDelegatingQueryInterface(REFIID iid, PVOID* ppv) override {
        if (IsEqualIID(iid, IID_IUnknown) ||
            IsEqualIID(iid, IID_IMiniport) ||
            IsEqualIID(iid, IID_IMiniportWaveRT)) {
            *ppv = static_cast<IMiniportWaveRT*>(this);
            AddRef();
            return STATUS_SUCCESS;
        }
        *ppv = nullptr;
        return STATUS_INVALID_PARAMETER;
    }

    STDMETHODIMP_(ULONG) AddRef() override { return CUnknown::NonDelegatingAddRef(); }
    STDMETHODIMP_(ULONG) Release() override { return CUnknown::NonDelegatingRelease(); }

    STDMETHODIMP GetDescription(_Out_ PPCFILTER_DESCRIPTOR* Description) override {
        *Description = is_capture_ ? &g_wave_capture_filter : &g_wave_render_filter;
        return STATUS_SUCCESS;
    }

    STDMETHODIMP DataRangeIntersection(
        _In_ ULONG PinId,
        _In_ PKSDATARANGE ClientDataRange,
        _In_ PKSDATARANGE MyDataRange,
        _In_ ULONG OutputBufferLength,
        _Out_ PVOID ResultantFormat,
        _Out_ PULONG ResultantFormatLength) override
    {
        UNREFERENCED_PARAMETER(PinId);
        UNREFERENCED_PARAMETER(ClientDataRange);
        UNREFERENCED_PARAMETER(MyDataRange);
        UNREFERENCED_PARAMETER(OutputBufferLength);
        UNREFERENCED_PARAMETER(ResultantFormat);
        UNREFERENCED_PARAMETER(ResultantFormatLength);
        return STATUS_NOT_IMPLEMENTED;
    }

    STDMETHODIMP Init(
        _In_ PUNKNOWN UnknownAdapter,
        _In_ PRESOURCELIST ResourceList,
        _In_ PPORTWAVERT Port) override
    {
        UNREFERENCED_PARAMETER(UnknownAdapter);
        UNREFERENCED_PARAMETER(ResourceList);
        port_ = Port;
        if (port_) port_->AddRef();
        return STATUS_SUCCESS;
    }

    STDMETHODIMP NewStream(
        _Out_ PMINIPORTWAVERTSTREAM* Stream,
        _In_ PPORTWAVERTSTREAM PortStream,
        _In_ ULONG Pin,
        _In_ BOOLEAN Capture,
        _In_ PKSDATAFORMAT DataFormat) override
    {
        UNREFERENCED_PARAMETER(Pin);
        UNREFERENCED_PARAMETER(Capture);
        ULONG sr = 48000;
        ULONG ch = 2;
        ULONG bits = 32;
        BOOLEAN is_float = TRUE;

        if (DataFormat->FormatSize >= sizeof(KSDATAFORMAT_WAVEFORMATEX)) {
            auto* wf = (PKSDATAFORMAT_WAVEFORMATEX)DataFormat;
            sr = wf->WaveFormatEx.nSamplesPerSec;
            ch = wf->WaveFormatEx.nChannels;
            bits = wf->WaveFormatEx.wBitsPerSample;
            is_float = IsEqualGUID(DataFormat->SubFormat, KSDATAFORMAT_SUBTYPE_IEEE_FLOAT) ||
                       (wf->WaveFormatEx.wFormatTag == WAVE_FORMAT_IEEE_FLOAT);
            if (wf->WaveFormatEx.wFormatTag == WAVE_FORMAT_EXTENSIBLE &&
                DataFormat->FormatSize >= sizeof(KSDATAFORMAT_WAVEFORMATEXTENSIBLE)) {
                auto* wfe = (PKSDATAFORMAT_WAVEFORMATEXTENSIBLE)DataFormat;
                is_float = IsEqualGUID(wfe->WaveFormatExt.SubFormat, KSDATAFORMAT_SUBTYPE_IEEE_FLOAT);
            }
        }

        AF_ENDPOINT_SLOT* slot = is_capture_
            ? &g_af_adapter.capture_slots[endpoint_index_]
            : &g_af_adapter.render_slots[endpoint_index_];

        auto* obj = new (NonPagedPoolNx, AF_POOL_TAG) CAfWaveRtStream(
            slot, is_capture_, PortStream, sr, ch, bits, is_float);
        if (!obj) {
            return STATUS_INSUFFICIENT_RESOURCES;
        }
        *Stream = obj;
        (*Stream)->AddRef();
        return STATUS_SUCCESS;
    }

    STDMETHODIMP GetDeviceDescription(_Out_ PDEVICE_DESCRIPTION DeviceDescription) override {
        RtlZeroMemory(DeviceDescription, sizeof(DEVICE_DESCRIPTION));
        DeviceDescription->Version = DEVICE_DESCRIPTION_VERSION;
        DeviceDescription->Master = TRUE;
        DeviceDescription->ScatterGather = TRUE;
        DeviceDescription->Dma32BitAddresses = TRUE;
        DeviceDescription->Dma64BitAddresses = TRUE;
        DeviceDescription->InterfaceType = Internal;
        DeviceDescription->MaximumLength = 0x10000;
        return STATUS_SUCCESS;
    }

private:
    ULONG endpoint_index_;
    BOOLEAN is_capture_;
    PPORTWAVERT port_ = nullptr;
};

NTSTATUS AfCreateWaveRtMiniport(
    _Out_ PUNKNOWN* Unknown,
    _In_ ULONG EndpointIndex,
    _In_ BOOLEAN IsCapture,
    _In_ POOL_TYPE PoolType)
{
    auto* miniport = new (PoolType, AF_POOL_TAG) CAfWaveRtMiniport(EndpointIndex, IsCapture);
    if (!miniport) {
        return STATUS_INSUFFICIENT_RESOURCES;
    }
    *Unknown = static_cast<IMiniportWaveRT*>(miniport);
    (*Unknown)->AddRef();
    return STATUS_SUCCESS;
}

