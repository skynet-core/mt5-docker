cmake_minimum_required(VERSION 3.22)
project(plasma_container_input LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
set(CMAKE_AUTOMOC ON)
find_package(Qt6 REQUIRED COMPONENTS Core Widgets DBus)
find_package(KF6CoreAddons REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(WAYLAND REQUIRED IMPORTED_TARGET wayland-client)
pkg_check_modules(XKB REQUIRED IMPORTED_TARGET xkbcommon)
find_program(WAYLAND_SCANNER wayland-scanner REQUIRED)
find_library(KRFB_PRIVATE NAMES krfbprivate libkrfbprivate.so.5 libkrfbprivate.so.5.0 REQUIRED)
find_path(RFB_INCLUDE_DIR rfb.h PATH_SUFFIXES rfb REQUIRED)

file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/krfbprivate_export.h"
    "#pragma once\n#include <QtCore/qglobal.h>\n#define KRFBPRIVATE_EXPORT Q_DECL_IMPORT\n")
foreach(protocol fake-input ext-data-control-v1 wlr-data-control-unstable-v1)
    add_custom_command(OUTPUT ${protocol}-client.h ${protocol}-protocol.c
        COMMAND ${WAYLAND_SCANNER} client-header "${CMAKE_CURRENT_SOURCE_DIR}/${protocol}.xml" ${protocol}-client.h
        COMMAND ${WAYLAND_SCANNER} private-code "${CMAKE_CURRENT_SOURCE_DIR}/${protocol}.xml" ${protocol}-protocol.c
        DEPENDS ${protocol}.xml)
endforeach()
add_library(kwin MODULE kwin-input.cpp fake-input-protocol.c fake-input-client.h)
set_target_properties(kwin PROPERTIES PREFIX "")
target_include_directories(kwin PRIVATE "${KRFB_SOURCE_DIR}/krfb" "${RFB_INCLUDE_DIR}" "${CMAKE_CURRENT_BINARY_DIR}")
target_link_libraries(kwin PRIVATE Qt6::Widgets KF6::CoreAddons PkgConfig::WAYLAND PkgConfig::XKB "${KRFB_PRIVATE}")

add_executable(plasma-ui-fixture ui-fixture.cpp)
target_link_libraries(plasma-ui-fixture PRIVATE Qt6::Widgets Qt6::DBus)

add_executable(plasma-vnc-benchmark vnc-benchmark.cpp)
target_link_libraries(plasma-vnc-benchmark PRIVATE Qt6::Widgets)

add_executable(plasma-clipboard clipboard.cpp
    ext-data-control-v1-protocol.c ext-data-control-v1-client.h
    wlr-data-control-unstable-v1-protocol.c wlr-data-control-unstable-v1-client.h)
target_include_directories(plasma-clipboard PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
target_link_libraries(plasma-clipboard PRIVATE Qt6::Gui PkgConfig::WAYLAND)
