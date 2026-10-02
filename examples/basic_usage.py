# -*- coding: utf-8 -*-
"""stealth_fetch 用法示例：本地请求 + 指纹诊断。

运行：.venv/bin/python examples/basic_usage.py [本地URL]
不指定 URL 时仅演示离线部分（不联网）。
"""
import asyncio
import sys

from stealth_fetch import AsyncClient, BrowserProfile, RetryPolicy


async def demo(url: str | None):
    profile = BrowserProfile.chrome()
    policy = RetryPolicy(max_attempts=2, total_budget_s=20.0)
    async with AsyncClient(profile=profile, policy=policy) as client:
        print("profile:", profile.summary())
        if url:
            r = await client.get(url, timeout=10.0)
            print("status:", r.status_code, "| 分类:", r.classification.kind, r.classification.rule)
            print("重复头 set-cookie:", r.headers.get_all("set-cookie"))
            print("尝试记录:", [(a.engine, a.outcome) for a in r.attempts])
        else:
            print("(未给 URL，跳过在线请求；离线部分完成)")


if __name__ == "__main__":
    asyncio.run(demo(sys.argv[1] if len(sys.argv) > 1 else None))
