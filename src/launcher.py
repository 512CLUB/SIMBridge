#!/usr/bin/env python3
import socket
import sys
import threading
from http.server import ThreadingHTTPServer

import webview

from server import ARCHIVE_SYNC, FORWARDER, Handler


HOST = "0.0.0.0"
LOCAL_HOST = "127.0.0.1"
START_PORT = 8765


def find_available_port(start=START_PORT, attempts=50):
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((HOST, port))
            except OSError:
                continue
            return port
    raise RuntimeError("没有找到可用本地端口")


class App:
    def __init__(self):
        self.port = find_available_port()
        self.url = f"http://{LOCAL_HOST}:{self.port}/"
        self.server = ThreadingHTTPServer((HOST, self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.stopped = False

    def start_server(self):
        self.thread.start()
        FORWARDER.start()
        ARCHIVE_SYNC.start()

    def stop_server(self):
        if self.stopped:
            return
        self.stopped = True
        FORWARDER.stop()
        ARCHIVE_SYNC.stop()
        self.server.shutdown()
        self.server.server_close()

    def run(self):
        self.start_server()
        window = webview.create_window(
            "SIMBridge",
            self.url,
            width=1180,
            height=780,
            min_size=(860, 600),
        )
        window.events.closed += self.stop_server
        try:
            options = {"private_mode": False}
            if sys.platform == "darwin":
                options["gui"] = "cocoa"
            webview.start(**options)
        finally:
            self.stop_server()


def main():
    App().run()


if __name__ == "__main__":
    main()
