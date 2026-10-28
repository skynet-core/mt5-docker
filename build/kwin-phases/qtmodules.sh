#!/usr/bin/env bash
# Sourced by build/kwin-6.8.2 inside its matching cached build stage.
build_qt qttools -DFEATURE_assistant=OFF -DFEATURE_designer=OFF \
	-DFEATURE_qdoc=OFF -DFEATURE_distancefieldgenerator=OFF \
	-DFEATURE_pixeltool=OFF -DFEATURE_qtdiag=OFF
build_qt qt5compat
build_qt qtpositioning
