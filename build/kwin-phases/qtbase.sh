#!/usr/bin/env bash
# Sourced by build/kwin-6.8.2 inside its matching cached build stage.
: "${prefix:?Sourced through build/kwin-6.8.2}"
# Bundled JPEG and no ICU avoid Debian-only SONAMEs in the Ubuntu overlay.
build_qt qtbase -DFEATURE_icu=OFF -DINPUT_libjpeg=qt -DFEATURE_sql=OFF
"$prefix/bin/qmake" -query QT_VERSION | grep -Fx 6.11.2
