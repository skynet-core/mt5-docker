// SPDX-License-Identifier: GPL-2.0-or-later
// Test-only editor: the output file is written exclusively by a Save UI action.
#include <QApplication>
#include <QClipboard>
#include <QDBusConnection>
#include <QDBusMessage>
#include <QDBusReply>
#include <QDBusUnixFileDescriptor>
#include <QDebug>
#include <QElapsedTimer>
#include <QFile>
#include <QFileDevice>
#include <QFileInfo>
#include <QImage>
#include <QJsonDocument>
#include <QJsonObject>
#include <QKeySequence>
#include <QLabel>
#include <QMimeData>
#include <QObject>
#include <QPainter>
#include <QPixmap>
#include <QPushButton>
#include <QScreen>
#include <QShortcut>
#include <QString>
#include <QTemporaryFile>
#include <QTextEdit>
#include <QThread>
#include <QVBoxLayout>
#include <QWidget>
#include <cstdio>
#include <functional>
#include <qnamespace.h>

int captureOutput(const QString &destination, bool includeCursor = false) {
  auto *screen = QGuiApplication::primaryScreen();
  if (!screen) {
    std::fprintf(stderr, "No compositor output is available\n");
    return 1;
  }
  QImage image;
  QString method;
  if (QGuiApplication::platformName() == "xcb") {
    if (includeCursor) {
      std::fprintf(stderr,
                   "Cursor capture requires the Wayland screenshot API\n");
      return 1;
    }
    image = screen->grabWindow(0).toImage();
    method = "x11-root";
  } else {
    QTemporaryFile pixels;
    if (!pixels.open()) {
      std::fprintf(stderr, "Cannot open screenshot buffer: %s\n",
                   qPrintable(pixels.errorString()));
      return 1;
    }
    const QRect area = screen->geometry();
    auto call = QDBusMessage::createMethodCall(
        "org.kde.KWin.ScreenShot2", "/org/kde/KWin/ScreenShot2",
        "org.kde.KWin.ScreenShot2", "CaptureArea");
    call.setArguments(
        {area.x(), area.y(), uint(area.width()), uint(area.height()),
         QVariantMap{{"include-cursor", includeCursor},
                     {"hide-caller-windows", false}},
         QVariant::fromValue(QDBusUnixFileDescriptor(pixels.handle()))});
    QDBusReply<QVariantMap> reply =
        QDBusConnection::sessionBus().call(call, QDBus::Block, 10000);
    if (!reply.isValid()) {
      std::fprintf(stderr, "KWin output capture failed: %s: %s\n",
                   qPrintable(reply.error().name()),
                   qPrintable(reply.error().message()));
      return 1;
    }
    const auto attributes = reply.value();
    const int width = attributes.value("width").toInt();
    const int height = attributes.value("height").toInt();
    const int stride = attributes.value("stride").toInt();
    const auto format =
        static_cast<QImage::Format>(attributes.value("format").toInt());
    const qint64 size = qint64(stride) * height;
    if (width <= 0 || height <= 0 || stride <= 0 || size > 128 * 1024 * 1024) {
      std::fprintf(stderr, "KWin returned invalid screenshot dimensions\n");
      return 1;
    }
    // KWin sends metadata before its worker finishes writing the image FD.
    QElapsedTimer timeout;
    timeout.start();
    while (QFileInfo(pixels.fileName()).size() < size &&
           timeout.elapsed() < 10000)
      QThread::msleep(10);
    if (QFileInfo(pixels.fileName()).size() != size || !pixels.seek(0)) {
      std::fprintf(stderr, "KWin did not finish the screenshot buffer\n");
      return 1;
    }
    const QByteArray bytes = pixels.readAll();
    image = QImage(reinterpret_cast<const uchar *>(bytes.constData()), width,
                   height, stride, format)
                .copy();
    method = "kwin-screenshot2";
  }
  if (image.isNull() || !image.save(destination, "PNG")) {
    std::fprintf(stderr, "Cannot save compositor screenshot: %s\n",
                 qPrintable(destination));
    return 1;
  }
  QJsonObject attributes{
      {"method", method}, {"width", image.width()}, {"height", image.height()}};
  if (includeCursor)
    attributes.insert("include_cursor", true);
  const auto metadata =
      QJsonDocument(attributes).toJson(QJsonDocument::Compact);
  // Machine-readable results must survive QT_LOGGING_RULES disabling qInfo.
  std::printf("%s\n", metadata.constData());
  std::fflush(stdout);
  return 0;
}

