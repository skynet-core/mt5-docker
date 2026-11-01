// SPDX-License-Identifier: GPL-2.0-or-later
// Exercise the real PWFrameBuffer frameReceived connection, without a server.
#undef NDEBUG
#include <cassert>
#include <cstdio>
#include <cstring>
#include <memory>
#include <vector>
#include <QGuiApplication>
#include <QImage>
#include <QTemporaryDir>
#include <rfb/rfb.h>
#include <pipewiresourcestream.h>
#include "pw_framebuffer.h"
#if !KPIPEWIRE60
// Exact upstream declaration; CMake checks each supported private ABI.
#include "pipewire-frame-cleanup.h"
#endif

struct KrfbFrameFormat {
    const char *name = nullptr;
    QImage::Format image;
#if !KPIPEWIRE60
    spa_video_format spa;
    bool swap = false;
#endif
};

#if KPIPEWIRE60
static const KrfbFrameFormat formats[] = {
    {"RGBA8888", QImage::Format_RGBA8888},
    {"RGBX8888", QImage::Format_RGBX8888},
    {"ARGB32", QImage::Format_ARGB32},
    {"RGB32", QImage::Format_RGB32},
    {"RGB888", QImage::Format_RGB888},
    {"BGR888", QImage::Format_BGR888},
    {"Grayscale8", QImage::Format_Grayscale8},
    {"ARGB32-premultiplied", QImage::Format_ARGB32_Premultiplied},
    {"RGBA8888-premultiplied", QImage::Format_RGBA8888_Premultiplied},
};
#else
static const KrfbFrameFormat formats[] = {
    {"RGBA", QImage::Format_RGBA8888_Premultiplied, SPA_VIDEO_FORMAT_RGBA, false},
    {"RGBx", QImage::Format_RGBX8888, SPA_VIDEO_FORMAT_RGBx, false},
    {"BGRx", QImage::Format_RGBA8888_Premultiplied, SPA_VIDEO_FORMAT_BGRx, true},
    {"BGRA", QImage::Format_RGBA8888_Premultiplied, SPA_VIDEO_FORMAT_BGRA, true},
    {"ABGR", QImage::Format_ARGB32, SPA_VIDEO_FORMAT_ABGR, true},
    {"xBGR", QImage::Format_ARGB32, SPA_VIDEO_FORMAT_xBGR, true},
    {"RGB", QImage::Format_RGB888, SPA_VIDEO_FORMAT_RGB, false},
    {"BGR", QImage::Format_BGR888, SPA_VIDEO_FORMAT_BGR, false},
    {"GRAY8", QImage::Format_Grayscale8, SPA_VIDEO_FORMAT_GRAY8, false},
};
#endif

static void deliver(PipeWireSourceStream *stream, const QImage &image,
                    const KrfbFrameFormat &format)
{
    PipeWireFrame frame{};
#if KPIPEWIRE60
    (void)format;
    frame.image = image;
#else
    // Capture the real QImage so the public data pointer stays alive until the
    // library's actual reference-counted frame cleanup releases it.
    auto *cleanup = new PipeWireFrameCleanupFunction([image] {});
    frame.format = format.spa;
    frame.dataFrame = std::make_shared<PipeWireFrameData>(
        format.spa, const_cast<uchar *>(image.constBits()), image.size(),
        image.bytesPerLine(), cleanup);
#endif
    Q_EMIT stream->frameReceived(frame);
}

static rfbPixelFormat wireFormat{};

static void verify(PWFrameBuffer &fb, const QImage &expected, bool expectDamage = true)
{
    assert(fb.width() == expected.width());
    assert(fb.height() == expected.height());
    assert(fb.depth() == 32);
    assert(fb.paddedWidth() == expected.width() * 4);
    assert(fb.data());
    for (int y = 0; y < expected.height(); ++y) {
        for (int x = 0; x < expected.width(); ++x) {
            const auto *bytes = reinterpret_cast<const unsigned char *>(
                fb.data() + y * fb.paddedWidth() + x * 4);
            uint32_t value = 0;
            for (int channel = 0; channel < 4; ++channel) {
                value |= uint32_t(bytes[channel]) << (8 * (wireFormat.bigEndian ? 3 - channel : channel));
            }
            const QRgb actual = qRgb(
                ((value >> wireFormat.redShift) & wireFormat.redMax) * 255 / wireFormat.redMax,
                ((value >> wireFormat.greenShift) & wireFormat.greenMax) * 255 / wireFormat.greenMax,
                ((value >> wireFormat.blueShift) & wireFormat.blueMax) * 255 / wireFormat.blueMax);
            if (actual != expected.pixelColor(x, y).rgb()) {
                std::fprintf(stderr, "pixel %d,%d actual=%08x expected=%08x\n",
                             x, y, actual, expected.pixelColor(x, y).rgb());
                assert(actual == expected.pixelColor(x, y).rgb());
            }
        }
    }
    const auto damage = fb.modifiedTiles();
    assert(damage.size() == (expectDamage ? 1 : 0));
    if (expectDamage) assert(damage.front() == QRect(0, 0, expected.width(), expected.height()));
}

