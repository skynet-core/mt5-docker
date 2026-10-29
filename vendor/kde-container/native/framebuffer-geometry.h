// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <QCoreApplication>
#include <QSize>
#include <QVariant>

namespace PlasmaFramebuffer {
inline void publish(const QSize &size) {
  QCoreApplication::instance()->setProperty("plasmaFramebufferSize", size);
}
inline QVariant current() {
  return QCoreApplication::instance()->property("plasmaFramebufferSize");
}
} // namespace PlasmaFramebuffer
