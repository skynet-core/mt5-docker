// SPDX-License-Identifier: GPL-2.0-or-later
#ifndef PLASMA_FRAMEBUFFER_GEOMETRY_H
#define PLASMA_FRAMEBUFFER_GEOMETRY_H

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

#endif
