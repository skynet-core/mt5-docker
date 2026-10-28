# /// script
# requires-python = ">=3.10"
# dependencies = ["pycryptodome>=3.20,<4", "pillow>=11,<13", "websocket-client>=1.8,<2"]
# ///
"""Authenticate and capture a nonblank framebuffer over VNC or noVNC's WebSocket."""

import argparse
import time

from rfb import RFBClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--password", default="testpass")
    parser.add_argument("--web", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    client = RFBClient(args.port, args.password, args.web)
    try:
        deadline = time.monotonic() + 30
        while True:
            image = client.screenshot()
            image.save(args.output)
            colors = image.getcolors(image.width * image.height)
            if (
                len(colors) > 128
                and sum(count for count, color in colors if max(color) > 32)
                > image.width * image.height * 0.05
            ):
                break
            assert time.monotonic() < deadline, (
                "Framebuffer remained blank for 30 seconds"
            )
            time.sleep(1)
        print(
            f"Authenticated {client.name!r}; captured {image.width}x{image.height}: {args.output}"
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
