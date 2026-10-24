"""Use a phone browser as a mouse and keyboard for this Windows PC."""

import ctypes
import ipaddress
import json
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import psutil


PORT = 8765
INDEX = Path(__file__).with_name("index.html").read_bytes()


class WinInput:
    def __init__(self):
        from ctypes import wintypes

        pointer = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else wintypes.DWORD

        class MouseInput(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("data", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("extra", pointer)]

        class KeyInput(ctypes.Structure):
            _fields_ = [("vk", wintypes.WORD), ("scan", wintypes.WORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("extra", pointer)]

        class InputData(ctypes.Union):
            _fields_ = [("mouse", MouseInput), ("key", KeyInput)]

        class Input(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("data", InputData)]

        self.Input = Input
        self.InputData = InputData
        self.MouseInput = MouseInput
        self.KeyInput = KeyInput
        self.send_input = ctypes.WinDLL("user32", use_last_error=True).SendInput
        self.send_input.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
        self.send_input.restype = wintypes.UINT

    def send(self, *events):
        inputs = (self.Input * len(events))(*events)
        sent = self.send_input(len(inputs), inputs, ctypes.sizeof(self.Input))
        if sent != len(inputs):
            raise OSError(ctypes.get_last_error(), "Windows rejected the input")

    def mouse(self, dx=0, dy=0, data=0, flags=0):
        return self.Input(0, self.InputData(mouse=self.MouseInput(dx, dy, data, flags, 0, 0)))

    def key(self, scan, flags):
        return self.Input(1, self.InputData(key=self.KeyInput(0, scan, flags, 0, 0)))

    def move(self, dx, dy):
        self.send(self.mouse(dx, dy, flags=0x0001))

    def click(self, button):
        down, up = (0x0002, 0x0004) if button == "left" else (0x0008, 0x0010)
        self.send(self.mouse(flags=down), self.mouse(flags=up))

    def hold(self, pressed):
        self.send(self.mouse(flags=0x0002 if pressed else 0x0004))

    def scroll(self, direction):
        self.send(self.mouse(data=ctypes.c_uint32(direction * 120).value, flags=0x0800))

    def text(self, value):
        encoded = value.encode("utf-16-le")
        units = [int.from_bytes(encoded[i:i + 2], "little") for i in range(0, len(encoded), 2)]
        for start in range(0, len(units), 64):
            events = []
            for unit in units[start:start + 64]:
                events.extend((self.key(unit, 0x0004), self.key(unit, 0x0006)))
            self.send(*events)

    def special(self, name):
        scan = {"enter": 0x0D, "backspace": 0x08}[name]
        self.send(self.Input(1, self.InputData(key=self.KeyInput(scan, 0, 0, 0, 0))),
                  self.Input(1, self.InputData(key=self.KeyInput(scan, 0, 0x0002, 0, 0))))


def apply_action(device, action):
    if not isinstance(action, dict):
        raise ValueError("Expected an action object")
    kind = action.get("type")
    if kind == "move":
        dx, dy = action.get("dx"), action.get("dy")
        if type(dx) is not int or type(dy) is not int or not all(-200 <= v <= 200 for v in (dx, dy)):
            raise ValueError("Invalid movement")
        device.move(dx, dy)
    elif kind == "click" and action.get("button") in ("left", "right"):
        device.click(action["button"])
    elif kind == "hold" and type(action.get("pressed")) is bool:
        device.hold(action["pressed"])
    elif kind == "scroll" and type(action.get("direction")) is int and action["direction"] in (-1, 1):
        device.scroll(action["direction"])
    elif kind in ("text", "edit"):
        value = action.get("value")
        delete = action.get("delete", 0) if kind == "edit" else 0
        if (type(delete) is not int or not 0 <= delete <= 1000 or
                not isinstance(value, str) or len(value) > 1000 or
                (kind == "text" and not value) or (kind == "edit" and not delete and not value)):
            raise ValueError("Invalid text edit")
        try:
            value.encode("utf-16-le")
        except UnicodeEncodeError as exc:
            raise ValueError("Invalid text") from exc
        for _ in range(delete):
            device.special("backspace")
        if value:
            device.text(value)
    elif kind == "key" and action.get("name") in ("enter", "backspace"):
        device.special(action["name"])
    elif kind == "keepalive":
        return
    else:
        raise ValueError("Unknown or invalid action")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Windowed EXEs have no stderr stream.

    def reply(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        try:
            url = urlsplit("http://" + self.headers["Host"])
            address = ipaddress.ip_address(url.hostname)
            return url.port == self.server.server_port and (address.is_private or address.is_loopback)
        except (KeyError, ValueError, TypeError):
            return False

    def do_GET(self):
        if not self.valid_host() or self.path != "/":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(INDEX)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(INDEX)

    def do_POST(self):
        host = self.headers.get("Host", "")
        if not self.valid_host() or self.headers.get("Origin") != "http://" + host:
            self.reply(403, {"error": "Invalid origin"})
            return
        if self.path not in ("/api/pair", "/api/input"):
            self.reply(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if self.headers.get("Content-Type") != "application/json" or not 0 < length <= 8192:
                raise ValueError("Invalid request")
            body = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self.reply(400, {"error": "Invalid request"})
            return
        state = self.server.state
        if self.path == "/api/pair":
            with state["lock"]:
                if time.monotonic() < state["locked_until"]:
                    self.reply(429, {"error": "Too many attempts. Try again in one minute."})
                    return
                if not isinstance(body, dict) or not isinstance(body.get("code"), str) or not secrets.compare_digest(body["code"], state["code"]):
                    state["failures"] += 1
                    if state["failures"] >= 5:
                        state["locked_until"] = time.monotonic() + 60
                        state["failures"] = 0
                    self.reply(403, {"error": "Wrong pairing code"})
                    return
                state["token"] = secrets.token_urlsafe(32)
                state["failures"] = 0
                self.reply(200, {"token": state["token"]})
            return
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        with state["lock"]:
            if not token or not secrets.compare_digest(token, state["token"]):
                self.reply(401, {"error": "Pair your phone first"})
                return
            try:
                apply_action(state["device"], body)
                if body.get("type") == "hold":
                    state["held"] = body["pressed"]
                if state["held"]:
                    state["last_input"] = time.monotonic()
            except ValueError as exc:
                self.reply(400, {"error": str(exc)})
                return
            except OSError as exc:
                state["last_error"] = str(exc)
                self.reply(500, {"error": "Windows rejected the input"})
                return
        self.reply(200, {"ok": True})


class PhoneMouseServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def service_actions(self):
        super().service_actions()
        with self.state["lock"]:
            if self.state["held"] and time.monotonic() - self.state["last_input"] > 10:
                try:
                    self.state["device"].hold(False)
                except OSError as exc:
                    self.state["last_error"] = str(exc)
                self.state["held"] = False

    def server_close(self):
        if hasattr(self, "state"):
            with self.state["lock"]:
                if self.state["held"]:
                    try:
                        self.state["device"].hold(False)
                    except OSError as exc:
                        self.state["last_error"] = str(exc)
                    self.state["held"] = False
        super().server_close()


def local_address(wireless_only=False):
    stats = psutil.net_if_stats()
    addresses = []
    for name, entries in psutil.net_if_addrs().items():
        if not stats.get(name) or not stats[name].isup:
            continue
        name_key = name.lower().replace("-", "")
        priority = 0 if any(word in name_key for word in ("wifi", "wlan", "wireless")) else 1
        if any(word in name_key for word in ("virtual", "vethernet", "vmware", "loopback")):
            priority = 2
        if wireless_only and priority != 0:
            continue
        for entry in entries:
            if entry.family != socket.AF_INET:
                continue
            address = ipaddress.ip_address(entry.address)
            if address.is_private and not address.is_loopback and not address.is_link_local:
                addresses.append((priority, entry.address))
    return list(dict.fromkeys(address for _, address in sorted(addresses)))


def create_server():
    code = f"{secrets.randbelow(1_000_000):06d}"
    device = WinInput()
    server = PhoneMouseServer(("0.0.0.0", PORT), Handler)
    server.state = {"code": code, "token": "", "failures": 0, "locked_until": 0,
                    "lock": threading.Lock(), "device": device, "last_error": "",
                    "held": False, "last_input": 0}
    return server


def main():
    if sys.platform != "win32":
        raise SystemExit("This server runs on Windows only")
    server = create_server()
    addresses = local_address()
    if addresses:
        print("Open one of these addresses on your phone (same Wi-Fi):")
        for address in addresses:
            print(f"  http://{address}:{PORT}")
    else:
        print("No local network address found. Connect this PC to Wi-Fi and try again.")
    print(f"Pairing code: {server.state['code']}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
