#include "driver.h"

static KSDATARANGE g_topo_range = {
    sizeof(KSDATARANGE),
    0,
    0,
    0,
    STATICGUIDOF(KSDATAFORMAT_TYPE_AUDIO),
    STATICGUIDOF(KSDATAFORMAT_SUBTYPE_ANALOG),
    STATICGUIDOF(KSDATAFORMAT_SPECIFIER_NONE)
};

static PKSDATARANGE g_topo_range_ptrs[] = { &g_topo_range };

static PCPIN_DESCRIPTOR g_topo_render_pins[] = {
    {
        0, 0, 0, nullptr,
        {
            0, nullptr, 0, nullptr,
            SIZEOF_ARRAY(g_topo_range_ptrs), g_topo_range_ptrs,
            KSPIN_DATAFLOW_IN,
            KSPIN_COMMUNICATION_NONE,
            &KSCATEGORY_AUDIO,
            nullptr,
            0
        }
    },
    {
        0, 0, 0, nullptr,
        {
            0, nullptr, 0, nullptr,
            SIZEOF_ARRAY(g_topo_range_ptrs), g_topo_range_ptrs,
            KSPIN_DATAFLOW_OUT,
            KSPIN_COMMUNICATION_NONE,
            &KSNODETYPE_SPEAKER,
            nullptr,
            0
        }
    }
};

static PCPIN_DESCRIPTOR g_topo_capture_pins[] = {
    {
        0, 0, 0, nullptr,
        {
            0, nullptr, 0, nullptr,
            SIZEOF_ARRAY(g_topo_range_ptrs), g_topo_range_ptrs,
            KSPIN_DATAFLOW_IN,
            KSPIN_COMMUNICATION_NONE,
            &KSNODETYPE_MICROPHONE,
            nullptr,
            0
        }
    },
    {
        0, 0, 0, nullptr,
        {
            0, nullptr, 0, nullptr,
            SIZEOF_ARRAY(g_topo_range_ptrs), g_topo_range_ptrs,
            KSPIN_DATAFLOW_OUT,
            KSPIN_COMMUNICATION_NONE,
            &KSCATEGORY_AUDIO,
            nullptr,
            0
        }
    }
};

static PCCONNECTION_DESCRIPTOR g_topo_conns[] = {
    { PCFILTER_NODE, 0, PCFILTER_NODE, 1 }
};

static PCFILTER_DESCRIPTOR g_topo_render_filter = {
    0, nullptr,
    sizeof(PCPIN_DESCRIPTOR), SIZEOF_ARRAY(g_topo_render_pins), g_topo_render_pins,
    0, 0, nullptr,
    SIZEOF_ARRAY(g_topo_conns), g_topo_conns,
    0, nullptr
};

static PCFILTER_DESCRIPTOR g_topo_capture_filter = {
    0, nullptr,
    sizeof(PCPIN_DESCRIPTOR), SIZEOF_ARRAY(g_topo_capture_pins), g_topo_capture_pins,
    0, 0, nullptr,
    SIZEOF_ARRAY(g_topo_conns), g_topo_conns,
    0, nullptr
};

class CAfTopologyMiniport : public IMiniportTopology, public CUnknown {
public:
    CAfTopologyMiniport(ULONG endpoint_index, BOOLEAN is_capture)
        : CUnknown(nullptr)
        , endpoint_index_(endpoint_index)
        , is_capture_(is_capture) {}

    ~CAfTopologyMiniport() override {
        if (port_) {
            port_->Release();
            port_ = nullptr;
        }
    }

    STDMETHODIMP NonDelegatingQueryInterface(REFIID iid, PVOID* ppv) override {
        if (IsEqualIID(iid, IID_IUnknown) ||
            IsEqualIID(iid, IID_IMiniport) ||
            IsEqualIID(iid, IID_IMiniportTopology)) {
            *ppv = static_cast<IMiniportTopology*>(this);
            AddRef();
            return STATUS_SUCCESS;
        }
        *ppv = nullptr;
        return STATUS_INVALID_PARAMETER;
    }

    STDMETHODIMP_(ULONG) AddRef() override { return CUnknown::NonDelegatingAddRef(); }
    STDMETHODIMP_(ULONG) Release() override { return CUnknown::NonDelegatingRelease(); }

    STDMETHODIMP GetDescription(_Out_ PPCFILTER_DESCRIPTOR* Description) override {
        *Description = is_capture_ ? &g_topo_capture_filter : &g_topo_render_filter;
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
        _In_ PPORTTOPOLOGY Port) override
    {
        UNREFERENCED_PARAMETER(UnknownAdapter);
        UNREFERENCED_PARAMETER(ResourceList);
        port_ = Port;
        if (port_) port_->AddRef();
        return STATUS_SUCCESS;
    }

private:
    ULONG endpoint_index_;
    BOOLEAN is_capture_;
    PPORTTOPOLOGY port_ = nullptr;
};

NTSTATUS AfCreateTopologyMiniport(
    _Out_ PUNKNOWN* Unknown,
    _In_ ULONG EndpointIndex,
    _In_ BOOLEAN IsCapture,
    _In_ POOL_TYPE PoolType)
{
    auto* miniport = new (PoolType, AF_POOL_TAG) CAfTopologyMiniport(EndpointIndex, IsCapture);
    if (!miniport) {
        return STATUS_INSUFFICIENT_RESOURCES;
    }
    *Unknown = static_cast<IMiniportTopology*>(miniport);
    (*Unknown)->AddRef();
    return STATUS_SUCCESS;
}