int main(int argc, char **argv)
{
    QTemporaryDir runtime;
    assert(runtime.isValid());
    qputenv("QT_QPA_PLATFORM", "offscreen");
    qputenv("QT_QPA_PLATFORMTHEME", "generic");
    qputenv("XDG_RUNTIME_DIR", runtime.path().toUtf8());
    qputenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent-private-frame-unit-bus");
    qunsetenv("DISPLAY");
    qunsetenv("WAYLAND_DISPLAY");
    qunsetenv("XAUTHORITY");
    QGuiApplication app(argc, argv);
    PWFrameBuffer fb;
    // Allocates the public framebuffer before the offscreen Wayland lookup.
    // No event loop is entered and no PipeWire/compositor connection is needed.
    fb.startVirtualMonitor(QStringLiteral("unit"), QSize(3, 2), 1);
    auto *stream = fb.findChild<PipeWireSourceStream *>();
    assert(stream);
    auto *screen = rfbGetScreen(&argc, argv, fb.width(), fb.height(), 8, 3, 4);
    assert(screen);
    fb.getServerFormat(screen->serverFormat);
    wireFormat = screen->serverFormat;
    assert(wireFormat.trueColour && wireFormat.bitsPerPixel == 32);
    assert(wireFormat.redMax && wireFormat.greenMax && wireFormat.blueMax);
    std::printf("actual RFB pixel format: endian=%u R=%u/%u G=%u/%u B=%u/%u\n",
                static_cast<unsigned>(wireFormat.bigEndian),
                static_cast<unsigned>(wireFormat.redShift), static_cast<unsigned>(wireFormat.redMax),
                static_cast<unsigned>(wireFormat.greenShift), static_cast<unsigned>(wireFormat.greenMax),
                static_cast<unsigned>(wireFormat.blueShift), static_cast<unsigned>(wireFormat.blueMax));
    int changes = 0;
    QObject::connect(&fb, &FrameBuffer::frameBufferChanged, [&changes] { ++changes; });
    int frames = 0;
    for (const auto &format : formats) {
        for (const QSize size : {QSize(3, 2), QSize(7, 5), QSize(1, 1), QSize(3, 7)}) {
            QImage seed(size, QImage::Format_RGB32);
            for (int y = 0; y < size.height(); ++y) {
                for (int x = 0; x < size.width(); ++x) {
                    seed.setPixel(x, y, qRgb(18 + x * 17, 110 + y * 13, 178 - x * 9));
                }
            }
            QImage expected = seed.convertToFormat(format.image);
            QImage raw = expected;
#if !KPIPEWIRE60
            if (format.swap) raw = expected.rgbSwapped();
#endif
            const QSize previous(fb.width(), fb.height());
            const int before = changes;
            deliver(stream, raw, format);
            verify(fb, expected);
            assert(changes - before == (previous != size ? 1 : 0));
            ++frames;
        }
        std::printf("format %s passed odd widths and grow/shrink\n", format.name);
    }

    // Distinct padding must never become native pixels or overrun tight fb.
    const int width = 3, height = 5, stride = 32;
    std::vector<uchar> storage(stride * height, 0x5a);
#if KPIPEWIRE60
    const auto paddedFormat = QImage::Format_RGB32;
    const KrfbFrameFormat paddedCase{"padded-RGB32", paddedFormat};
#else
    const auto paddedFormat = QImage::Format_RGBX8888;
    const KrfbFrameFormat paddedCase{"padded-RGBx", paddedFormat, SPA_VIDEO_FORMAT_RGBx, false};
#endif
    QImage padded(storage.data(), width, height, stride, paddedFormat);
    padded.fill(qRgb(18, 210, 178));
    for (int y = 0; y < height; ++y) {
        std::memset(storage.data() + y * stride + width * 4, 0x5a, stride - width * 4);
    }
    deliver(stream, padded, paddedCase);
    verify(fb, padded);
    ++frames;

    // A present but null QImage must leave both allocation and geometry alone.
    const char *allocation = fb.data();
    const QSize previous(fb.width(), fb.height());
    const int before = changes;
    deliver(stream, QImage(), formats[0]);
    assert(fb.data() == allocation);
    assert(QSize(fb.width(), fb.height()) == previous);
    assert(changes == before);
    verify(fb, padded, false);
    std::printf("real frameReceived callback passed %d frames, padded stride and null preservation; legacy API=%d\n", frames, KPIPEWIRE60);
    rfbScreenCleanup(screen);
}
