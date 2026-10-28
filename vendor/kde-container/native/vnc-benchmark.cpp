// SPDX-License-Identifier: GPL-2.0-or-later
// Test-only animation and keyboard acknowledgement, observed through noVNC.
#include <QApplication>
#include <QColor>
#include <QKeyEvent>
#include <QLinearGradient>
#include <QPainter>
#include <QTimer>
#include <QWidget>
#include <qnamespace.h>

class BenchmarkWindow final : public QWidget {
public:
  BenchmarkWindow() {
    setWindowTitle("Plasma VNC performance");
    resize(800, 500);
    setFocusPolicy(Qt::StrongFocus);
    timer.setTimerType(Qt::PreciseTimer);
    connect(&timer, &QTimer::timeout, this, [this] {
      ++frame;
      update(QRect(32, 90, 80, 80));
    });
    timer.start(16);
  }

protected:
  static QColor marker(unsigned value) {
    return QColor(48 + 64 * static_cast<int>(value & 3u),
                  48 + 64 * static_cast<int>((value >> 2u) & 3u),
                  48 + 64 * static_cast<int>((value >> 4u) & 3u));
  }

  void paintEvent(QPaintEvent *) override {
    QPainter painter(this);
    painter.fillRect(rect(), Qt::white);
    painter.fillRect(QRect(12, 12, width() - 24, 44), QColor(18, 210, 178));
    painter.setPen(Qt::black);
    painter.drawText(QRect(12, 12, width() - 24, 44), Qt::AlignCenter,
                     "noVNC frame delivery and input latency");
    painter.fillRect(QRect(32, 90, 80, 80), marker(frame));
    painter.fillRect(QRect(160, 90, 80, 80), marker(keys));
    painter.drawText(
        32, 195, "Animation (60 Hz source)       Keyboard acknowledgement (A)");
    QLinearGradient gradient(0, 220, width(), height());
    gradient.setColorAt(0, QColor(30, 80, 170));
    gradient.setColorAt(0.5, QColor(210, 100, 40));
    gradient.setColorAt(1, QColor(50, 170, 100));
    painter.fillRect(QRect(12, 220, width() - 24, height() - 232), gradient);
  }

  void keyPressEvent(QKeyEvent *event) override {
    if (event->key() == Qt::Key_A && !event->isAutoRepeat()) {
      ++keys;
      update(QRect(160, 90, 80, 80));
    } else if (event->key() == Qt::Key_P) {
      timer.stop();
    }
  }

private:
  QTimer timer;
  unsigned frame = 0;
  unsigned keys = 0;
};

int main(int argc, char **argv) {
  QApplication app(argc, argv);
  BenchmarkWindow window;
  window.show();
  window.setFocus();
  return app.exec();
}
