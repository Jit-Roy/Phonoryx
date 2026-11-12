cmake_minimum_required(VERSION 3.24)
project(AudioFlowEngine LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(MSVC)
    add_compile_options(/W4 /wd4324 /permissive- /EHsc /utf-8)
    add_compile_definitions(_CRT_SECURE_NO_WARNINGS NOMINMAX WIN32_LEAN_AND_MEAN UNICODE _UNICODE)
endif()

set(ENGINE_SOURCES
    src/device_manager.cpp
    src/process_manager.cpp
    src/dsp_nodes.cpp
    src/wasapi_endpoint.cpp
    src/virtual_driver_bridge.cpp
    src/audio_graph.cpp
    src/engine_capi.cpp
)

add_library(audioflow_engine SHARED ${ENGINE_SOURCES})
target_include_directories(audioflow_engine
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/src
        ${CMAKE_CURRENT_SOURCE_DIR}/../driver/include
)
target_compile_definitions(audioflow_engine PRIVATE AUDIOFLOW_EXPORTS)
target_link_libraries(audioflow_engine PRIVATE
    ole32
    oleaut32
    avrt
    mmdevapi
    ksuser
    setupapi
    cfgmgr32
    psapi
    winmm
)

# Output DLL into project root build/bin and python package runtime folder for easy loading
set_target_properties(audioflow_engine PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
    ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib"
)

add_executable(audioflow_tests
    tests/test_main.cpp
    ${ENGINE_SOURCES}
)
target_include_directories(audioflow_tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    ${CMAKE_CURRENT_SOURCE_DIR}/../driver/include
)
target_compile_definitions(audioflow_tests PRIVATE AUDIOFLOW_STATIC_TEST)
target_link_libraries(audioflow_tests PRIVATE
    ole32
    oleaut32
    avrt
    mmdevapi
    ksuser
    setupapi
    cfgmgr32
    psapi
    winmm
)
set_target_properties(audioflow_tests PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)

enable_testing()
add_test(NAME AudioFlowEngineTests COMMAND audioflow_tests)
