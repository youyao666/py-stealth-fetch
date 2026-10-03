# -*- coding: utf-8 -*-
"""共享 fixture：本地线程 HTTP 服务（真实 socket，零第三方）。"""
import http.server
import json
import socket
import socketserver
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
        elif p.path.startswith("/setvar/"):
            i = p.path.rsplit("/", 1)[1]
            self._send(200, b"ok", [("Set-Cookie", f"c{i}={i}; Path=/")])
        elif p.path == "/allcookies":
            self._send(200, json.dumps({"cookie": self.headers.get("Cookie", "")}).encode(),
                       [("Content-Type", "application/json")])
        elif p.path == "/big":
            self._send(200, b"x" * 100_000, [("Content-Type", "application/octet-stream")])
        elif p.path == "/viaproxy":
            self._send(200, b"via-proxy-ok", [])
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


class _LocalHTTPServer(http.server.ThreadingHTTPServer):
    # Windows 上默认 listen backlog=5，12+ 并发连接会被直接拒绝（实测 CI）
    request_queue_size = 128


@pytest.fixture()
def local_base():
    srv = _LocalHTTPServer(("127.0.0.1", 0), Router)
    Router.post_count = 0
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


# ---------- 本地迷你 HTTP 正向代理（P0-2：验证引擎的 proxy 参数真实生效） ----------

class _MiniProxyHandler(socketserver.BaseRequestHandler):
    seen: list = []          # noqa: RUF012 类属性（fixture 每次重置）

    def handle(self):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.request.recv(65536)
            if not chunk:
                return
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").splitlines()
        method, target, _ver = lines[0].split()
        if method == "CONNECT":
            # CONNECT 隧道（chrome_fp 等对明文目标也走隧道）
            host, port = target.rsplit(":", 1)
            _MiniProxyHandler.seen.append(target)
            print(f"[proxy] {lines[0][:90]}")
            try:
                upstream = socket.create_connection((host, int(port)), timeout=10)
            except OSError:
                self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                return
            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self._pipe(self.request, upstream)
            return
        # 正向代理收到绝对形式请求行：GET http://host:port/path HTTP/1.1
        host_port = urllib.parse.urlsplit(target).netloc
        _MiniProxyHandler.seen.append(host_port)
        print(f"[proxy] {lines[0][:90]}")
        # 把请求行改回相对形式后原样转发（仅支持明文 HTTP 目标，测试够用）
        rel = lines[0].replace(target, urllib.parse.urlsplit(target).path or "/")
        out_head = "\r\n".join([rel, *lines[1:]]).encode("latin-1") + b"\r\n\r\n"
        # 取 Content-Length 决定 body 长度
        clen = 0
        for line in lines[1:]:
            if line.lower().startswith("content-length:"):
                clen = int(line.split(":", 1)[1])
        while len(rest) < clen:
            chunk = self.request.recv(65536)
            if not chunk:
                break
            rest += chunk
        try:
            upstream = socket.create_connection(("127.0.0.1", int(host_port.rsplit(":", 1)[1])), timeout=10)
        except OSError:
            self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        upstream.sendall(out_head + rest)
        self._pipe(self.request, upstream)

    @staticmethod
    def _pipe(a, b):
        """双向转发直到任一侧关闭。"""
        import select
        sockets = [a, b]
        while sockets:
            readable, _w, _e = select.select(sockets, [], [], 15)
            if not readable:
                break
            for src in readable:
                try:
                    data = src.recv(65536)
                except OSError:
                    data = b""
                if not data:
                    for sck in sockets:
                        try:
                            sck.close()
                        except OSError:
                            pass
                    return
                dst = b if src is a else a
                try:
                    dst.sendall(data)
                except OSError:
                    return


class _ThreadingProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 128


@pytest.fixture()
def mini_proxy():
    """返回 (proxy_url, seen_list)。断言流量确实经过代理。"""
    _MiniProxyHandler.seen = []
    srv = _ThreadingProxy(("127.0.0.1", 0), _MiniProxyHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", _MiniProxyHandler.seen
    srv.shutdown()
    srv.server_close()
    srv.server_close()
