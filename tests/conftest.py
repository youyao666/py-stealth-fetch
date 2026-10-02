# -*- coding: utf-8 -*-
"""共享 fixture：本地线程 HTTP 服务（真实 socket，零第三方）。"""
import http.server
import json
import threading
import urllib.parse

import pytest


class Router(http.server.BaseHTTPRequestHandler):
    """按路径路由的本地服务：覆盖 GET/POST/重定向/Cookie/重复头/慢响应/二进制。"""

    def _send(self, code, body: bytes, headers: list[tuple[str, str]]):
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(p.query)
        if p.path == "/echo" or p.path.endswith("/echo"):
            body = json.dumps({"qs": q, "cookie": self.headers.get("Cookie", ""),
                               "ua": self.headers.get("User-Agent", "")}).encode()
            self._send(200, body, [("Content-Type", "application/json; charset=utf-8")])
        elif p.path == "/setcookies":
            self._send(200, b"ok", [("Set-Cookie", "a=1; Path=/"),
                                    ("Set-Cookie", "b=2; Path=/sub"),
                                    ("Set-Cookie", "dup=host; Path=/"),
                                    ("X-Dup", "v1"), ("X-Dup", "v2")])
        elif p.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/echo?redirected=1")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif p.path == "/slow":
            import time
            time.sleep(float(q.get("t", ["5"])[0]))
            self._send(200, b"finally", [])
        elif p.path == "/429":
            self._send(429, b"slow down", [("Retry-After", "1"),
                                           ("Content-Type", "text/plain")])
        elif p.path == "/429-date":
            import email.utils
            future = email.utils.formatdate(4102444800)  # 2100-01-01，远超任何预算
            self._send(429, b"slow down", [("Retry-After", future)])
        elif p.path == "/503":
            self._send(503, b"unavailable", [("Content-Type", "text/plain")])
        elif p.path == "/403":
            self._send(403, b"forbidden", [("Content-Type", "text/plain")])
        elif p.path == "/bin":
            self._send(200, bytes(range(256)), [("Content-Type", "application/octet-stream")])
        elif p.path == "/gbk":
            self._send(200, "中文".encode("gbk"), [("Content-Type", "text/html; charset=gbk")])
        elif p.path == "/badjson":
            self._send(200, b"{not json", [("Content-Type", "application/json")])
        elif p.path == "/challenge":
            self._send(200, b'<html>please complete the captcha</html>',
                       [("Content-Type", "text/html")])
        else:
            self._send(404, b"nf", [])

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n)
        # 记录收到的 POST 次数，用于断言"未重复提交"
        Router.post_count = getattr(Router, "post_count", 0) + 1
        self._send(200, json.dumps({"received": data.decode("utf-8", "replace"),
                                    "count": Router.post_count}).encode(),
                   [("Content-Type", "application/json")])

    def log_message(self, *a):
        pass


@pytest.fixture()
def local_base():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Router)
    Router.post_count = 0
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()
