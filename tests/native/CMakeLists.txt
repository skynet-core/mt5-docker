cmake_minimum_required(VERSION 3.22)
project(plasma_native_tests LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_AUTOMOC ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
add_compile_options(-UNDEBUG)
enable_testing()
add_compile_options(--coverage -O0 -g -fprofile-abs-path -fprofile-update=atomic)
add_link_options(--coverage)
get_filename_component(REPOSITORY "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
set(NATIVE "${REPOSITORY}/vendor/kde-container/native")
set(COMPAT "${REPOSITORY}/vendor/kde-container/compat")
find_package(Qt6 REQUIRED COMPONENTS Core Gui Widgets DBus Test)
find_package(PkgConfig REQUIRED)
pkg_check_modules(WAYLAND REQUIRED IMPORTED_TARGET wayland-client)
pkg_check_modules(XKB REQUIRED IMPORTED_TARGET xkbcommon)
find_program(WAYLAND_SCANNER wayland-scanner REQUIRED)
foreach(protocol fake-input ext-data-control-v1 wlr-data-control-unstable-v1)
    foreach(side client server)
        add_custom_command(OUTPUT ${protocol}-${side}.h
            COMMAND ${WAYLAND_SCANNER} ${side}-header "${NATIVE}/${protocol}.xml" ${protocol}-${side}.h
            DEPENDS "${NATIVE}/${protocol}.xml")
    endforeach()
    add_custom_command(OUTPUT ${protocol}-protocol.c
        COMMAND ${WAYLAND_SCANNER} private-code "${NATIVE}/${protocol}.xml" ${protocol}-protocol.c
        DEPENDS "${NATIVE}/${protocol}.xml")
    list(APPEND PROTOCOLS ${protocol}-protocol.c ${protocol}-client.h ${protocol}-server.h)
endforeach()
add_library(protocols STATIC ${PROTOCOLS})
target_link_libraries(protocols PUBLIC PkgConfig::WAYLAND)
target_include_directories(protocols PUBLIC "${CMAKE_CURRENT_BINARY_DIR}")

add_executable(clipboard-unit clipboard-unit.cpp)
target_link_libraries(clipboard-unit PRIVATE protocols Qt6::Gui)
add_test(NAME clipboard-core COMMAND clipboard-unit core)
add_test(NAME clipboard-transfer COMMAND clipboard-unit transfer)
add_test(NAME clipboard-transfer-timeout COMMAND clipboard-unit timeout)

function(expect_failure name target scenario status pattern)
    add_test(NAME ${name} COMMAND "${CMAKE_COMMAND}"
        "-DPROGRAM=$<TARGET_FILE:${target}>" "-DCASE=${scenario}"
        "-DEXPECT=${status}" "-DMATCH=${pattern}"
        -P "${CMAKE_CURRENT_SOURCE_DIR}/expect.cmake")
endfunction()
foreach(scenario invalid-json invalid-mode invalid-data invalid-base64 wire-limit data-limit stdout-failed)
    expect_failure(clipboard-${scenario} clipboard-unit ${scenario} 1 "Plasma clipboard:")
endforeach()
expect_failure(clipboard-unknown-case clipboard-unit unknown-case 2 "")

add_library(egl-fence-wait SHARED "${COMPAT}/egl-fence-wait.cpp")
target_link_libraries(egl-fence-wait PRIVATE dl)
add_library(fake-egl SHARED "${REPOSITORY}/vendor/kde-container/test/egl-fence-stub.cpp")
set_target_properties(fake-egl PROPERTIES OUTPUT_NAME EGL SOVERSION 1 LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/fake-egl")
target_link_libraries(fake-egl PRIVATE pthread)
add_executable(egl-fence-test "${REPOSITORY}/vendor/kde-container/test/egl-fence-test.cpp")
target_link_libraries(egl-fence-test PRIVATE dl)
foreach(vendor NVIDIA Mesa)
    add_test(NAME egl-fence-${vendor} COMMAND "${CMAKE_COMMAND}" -E env
        "LD_LIBRARY_PATH=${CMAKE_CURRENT_BINARY_DIR}/fake-egl" "FAKE_EGL_VENDOR=${vendor}"
        $<TARGET_FILE:egl-fence-test> $<TARGET_FILE:egl-fence-wait>)
endforeach()

add_library(xtest-paced SHARED "${COMPAT}/xtest-paced.cpp")
target_link_libraries(xtest-paced PRIVATE dl)
add_library(fake-xtest SHARED fake-xtest.cpp)
add_executable(compat-unit compat-unit.cpp)
target_link_libraries(compat-unit PRIVATE dl)
target_link_options(compat-unit PRIVATE -Wl,--export-dynamic)
foreach(scenario original-empty original-absent fence-eintr)
    add_test(NAME compat-${scenario} COMMAND "${CMAKE_COMMAND}" -E env
        "LD_LIBRARY_PATH=${CMAKE_CURRENT_BINARY_DIR}/fake-egl"
        "FAKE_EGL_VENDOR=NVIDIA" "NATIVE_CASE=${scenario}"
        $<TARGET_FILE:compat-unit> $<TARGET_FILE:egl-fence-wait>)
endforeach()
foreach(scenario egl-open get-proc fence-api fence-error restore-failed)
    add_test(NAME compat-${scenario} COMMAND "${CMAKE_COMMAND}"
        "-DPROGRAM=$<TARGET_FILE:compat-unit>"
        "-DCASE=$<TARGET_FILE:egl-fence-wait>"
        "-DENVIRONMENT=LD_LIBRARY_PATH=${CMAKE_CURRENT_BINARY_DIR}/fake-egl;FAKE_EGL_VENDOR=NVIDIA;NATIVE_CASE=${scenario}"
        "-DEXPECT=99" "-DMATCH=Plasma"
        -P "${CMAKE_CURRENT_SOURCE_DIR}/expect.cmake")
endforeach()
add_executable(xtest-unit xtest-unit.cpp)
target_link_libraries(xtest-unit PRIVATE dl)
target_link_options(xtest-unit PRIVATE -Wl,--export-dynamic)
add_test(NAME xtest-pacing COMMAND "${CMAKE_COMMAND}" -E env
    "LD_PRELOAD=$<TARGET_FILE:xtest-paced>:$<TARGET_FILE:fake-xtest>"
    $<TARGET_FILE:xtest-unit> $<TARGET_FILE:xtest-paced> $<TARGET_FILE:fake-xtest>)
add_test(NAME xtest-missing COMMAND "${CMAKE_COMMAND}"
    "-DPROGRAM=$<TARGET_FILE:xtest-unit>"
    "-DCASE=$<TARGET_FILE:xtest-paced>;$<TARGET_FILE:fake-xtest>"
    "-DENVIRONMENT=NATIVE_CASE=xtest-missing" "-DEXPECT=99" "-DMATCH=cannot resolve XTest"
    -P "${CMAKE_CURRENT_SOURCE_DIR}/expect.cmake")

add_executable(fixture-unit fixture-unit.cpp)
target_link_libraries(fixture-unit PRIVATE Qt6::Widgets Qt6::DBus Qt6::Test dl)
foreach(scenario text text-save-failed images publish-image image-save-failed invalid-args capture-ok capture-invalid-size capture-error capture-save-failed capture-no-screen capture-temp-failed capture-incomplete capture-x11)
    add_test(NAME fixture-${scenario} COMMAND fixture-unit ${scenario})
endforeach()
add_executable(benchmark-unit benchmark-unit.cpp)
target_link_libraries(benchmark-unit PRIVATE Qt6::Widgets Qt6::Test)
add_test(NAME benchmark-ui COMMAND benchmark-unit)

set_tests_properties(fixture-capture-x11 PROPERTIES SKIP_RETURN_CODE 77)
add_executable(clipboard-protocol-unit clipboard-protocol-unit.cpp fake-wayland.cpp)
target_link_libraries(clipboard-protocol-unit PRIVATE protocols Qt6::Gui PkgConfig::XKB)
foreach(scenario ext wlr-v1 wlr-v2 prefer-ext-first oversize-offer endpoint-wayland endpoint-x11 read-eintr read-eagain bridge bridge-fallback bridge-worker-zero)
    add_test(NAME clipboard-protocol-${scenario} COMMAND clipboard-protocol-unit ${scenario})
endforeach()
foreach(scenario connect-failed enumerate-failed device-failed finished dispatch-failed unknown-offer pipe-failed stdin-failed read-failed bridge-invalid-message bridge-start-failed bridge-write-failed)
    expect_failure(clipboard-protocol-${scenario} clipboard-protocol-unit ${scenario} 1 "Plasma clipboard:")
endforeach()
foreach(scenario protocol-unavailable seat-unavailable)
    expect_failure(clipboard-protocol-${scenario} clipboard-protocol-unit ${scenario} 3 "host does not expose")
endforeach()

set(KRFB_SOURCE_DIR "" CACHE PATH "Pinned KRFB source tree supplying the real event interfaces")
if(NOT EXISTS "${KRFB_SOURCE_DIR}/krfb/events.cpp")
    message(FATAL_ERROR "Set KRFB_SOURCE_DIR to the pinned KRFB tree used by the image input-source stage")
endif()
if(KRFB_SOURCE_DIR)
    find_package(KF6CoreAddons REQUIRED)
    find_path(RFB_INCLUDE_DIR rfb.h PATH_SUFFIXES rfb REQUIRED)
    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/krfbprivate_export.h" "#pragma once\n#define KRFBPRIVATE_EXPORT\n")
    add_library(krfb-test-api STATIC
        "${KRFB_SOURCE_DIR}/krfb/events.cpp" "${KRFB_SOURCE_DIR}/krfb/eventsplugin.cpp")
    target_include_directories(krfb-test-api PUBLIC "${KRFB_SOURCE_DIR}/krfb" "${RFB_INCLUDE_DIR}" "${CMAKE_CURRENT_BINARY_DIR}")
    target_link_libraries(krfb-test-api PUBLIC Qt6::Widgets)
    set_source_files_properties("${NATIVE}/kwin-input.cpp" PROPERTIES HEADER_FILE_ONLY TRUE)
    add_executable(input-unit input-unit.cpp fake-wayland.cpp "${NATIVE}/kwin-input.cpp")
    target_link_libraries(input-unit PRIVATE protocols krfb-test-api KF6::CoreAddons PkgConfig::XKB)
    foreach(scenario normal-v5 normal-v6)
        add_test(NAME input-${scenario} COMMAND input-unit ${scenario})
    endforeach()
    foreach(scenario connect-failed enumerate-failed input-unavailable auth-failed keymap-unavailable dispatch-failed)
        expect_failure(input-${scenario} input-unit ${scenario} 99 "KRFB KWin input:")
    endforeach()
endif()
get_property(native_tests DIRECTORY PROPERTY TESTS)
set_tests_properties(${native_tests} PROPERTIES TIMEOUT 30)
