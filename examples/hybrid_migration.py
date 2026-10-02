# -*- coding: utf-8 -*-
"""hybrid 示例：浏览器快照迁入 → 一次显式验证（本地服务，全程离线）。

真实场景中快照来自你的浏览器自动化（本包不含浏览器集成——快照生产者在包外，
按 BrowserSessionSnapshot 契约导出 JSON 即可）。
"""
import asyncio
import http.server
import json
import threading

from stealth_fetch import (AsyncClient, BrowserSessionSnapshot, RetryPolicy,
                           SessionMigrator, SnapshotCookie)
from stealth_fetch.engines.curl_cffi_engine import CurlCffiEngine


def start_local_site():
    """模拟目标站点：看到会话 Cookie 才返回 'welcome'。"""
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            ok = "sid=migrated-session" in self.headers.get("Cookie", "")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"page": "welcome" if ok else "login"}).encode())
        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/"


async def main():
    base = start_local_site()

    # 1) 浏览器快照（示例：正常应由浏览器自动化导出）
    snapshot = BrowserSessionSnapshot(
        cookies=(SnapshotCookie(name="sid", value="migrated-session", domain="127.0.0.1",
                                path="/", secure=False, http_only=True, same_site="Lax"),),
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        browser_family="chrome", browser_version="124",
        proxy_descriptor=None, source="demo")

    # 2) 迁移 + 验证：成功与否只由谓词决定（页面内容判定，不是状态码）
    async with AsyncClient(engines=[CurlCffiEngine()],
                           policy=RetryPolicy(max_attempts=1)) as client:
        migrator = SessionMigrator(client)
        r = await migrator.migrate(
            snapshot, base,
            success_predicate=lambda resp: resp.json().get("page") == "welcome")
        print(json.dumps(r.summary(), ensure_ascii=False, indent=2))
        assert r.status == "verified", "迁移验证失败：查看 limitations/evidence"


if __name__ == "__main__":
    asyncio.run(main())
