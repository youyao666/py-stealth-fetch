# -*- coding: utf-8 -*-
"""指纹诊断（审计第四节概念修正的落地）。

- 对比结果四态：match / mismatch / missing / unavailable。
  missing=一侧缺字段；unavailable=字段存在但值为空/None——**任一侧缺值绝不判 match**（审计 P1）。
- schema 适配器只用 2026-10-02 实测的 tls.peet.ws /api/clean 扁平结构
  （ja3/ja4/akamai/peetprint 在顶层；不存在 tls.http2 这类嵌套路径），见
  docs/fixtures/tls_peet_clean.json；schema 变化时报告 unavailable 而非报匹配。
- 解析只用 json.loads；禁止 eval（审计 P0）。
- JA3/JA4 摘要相同 ≠ 浏览器实现逐字节一致：报告如实标注这一点。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .exceptions import FingerprintParseError

# 实测 schema（tls.peet.ws /api/clean，2026-10-02）：全部为顶层字符串字段
TLS_PEET_FIELDS = {
    "ja3": "TLS ClientHello 摘要(JA3)",
    "ja4": "TLS ClientHello 摘要(JA4)",
    "akamai": "HTTP/2 SETTINGS/优先级指纹",
    "peetprint": "TLS+HTTP/2 组合指纹",
    "http_version": "协商出的协议版本",
}


@dataclass
class FieldComparison:
    field: str
    status: str          # match / mismatch / missing / unavailable
    observed: str | None = None
    baseline: str | None = None
    note: str = ""


@dataclass
class FingerprintReport:
    fields: list[FieldComparison] = field(default_factory=list)
    environment: dict = field(default_factory=dict)   # 引擎/profile/平台/时间/基线来源
    overall: str = "unknown"                          # match/mismatch 仅在可比字段全部一致/存在差异时给出

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for f in self.fields:
            counts[f.status] = counts.get(f.status, 0) + 1
        return {"overall": self.overall, "counts": counts,
                "fields": [{"field": f.field, "status": f.status} for f in self.fields]}


def parse_observation(raw: str | bytes, *, expect_schema: str = "tls_peet_clean") -> dict:
    """解析观察服务响应。非 JSON / 非对象 / 关键字段全缺 → FingerprintParseError。"""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise FingerprintParseError(f"指纹响应不是合法 JSON: {e}") from e
    if not isinstance(data, dict):
        raise FingerprintParseError(f"指纹响应应为 JSON 对象，得到 {type(data).__name__}")
    known = set(TLS_PEET_FIELDS) if expect_schema == "tls_peet_clean" else set()
    if known and not (set(data) & known):
        raise FingerprintParseError(
            f"响应不含已知 schema({expect_schema}) 的任何字段；可能 schema 已变化: {sorted(data)[:8]}")
    return data


def _value(data: dict, key: str):
    v = data.get(key)
    if v is None:
        return None, "missing"          # 字段缺失
    if not isinstance(v, str) or not v.strip():
        return v, "unavailable"         # 字段在但为空——绝不因两侧同空判 match
    return v.strip(), "ok"


def compare_fingerprints(observed: dict, baseline: dict, *,
                         fields: dict | None = None,
                         environment: dict | None = None) -> FingerprintReport:
    spec = fields or TLS_PEET_FIELDS
    comps: list[FieldComparison] = []
    for key, label in spec.items():
        ov, os_ = _value(observed, key)
        bv, bs = _value(baseline, key)
        if os_ != "ok" or bs != "ok":
            status = os_ if os_ != "ok" else bs
            note = {"missing": "字段缺失", "unavailable": "字段存在但值为空"}[status]
            comps.append(FieldComparison(key, status, ov, bv, note))
        else:
            comps.append(FieldComparison(
                key, "match" if ov == bv else "mismatch", ov, bv,
                note="摘要相等仅表示该层特征一致，不等于浏览器实现逐字节一致" if key in ("ja3", "ja4") else ""))
    comparable = [c for c in comps if c.status in ("match", "mismatch")]
    overall = "unknown"
    if comparable:
        overall = "match" if all(c.status == "match" for c in comparable) else "mismatch"
    return FingerprintReport(fields=comps, environment=environment or {}, overall=overall)


def load_baseline_from_fixture(path: str) -> dict:
    """读取本地基线 fixture（默认 CI/测试路径；外部观察仅显式调用时访问）。"""
    with open(path, encoding="utf-8") as f:
        return parse_observation(f.read())


async def collect_observation(url: str = "https://tls.peet.ws/api/clean",
                              engine_profile: str = "chrome") -> dict:
    """显式诊断入口：真实请求一次观察服务（测试不默认调用）。"""
    from .engines.curl_cffi_engine import CurlCffiEngine
    from .models import Request

    async with CurlCffiEngine(engine_profile=engine_profile) as engine:
        resp = await engine.request(Request(method="GET", url=url, timeout=20.0))
        if "json" not in (resp.headers.get("content-type") or ""):
            raise FingerprintParseError(
                f"观察服务返回非 JSON Content-Type: {resp.headers.get('content-type')!r}")
        return parse_observation(resp.content)


if __name__ == "__main__":  # 显式诊断 CLI：python -m stealth_fetch.fingerprint [基线fixture]
    import asyncio
    import sys as _sys

    async def _cli():
        observed = await collect_observation()
        if len(_sys.argv) > 1:
            baseline = load_baseline_from_fixture(_sys.argv[1])
        else:
            from pathlib import Path as _P
            default = _P(__file__).resolve().parents[2] / "docs" / "fixtures" / "tls_peet_clean.json"
            baseline = load_baseline_from_fixture(str(default)) if default.exists() else dict(observed)
        import time as _t
        report = compare_fingerprints(observed, baseline, environment={
            "engine": "curl_cffi", "profile": "chrome",
            "collected_at": _t.strftime("%Y-%m-%d %H:%M:%S"),
            "baseline_source": _sys.argv[1] if len(_sys.argv) > 1 else "当前实测(自比较)"})
        print("overall:", report.overall)
        for f in report.fields:
            print(f"  {f.field:14} {f.status:10} {f.note}")

    asyncio.run(_cli())
