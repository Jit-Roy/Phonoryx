cmake_minimum_required(VERSION 3.24)
project(PhonoryxEngine LANGUAGES CXX)

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
    src/audio_graph.cpp
    src/engine_capi.cpp
)

add_library(phonoryx_engine SHARED ${ENGINE_SOURCES})
target_include_directories(phonoryx_engine
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/src
)
target_compile_definitions(phonoryx_engine PRIVATE PHONORYX_EXPORTS)
target_link_libraries(phonoryx_engine PRIVATE
    ole32
    oleaut32
    avrt
    mmdevapi
    ksuser
    psapi
    winmm
)

# Output DLL into project root build/bin and python package runtime folder for easy loading
set_target_properties(phonoryx_engine PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
    ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib"
)

add_executable(phonoryx_tests
    tests/test_main.cpp
    ${ENGINE_SOURCES}
)
target_include_directories(phonoryx_tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)
target_compile_definitions(phonoryx_tests PRIVATE PHONORYX_STATIC_TEST)
target_link_libraries(phonoryx_tests PRIVATE
    ole32
    oleaut32
    avrt
    mmdevapi
    ksuser
    psapi
    winmm
)
set_target_properties(phonoryx_tests PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)

enable_testing()
add_test(NAME PhonoryxEngineTests COMMAND phonoryx_tests)
