"""Windows control window for Phone Mouse."""

import threading
import tkinter as tk
from tkinter import ttk

import qrcode

import server


class PhoneMouseApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Phone Mouse")
        self.root.geometry("720x520")
        self.root.minsize(650, 490)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.httpd = None
        self.thread = None
        self.addresses = []
        self.awaiting_wireless = False

        self.status = tk.StringVar(value="Starting…")
        self.address = tk.StringVar(value="Looking for a local network address…")
        self.code = tk.StringVar(value="------")
        self.error = tk.StringVar()

        frame = ttk.Frame(self.root, padding=24)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="Phone Mouse", font=("Segoe UI", 19, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.status, font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", pady=(2, 18))

        details = ttk.LabelFrame(frame, text="Connect your phone", padding=16)
        details.grid(row=2, column=0, sticky="ew")
        details.columnconfigure(0, weight=1)
        ttk.Label(details, text="1. Open this URL on your phone (same Wi-Fi):").grid(row=0, column=0, sticky="w")
        self.address_box = ttk.Combobox(details, textvariable=self.address, state="readonly", width=37)
        self.address_box.grid(row=1, column=0, sticky="w", pady=(8, 6))
        self.address_box.bind("<<ComboboxSelected>>", self.update_qr)
        self.copy_button = ttk.Button(details, text="Copy address", command=self.copy_address)
        self.copy_button.grid(row=2, column=0, sticky="w", pady=(0, 16))
        ttk.Label(details, text="2. Enter this pairing code on your phone:").grid(row=3, column=0, sticky="w")
        ttk.Label(details, textvariable=self.code, font=("Consolas", 25, "bold")).grid(
            row=4, column=0, sticky="w", pady=(4, 2))
        self.qr_label = ttk.Label(details, text="QR code appears here")
        self.qr_label.grid(row=0, column=1, rowspan=5, padx=(20, 0), sticky="ne")
        ttk.Label(details, text="Or scan this QR code\nto open the URL", justify="center").grid(
            row=5, column=1, padx=(20, 0), pady=(8, 0))
        self.qr_image = None

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, sticky="w", pady=(18, 8))
        self.start_button = ttk.Button(buttons, text="Start server", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Stop server", command=self.stop)
        self.stop_button.pack(side="left", padx=(10, 0))
        self.stop_button.state(["disabled"])
        self.copy_button.state(["disabled"])
        ttk.Label(frame, textvariable=self.error, foreground="#a12424", wraplength=500).grid(
            row=4, column=0, sticky="w")
        ttk.Label(frame, text="Keep this window open while using your phone.\nIf Windows Firewall asks, allow access on Private networks.",
                  wraplength=500).grid(row=5, column=0, sticky="sw", pady=(18, 0))
        frame.rowconfigure(5, weight=1)

        self.root.after(0, self.start)
        self.root.after(500, self.refresh_status)

    def start(self):
        if self.httpd:
            return
        self.addresses = server.local_address(wireless_only=True)
        if not self.addresses:
            self.awaiting_wireless = True
            self.status.set("Waiting for Wi-Fi")
            self.address.set("Connect this PC to Wi-Fi")
            self.error.set("Connect this PC and your phone to the same Wi-Fi network.")
            return
        try:
            httpd = server.create_server()
        except OSError as exc:
            self.awaiting_wireless = False
            self.status.set("Could not start")
            self.error.set(f"Port {server.PORT} is in use. Close another Phone Mouse window, then click Start.")
            return
        self.httpd = httpd
        self.awaiting_wireless = False
        self.address_box["values"] = [f"http://{ip}:{server.PORT}" for ip in self.addresses]
        self.address.set(self.address_box["values"][0] if self.addresses else "No local network address found")
        self.update_qr()
        self.code.set(httpd.state["code"])
        self.error.set("")
        self.start_button.state(["disabled"])
        self.stop_button.state(["!disabled"])
        self.copy_button.state(["!disabled"] if self.addresses else ["disabled"])
        self.thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self.thread.start()
        self.status.set("Waiting for phone")

    def stop(self):
        self.awaiting_wireless = False
        if not self.httpd:
            return
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.httpd = None
        self.thread = None
        self.addresses = []
        self.address_box["values"] = []
        self.address.set("Start the server to get an address")
        self.qr_label.configure(image="", text="QR code appears here")
        self.qr_image = None
        self.code.set("------")
        self.status.set("Stopped")
        self.error.set("")
        self.start_button.state(["!disabled"])
        self.stop_button.state(["disabled"])
        self.copy_button.state(["disabled"])

    def copy_address(self):
        if self.addresses:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.address.get())
            self.root.update()

    def update_qr(self, event=None):
        if not self.addresses:
            return
        qr = qrcode.QRCode(border=4)
        qr.add_data(self.address.get())
        matrix = qr.get_matrix()
        image = tk.PhotoImage(width=len(matrix), height=len(matrix))
        for y, row in enumerate(matrix):
            image.put("{" + " ".join("#171923" if cell else "#ffffff" for cell in row) + "}", to=(0, y))
        self.qr_image = image.zoom(5, 5)
        self.qr_label.configure(image=self.qr_image, text="")

    def refresh_status(self):
        if self.httpd and server.local_address(wireless_only=True) != self.addresses:
            self.stop()
            self.start()
        elif self.awaiting_wireless:
            self.start()
        if self.httpd:
            with self.httpd.state["lock"]:
                paired = bool(self.httpd.state["token"])
                error = self.httpd.state["last_error"]
            self.status.set("Phone paired" if paired else "Waiting for phone")
            if error:
                self.error.set("Windows rejected an input. Check the focused app's permissions.")
        self.root.after(500, self.refresh_status)

    def close(self):
        self.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    PhoneMouseApp().run()