int main(int argc, char **argv) {
  QApplication app(argc, argv);
  QGuiApplication::setDesktopFileName("plasma-ui-fixture");
  if (argc == 3) {
    const QString option = QString::fromLocal8Bit(argv[1]);
    if (option == "--capture-output" ||
        option == "--capture-output-with-cursor")
      return captureOutput(QString::fromLocal8Bit(argv[2]),
                           option == "--capture-output-with-cursor");
  }
  const bool publishImage =
      argc == 3 && QString::fromLocal8Bit(argv[2]) == "--publish-image";
  const bool images =
      publishImage ||
      (argc == 3 && QString::fromLocal8Bit(argv[2]) == "--images");
  if (argc != 2 && !images)
    return 2;
  const QString output = QString::fromLocal8Bit(argv[1]);
  QWidget window;
  window.setWindowTitle(images ? "Plasma clipboard image regression"
                               : "Plasma VNC input regression");
  window.resize(560, 380);
  if (publishImage)
    window.setWindowFlag(Qt::WindowStaysOnTopHint);
  auto layout = new QVBoxLayout(&window);
  layout->setContentsMargins(12, 12, 12, 12);
  auto banner = new QLabel(images ? "Clipboard image paste test"
                                  : "VNC mouse drag and keyboard test");
  banner->setFixedHeight(44);
  banner->setAlignment(Qt::AlignCenter);
  banner->setStyleSheet(
      publishImage
          ? "background: rgb(215,120,30); color: black; font-size: 18px;"
          : "background: rgb(18,210,178); color: black; font-size: 18px;");
  layout->addWidget(banner);
  if (images) {
    QImage image(3840, 2160, QImage::Format_ARGB32);
    image.fill(Qt::transparent);
    {
      QPainter painter(&image);
      painter.fillRect(0, 0, 1920, 1080,
                       publishImage ? QColor(210, 100, 180)
                                    : QColor(220, 40, 70));
      painter.fillRect(1920, 0, 1920, 1080, QColor(20, 180, 90));
      painter.fillRect(0, 1080, 1920, 1080, QColor(40, 90, 220));
      painter.fillRect(1920, 1080, 1920, 1080, QColor(0, 0, 0, 128));
    }
    QImage pasted;
    auto preview = new QLabel("Paste an image with Ctrl+V");
    preview->setAlignment(Qt::AlignCenter);
    preview->setMinimumHeight(180);
    layout->addWidget(preview, 1);
    auto status = new QLabel("No image pasted");
    layout->addWidget(status);
    auto action = [&](const QString &label, const QKeySequence &key,
                      const std::function<void()> &callback) {
      auto button = new QPushButton(label);
      layout->addWidget(button);
      QObject::connect(button, &QPushButton::clicked, &window, callback);
      auto shortcut = new QShortcut(key, &window);
      QObject::connect(shortcut, &QShortcut::activated, &window, callback);
    };
    action("Copy 4K Qt image (Ctrl+C)", QKeySequence::Copy, [&] {
      auto mime = new QMimeData;
      mime->setImageData(image);
      mime->setData("application/x-kde-suggestedfilename",
                    "Dolphin screenshot.png");
      // Clipboard-manager restores can include the former bridge marker.
      mime->setData("application/x-plasma6-clipboard-owner", "retired-owner");
      app.clipboard()->setMimeData(mime);
      // Keep the owner alive after the test clicks Copy and returns to KWin.
      if (publishImage)
        window.hide();
    });
    action("Paste image (Ctrl+V)", QKeySequence::Paste, [&] {
      pasted = app.clipboard()->image();
      if (!pasted.isNull())
        preview->setPixmap(QPixmap::fromImage(pasted).scaled(
            480, 220, Qt::KeepAspectRatio, Qt::FastTransformation));
      status->setText(pasted.isNull() ? "No image on clipboard"
                                      : "Image pasted");
    });
    action("Save pasted PNG to host file (Ctrl+S)", QKeySequence::Save, [&] {
      status->setText(pasted.save(output, "PNG") ? "Image saved to host file"
                                                 : "Image save failed");
    });
    window.show();
    return app.exec();
  }
  auto editor = new QTextEdit;
  editor->setAcceptRichText(false);
  editor->setStyleSheet("background: white; color: black; font-size: 18px;");
  layout->addWidget(editor);
  auto save = new QPushButton("Save to host file (Ctrl+S)");
  save->setFixedHeight(36);
  save->setStyleSheet(
      "background: rgb(36,88,180); color: white; border: 1px solid black;");
  layout->addWidget(save);
  auto status = new QLabel("Unsaved");
  layout->addWidget(status);
  auto saveAction = [&] {
    QFile file(output);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
      status->setText("Save failed: " + file.errorString());
      return;
    }
    file.write(editor->toPlainText().toUtf8());
    file.close();
    status->setText("Saved to host file");
  };
  QObject::connect(save, &QPushButton::clicked, &window, saveAction);
  auto shortcut = new QShortcut(QKeySequence::Save, &window);
  QObject::connect(shortcut, &QShortcut::activated, &window, saveAction);
  window.show();
  editor->setFocus();
  return app.exec();
}
