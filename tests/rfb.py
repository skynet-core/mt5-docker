"""Small RFB 3.8 client used for actual input and framebuffer regression tests."""

import socket
import struct
import time

import websocket
from Crypto.Cipher import DES
from PIL import Image


class RFBClient:
    def __init__(self, port, password="testpass", web=False, host="127.0.0.1"):
        if web:
            self.connection = websocket.create_connection(
                f"ws://{host}:{port}/websockify", timeout=20, subprotocols=["binary"]
            )
            self.send = self.connection.send_binary
            self.receive = self.connection.recv
        else:
            self.connection = socket.create_connection((host, port), timeout=20)
            self.send = self.connection.sendall
            self.receive = lambda: self.connection.recv(1024 * 1024)
        self.pending = bytearray()
        self.held_keys = set()
        self.position = (0, 0)
        self.buttons = 0
        assert self.read(12).startswith(b"RFB 003."), "No RFB greeting"
        self.send(b"RFB 003.008\n")
        methods = self.read(self.read(1)[0])
        assert 2 in methods, f"Password authentication was not offered: {methods}"
        self.send(b"\x02")
        key = bytes(
            int(f"{byte:08b}"[::-1], 2)
            for byte in password.encode("ascii").ljust(8, b"\0")
        )
        self.send(DES.new(key, DES.MODE_ECB).encrypt(self.read(16)))
        assert self.read(4) == b"\0\0\0\0", "VNC authentication failed"
        self.send(b"\x01")
        self.width, self.height = struct.unpack(">HH", self.read(4))
        self.read(16)
        self.name = self.read(struct.unpack(">I", self.read(4))[0]).decode(
            errors="replace"
        )
        pixel_format = struct.pack(
            ">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0
        )
        self.send(b"\x00\0\0\0" + pixel_format)
        self.send(struct.pack(">BBHi", 2, 0, 1, 0))  # Raw encoding.
        self.frame = Image.new("RGB", (self.width, self.height))

    def read(self, count):
        while len(self.pending) < count:
            part = self.receive()
            if not part:
                raise RuntimeError("VNC connection closed")
            self.pending.extend(part)
        result = bytes(self.pending[:count])
        del self.pending[:count]
        return result

    def screenshot(self):
        self.send(struct.pack(">BBHHHH", 3, 0, 0, 0, self.width, self.height))
        message = self.read(1)[0]
        while message != 0:
            if message == 2:
                pass  # Bell.
            elif message == 3:
                self.read(3)
                self.read(struct.unpack(">I", self.read(4))[0])
            else:
                raise RuntimeError(f"Unexpected server message {message}")
            message = self.read(1)[0]
        self.read(1)
        for _ in range(struct.unpack(">H", self.read(2))[0]):
            x, y, width, height, encoding = struct.unpack(">HHHHi", self.read(12))
            assert encoding == 0, f"Unexpected encoding {encoding}"
            pixels = self.read(width * height * 4)
            rectangle = Image.frombytes("RGB", (width, height), pixels, "raw", "BGRX")
            self.frame.paste(rectangle, (x, y))
        return self.frame.copy()

    def pointer(self, x, y, buttons=0):
        self.position = (int(x), int(y))
        self.buttons = buttons
        self.send(struct.pack(">BBHH", 5, buttons, *self.position))

    def click(self, x, y):
        self.pointer(x, y)
        # Allow remote pointer focus to settle before pressing the button.
        time.sleep(0.1)
        self.pointer(x, y, 1)
        time.sleep(0.08)
        self.pointer(x, y)

    def drag(self, x, y, dx, dy):
        self.pointer(x, y)
        time.sleep(0.1)
        self.pointer(x, y, 1)
        time.sleep(0.15)
        for step in range(1, 16):
            self.pointer(x + dx * step / 15, y + dy * step / 15, 1)
            time.sleep(0.04)
        self.pointer(x + dx, y + dy)

    def key(self, keysym, down):
        self.send(struct.pack(">BBHI", 4, int(down), 0, keysym))
        if down:
            self.held_keys.add(keysym)
        else:
            self.held_keys.discard(keysym)
        # Wine reads modifier state asynchronously after the remote key event.
        time.sleep(0.01)

    def text(self, value):
        for character in value:
            # RFB clients report modifier transitions as well as the resulting
            # keysym. Model a US keyboard, including Shift for capitals/symbols.
            shifted = character in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ~!@#$%^&*()_+{}|:"<>?'
            if shifted:
                self.key(0xFFE1, True)
            keysym = 0xFF0D if character == "\n" else ord(character)
            if keysym > 255 and character != "\n":
                keysym |= 0x01000000
            self.key(keysym, True)
            self.key(keysym, False)
            if shifted:
                self.key(0xFFE1, False)
            time.sleep(0.006)

    def chord(self, modifier, key):
        self.key(modifier, True)
        self.key(key, True)
        self.key(key, False)
        self.key(modifier, False)

    def close(self):
        try:
            for keysym in self.held_keys.copy():
                self.key(keysym, False)
            if self.buttons:
                self.pointer(*self.position)
        finally:
            self.connection.close()
