import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch
import socket

import server


class FakeInput:
    def __init__(self):
        self.actions = []

    def move(self, dx, dy):
        self.actions.append(("move", dx, dy))

    def click(self, button):
        self.actions.append(("click", button))

    def hold(self, pressed):
        self.actions.append(("hold", pressed))

    def scroll(self, direction):
        self.actions.append(("scroll", direction))

    def text(self, value):
        self.actions.append(("text", value))

    def special(self, name):
        self.actions.append(("key", name))


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.device = FakeInput()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.state = {"code": "123456", "token": "", "failures": 0,
                            "locked_until": 0, "lock": threading.Lock(), "device": self.device,
                            "held": False, "last_input": 0, "last_error": ""}
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    def request(self, path, body, token="", origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port)
        conn.request("POST", path, json.dumps(body), headers={
            "Content-Type": "application/json",
            "Origin": self.origin if origin is None else origin,
            "Authorization": "Bearer " + token,
        })
        response = conn.getresponse()
        result = response.status, json.loads(response.read())
        conn.close()
        return result

    def test_pair_and_control(self):
        self.assertEqual(self.request("/api/input", {"type": "click", "button": "left"})[0], 401)
        self.assertEqual(self.request("/api/pair", {"code": "wrong"})[0], 403)
        status, result = self.request("/api/pair", {"code": "123456"})
        self.assertEqual(status, 200)
        token = result["token"]
        self.assertEqual(self.request("/api/input", {"type": "move", "dx": 8, "dy": -4}, token)[0], 200)
        self.assertEqual(self.request("/api/input", {"type": "text", "value": "Hello 🌍"}, token)[0], 200)
        self.assertEqual(self.device.actions, [("move", 8, -4), ("text", "Hello 🌍")])

    def test_rejects_invalid_and_cross_origin_input(self):
        token = self.request("/api/pair", {"code": "123456"})[1]["token"]
        self.assertEqual(self.request("/api/input", {"type": "move", "dx": 1000, "dy": 0}, token)[0], 400)
        self.assertEqual(self.request("/api/input", {"type": "move", "dx": True, "dy": 0}, token)[0], 400)
        self.assertEqual(self.request("/api/input", {"type": "text", "value": "\ud800"}, token)[0], 400)
        self.assertEqual(self.request("/api/input", {"type": "edit", "delete": -1, "value": "x"}, token)[0], 400)
        self.assertEqual(self.request("/api/input", {"type": "click", "button": "left"}, token,
                                      origin="http://evil.example")[0], 403)
        self.assertEqual(self.device.actions, [])

    def test_live_edit_and_hold(self):
        token = self.request("/api/pair", {"code": "123456"})[1]["token"]
        self.assertEqual(self.request("/api/input", {"type": "edit", "delete": 2, "value": "é"}, token)[0], 200)
        self.assertEqual(self.request("/api/input", {"type": "hold", "pressed": True}, token)[0], 200)
        self.assertEqual(self.request("/api/input", {"type": "keepalive"}, token)[0], 200)
        self.assertEqual(self.request("/api/input", {"type": "hold", "pressed": False}, token)[0], 200)
        self.assertEqual(self.device.actions, [("key", "backspace"), ("key", "backspace"),
                                               ("text", "é"), ("hold", True), ("hold", False)])

    def test_stale_hold_is_released(self):
        httpd = server.PhoneMouseServer(("127.0.0.1", 0), server.Handler)
        device = FakeInput()
        httpd.state = {"lock": threading.Lock(), "device": device, "held": True,
                       "last_input": 0, "last_error": ""}
        try:
            httpd.service_actions()
            self.assertEqual(device.actions, [("hold", False)])
            self.assertFalse(httpd.state["held"])
        finally:
            httpd.server_close()

    def test_windows_unicode_events(self):
        device = server.WinInput()
        captured = []
        device.send_input = lambda count, inputs, size: captured.extend(
            (item.type, item.data.key.scan, item.data.key.flags) for item in inputs
        ) or count
        device.text("A🌍")
        self.assertEqual(captured, [(1, 65, 4), (1, 65, 6),
                                    (1, 0xD83C, 4), (1, 0xD83C, 6),
                                    (1, 0xDF0D, 4), (1, 0xDF0D, 6)])

    def test_wifi_address_comes_before_ethernet_and_virtual_adapters(self):
        addrs = {
            "Ethernet": [SimpleNamespace(family=socket.AF_INET, address="192.168.1.2")],
            "vEthernet (WSL)": [SimpleNamespace(family=socket.AF_INET, address="172.29.208.1")],
            "Wi-Fi": [SimpleNamespace(family=socket.AF_INET, address="192.168.1.14")],
        }
        stats = {name: SimpleNamespace(isup=True) for name in addrs}
        with patch("server.psutil.net_if_addrs", return_value=addrs), \
             patch("server.psutil.net_if_stats", return_value=stats):
            self.assertEqual(server.local_address(),
                             ["192.168.1.14", "192.168.1.2", "172.29.208.1"])
            self.assertEqual(server.local_address(wireless_only=True), ["192.168.1.14"])
            stats["Wi-Fi"].isup = False
            self.assertEqual(server.local_address(wireless_only=True), [])


if __name__ == "__main__":
    unittest.main()
