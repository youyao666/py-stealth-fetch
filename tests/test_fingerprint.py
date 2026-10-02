# -*- coding: utf-8 -*-
"""指纹诊断测试：真实 schema fixture、四态对比、无 eval、解析错误。"""
import json
from pathlib import Path

import pytest

from stealth_fetch import FingerprintParseError, compare_fingerprints, parse_observation
from stealth_fetch.fingerprint import TLS_PEET_FIELDS

FIXTURE = Path(__file__).parent.parent / "docs" / "fixtures" / "tls_peet_clean.json"


def test_real_fixture_parses_and_matches_itself():
    baseline = parse_observation(FIXTURE.read_text(encoding="utf-8"))
    report = compare_fingerprints(baseline, dict(baseline),
                                  environment={"baseline": "tls_peet_clean.json"})
    assert report.overall == "match"
    assert all(f.status == "match" for f in report.fields)
    # 真实 schema 关键字段都被覆盖（ja3/ja4/akemai/peetprint 顶层）
    assert set(TLS_PEET_FIELDS) <= {f.field for f in report.fields}


def test_mismatch_detected():
    a = {"ja3": "x", "ja4": "y", "akamai": "z", "peetprint": "p", "http_version": "h2"}
    b = {**a, "ja4": "different"}
    report = compare_fingerprints(a, b)
    assert report.overall == "mismatch"
    by = {f.field: f.status for f in report.fields}
    assert by["ja4"] == "mismatch" and by["ja3"] == "match"


def test_missing_field_is_missing_not_match():
    a = {"ja3": "x", "ja4": "y", "akamai": "z", "peetprint": "p", "http_version": "h2"}
    b = {k: v for k, v in a.items() if k != "akamai"}   # 基线缺 akamai
    report = compare_fingerprints(a, b)
    by = {f.field: f.status for f in report.fields}
    assert by["akamai"] == "missing"                    # 审计 P1：缺字段绝不判 match


def test_none_equals_none_is_unavailable_not_match():
    a = {"ja3": None, "ja4": "y"}
    b = {"ja3": None, "ja4": "y"}
    report = compare_fingerprints(a, b)
    by = {f.field: f.status for f in report.fields}
    assert by["ja3"] == "missing"                       # None 值按缺失/不可用处理
    empty = {"ja3": "", "ja4": "y"}
    report2 = compare_fingerprints(empty, dict(empty))
    assert {f.field: f.status for f in report2.fields}["ja3"] == "unavailable"


def test_parse_rejects_non_json_and_wrong_schema():
    with pytest.raises(FingerprintParseError):
        parse_observation("not json at all")
    with pytest.raises(FingerprintParseError):
        parse_observation("[1,2,3]")                    # 非对象
    with pytest.raises(FingerprintParseError):
        parse_observation(json.dumps({"unrelated": "schema"}))   # schema 漂移被识别


def test_no_eval_in_fingerprint_module():
    # 审计 P0：解析路径绝不使用 eval/exec（标准 JSON 解析器处理 true/null 等字面量）
    import stealth_fetch.fingerprint as fp
    src = Path(fp.__file__).read_text("utf-8")
    assert "eval(" not in src and "exec(" not in src
    parse_observation('{"ja3": "ok", "flag": true}')     # 标准 JSON 字面量正常解析，不报错
