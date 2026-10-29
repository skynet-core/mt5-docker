"""Use protocol frames to verify input and capture, independently of any desktop."""

import struct
import unittest
from unittest.mock import patch

from rfb import RFBClient


class Wire:
    def __init__(self, payload):
        self.payload = bytearray(payload)
        self.sent = []
        self.closed = False

    def recv(self, _size=None):
        # Split even headers across receives to exercise buffered framing.
        result = bytes(self.payload[:3])
        del self.payload[:3]
        return result

    def sendall(self, payload):
        self.sent.append(payload)

    send_binary = sendall

    def close(self):
        self.closed = True


def handshake(*, methods=b"\x02", status=0, greeting=b"RFB 003.008\n"):
    name = b"private framebuffer"
    return (
        greeting
        + bytes([len(methods)])
        + methods
        + b"0123456789abcdef"
        + struct.pack(">IHH", status, 3, 2)
        + bytes(16)
        + struct.pack(">I", len(name))
        + name
    )


class RfbProtocolTests(unittest.TestCase):
    def client(self, payload=b"", *, web=False):
        wire = Wire(handshake() + payload)
        transport = "websocket.create_connection" if web else "socket.create_connection"
        with patch(transport, return_value=wire) as connect:
            client = RFBClient(45678, web=web)
        return client, wire, connect

    def test_tcp_and_websocket_authentication_use_same_wire_contract(self):
        for web in (False, True):
            with self.subTest(web=web):
                client, wire, connect = self.client(web=web)
                self.assertEqual((client.width, client.height), (3, 2))
                self.assertEqual(client.name, "private framebuffer")
                self.assertEqual(
                    wire.sent[:3],
                    [
                        b"RFB 003.008\n",
                        b"\x02",
                        bytes.fromhex("177cc6afb100cbf3666dbc5efaa5f1c1"),
                    ],
                )
                self.assertEqual(wire.sent[-1], struct.pack(">BBHi", 2, 0, 1, 0))
                if web:
                    connect.assert_called_once_with(
                        "ws://127.0.0.1:45678/websockify",
                        timeout=20,
                        subprotocols=["binary"],
                    )
                else:
                    connect.assert_called_once_with(("127.0.0.1", 45678), timeout=20)
                client.close()
                self.assertTrue(wire.closed)

    def test_invalid_greeting_authentication_method_and_password_are_rejected(self):
        for payload, message in (
            (handshake(greeting=b"NOT A SERVER"), "No RFB greeting"),
            (handshake(methods=b"\x01"), "Password authentication was not offered"),
            (handshake(status=1), "VNC authentication failed"),
        ):
            with self.subTest(message=message):
                wire = Wire(payload)
                with (
                    patch("socket.create_connection", return_value=wire),
                    self.assertRaisesRegex(AssertionError, message),
                ):
                    RFBClient(45678)

    def test_capture_handles_bells_clipboard_messages_and_offset_rectangles(self):
        pixels = bytes.fromhex("0000ff00 00ff0000")
        frame = (
            b"\x02"  # Bell before the framebuffer update.
            + b"\x03\0\0\0"
            + struct.pack(">I", 4)
            + b"test"
            + b"\x00\0"
            + struct.pack(">H", 1)
            + struct.pack(">HHHHi", 1, 0, 2, 1, 0)
            + pixels
        )
        client, wire, _ = self.client(frame)
        image = client.screenshot()
        self.assertEqual(image.getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(image.getpixel((1, 0)), (255, 0, 0))
        self.assertEqual(image.getpixel((2, 0)), (0, 255, 0))
        self.assertEqual(wire.sent[-1], struct.pack(">BBHHHH", 3, 0, 0, 0, 3, 2))
        # The returned image must not alias the client framebuffer.
        image.putpixel((0, 0), (255, 255, 255))
        self.assertEqual(client.frame.getpixel((0, 0)), (0, 0, 0))

    def test_unexpected_server_message_encoding_and_eof_fail_capture(self):
        for payload, exception, message in (
            (b"\x04", RuntimeError, "Unexpected server message 4"),
            (
                b"\x00\0\0\x01" + struct.pack(">HHHHi", 0, 0, 1, 1, 5),
                AssertionError,
                "Unexpected encoding 5",
            ),
            (b"", RuntimeError, "VNC connection closed"),
        ):
            with self.subTest(message=message):
                client, _, _ = self.client(payload)
                with self.assertRaisesRegex(exception, message):
                    client.screenshot()

    def test_click_drag_and_key_transitions_release_their_buttons(self):
        client, wire, _ = self.client()
        wire.sent.clear()
        with patch("rfb.time.sleep"):
            client.click(1.9, 1.2)
            client.drag(0, 0, 2, 1)
        events = [struct.unpack(">BBHH", payload) for payload in wire.sent]
        self.assertEqual(events[:3], [(5, 0, 1, 1), (5, 1, 1, 1), (5, 0, 1, 1)])
        self.assertEqual(events[3:5], [(5, 0, 0, 0), (5, 1, 0, 0)])
        self.assertEqual(events[-1], (5, 0, 2, 1))
        self.assertEqual(client.position, (2, 1))
        self.assertTrue(all(event[1] == 1 for event in events[5:-1]))

    def test_text_uses_shift_unicode_and_enter_keys_and_chords_release_modifiers(self):
        client, wire, _ = self.client()
        wire.sent.clear()
        with patch("rfb.time.sleep"):
            client.text("A!éλ\n")
            client.chord(0xFFE3, ord("s"))
        events = [struct.unpack(">BBHI", payload) for payload in wire.sent]
        self.assertEqual(
            events[:4],
            [
                (4, 1, 0, 0xFFE1),
                (4, 1, 0, ord("A")),
                (4, 0, 0, ord("A")),
                (4, 0, 0, 0xFFE1),
            ],
        )
        self.assertIn((4, 1, 0, ord("é")), events)
        self.assertIn((4, 1, 0, 0x01000000 | ord("λ")), events)
        self.assertIn((4, 1, 0, 0xFF0D), events)
        self.assertEqual(
            events[-4:],
            [
                (4, 1, 0, 0xFFE3),
                (4, 1, 0, ord("s")),
                (4, 0, 0, ord("s")),
                (4, 0, 0, 0xFFE3),
            ],
        )
        self.assertFalse(client.held_keys)

    def test_close_releases_keys_and_pointer_even_after_input_failure(self):
        client, wire, _ = self.client()
        client.key(0xFFE3, True)
        client.key(ord("a"), True)
        client.pointer(2, 1, 1)
        wire.sent.clear()
        client.close()
        released = {struct.unpack(">BBHI", payload)[3] for payload in wire.sent[:-1]}
        self.assertEqual(released, {0xFFE3, ord("a")})
        self.assertEqual(wire.sent[-1], struct.pack(">BBHH", 5, 0, 2, 1))
        self.assertTrue(wire.closed)
        client, wire, _ = self.client()
        client.held_keys.add(0xFFE3)
        with (
            patch.object(client, "send", side_effect=OSError("closed test socket")),
            self.assertRaises(OSError),
        ):
            client.close()
        self.assertTrue(wire.closed)

    def test_read_only_capture_and_unpressed_motion_do_not_warp_on_disconnect(self):
        for moved in (False, True):
            with self.subTest(moved=moved):
                client, wire, _ = self.client()
                if moved:
                    client.pointer(2, 1)
                wire.sent.clear()
                client.close()
                self.assertEqual(wire.sent, [])
                self.assertTrue(wire.closed)


if __name__ == "__main__":
    unittest.main()
