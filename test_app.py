import unittest
from unittest.mock import patch

import qrcode

import app


class QRTest(unittest.TestCase):
    def test_pairing_waits_for_wifi(self):
        with patch("app.server.local_address", return_value=[]), \
             patch("app.server.create_server") as create_server:
            ui = app.PhoneMouseApp()
            ui.root.withdraw()
            try:
                ui.start()
                create_server.assert_not_called()
                self.assertEqual(ui.status.get(), "Waiting for Wi-Fi")
                self.assertEqual(ui.code.get(), "------")
                self.assertTrue(ui.awaiting_wireless)
            finally:
                ui.close()

    def test_displayed_qr_matches_selected_address(self):
        ui = app.PhoneMouseApp()
        ui.root.withdraw()
        try:
            ui.addresses = ["192.168.1.14"]
            for address in ("http://192.168.1.14:8765", "http://192.168.1.3:8765"):
                ui.address.set(address)
                ui.update_qr()
                qr = qrcode.QRCode(border=4)
                qr.add_data(address)
                matrix = qr.get_matrix()
                self.assertEqual(ui.qr_image.width(), len(matrix) * 5)
                for y, row in enumerate(matrix):
                    for x, dark in enumerate(row):
                        self.assertEqual(ui.qr_image.get(x * 5 + 2, y * 5 + 2) == (23, 25, 35), dark)
        finally:
            ui.close()


if __name__ == "__main__":
    unittest.main()
