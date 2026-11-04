// SPDX-License-Identifier: GPL-2.0-or-later
// Compile the exact patched KRFB method against real Qt and LibVNCServer.
#undef NDEBUG
#include <QList>
#include <QRect>
#include <rfb.h>
extern "C" {
#include <rfbregion.h>
}
#include <sys/socket.h>
#include <unistd.h>
#include <cassert>
#include <cstdio>
#include <vector>

class RfbServer {
    struct Private { rfbScreenInfoPtr screen; } storage;
    Private *d = &storage;
public:
    explicit RfbServer(rfbScreenInfoPtr screen) { d->screen = screen; }
    void updateScreen(const QList<QRect> &modifiedTiles);
};
#include "krfb-damage-method.inc"

int main(int argc, char **argv)
{
    constexpr int width = 9, height = 7;
    rfbLogEnable(0);
    auto *screen = rfbGetScreen(&argc, argv, width, height, 8, 3, 4);
    assert(screen);
    std::vector<char> pixels(width * height * 4);
    screen->frameBuffer = pixels.data();
    int sockets[2];
    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
    auto *client = rfbNewClient(screen, sockets[0]);
    assert(client);
    RfbServer server(screen);
    const QList<QRect> cases = {
        QRect(2, 3, 1, 1), QRect(1, 2, 5, 3), QRect(0, 0, width, height),
        QRect(0, height - 1, width, 1), QRect(width - 1, 0, 1, height),
        QRect(-2, -3, 5, 6), QRect(2, 3, 0, 0)
    };
    int missing = 0, unexpected = 0;
    for (const auto &damage : cases) {
        sraRgnMakeEmpty(client->modifiedRegion);
        server.updateScreen({damage});
        for (int y = 0; y < height; ++y) {
            for (int x = 0; x < width; ++x) {
                auto *point = sraRgnCreateRect(x, y, x + 1, y + 1);
                sraRgnAnd(point, client->modifiedRegion);
                const bool marked = !sraRgnEmpty(point);
                sraRgnDestroy(point);
                const bool expected = damage.contains(x, y);
                missing += expected && !marked;
                unexpected += !expected && marked;
            }
        }
    }
    std::printf("Actual KRFB damage: %lld rectangles, %d missing, %d unexpected pixels\n",
                static_cast<long long>(cases.size()), missing, unexpected);
    rfbClientConnectionGone(client);
    close(sockets[1]);
    rfbScreenCleanup(screen);
    return missing || unexpected ? 1 : 0;
}
