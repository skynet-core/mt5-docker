#!/usr/bin/env bash
# Sourced by build/kwin-6.8.2 inside its matching cached build stage.
build_kde extra-cmake-modules
build_cmake polkit-qt-1 -DQT_MAJOR_VERSION=6
build_kde plasma-wayland-protocols
for project in kcoreaddons kconfig karchive ki18n kdbusaddons kwidgetsaddons \
	kwindowsystem kauth kguiaddons kcolorscheme kidletime kglobalaccel \
	kcrash kpackage kservice kirigami ksvg kholidays; do
	build_kde "$project"
done
