# -*- coding: utf-8 -*-
"""Http2Finger 参数化模型测试（思路取自 reqrio H2Finger，值来自实测基线）。"""
import json
from pathlib import Path

from stealth_fetch import BrowserProfile, Http2Finger, compare_fingerprints

FIXTURE = Path(__file__).parent.parent / "docs" / "fixtures" / "tls_peet_clean.json"
REAL_AKAMAI = json.loads(FIXTURE.read_text(encoding="utf-8"))["akamai"]


def test_roundtrip_real_akamai():
    f = Http2Finger.from_akamai(REAL_AKAMAI)
    assert f is not None
    assert f.to_akamai() == REAL_AKAMAI           # 往返无损
    assert f.window_update == 15663105
    assert f.pseudo_order == ("m", "a", "s", "p")
    assert f.as_settings_map() == {1: 65536, 2: 0, 4: 6291456, 6: 262144}


def test_parse_failures_return_none_not_guesses():
    assert Http2Finger.from_akamai(None) is None
    assert Http2Finger.from_akamai("") is None
    assert Http2Finger.from_akamai("no-pipes") is None
    assert Http2Finger.from_akamai("a:1;b:2|x|y|z") is None   # 非数字 SETTINGS
    assert Http2Finger.from_akamai("|1|0|m") is None           # 空 SETTINGS


def test_chrome_preset_matches_observed_baseline():
    """预设值 = curl_cffi impersonate=chrome 的实测基线（不是抄文档，是抄观测）。"""
    assert Http2Finger.chrome().to_akamai() == REAL_AKAMAI


def test_parameter_level_diff_locates_exact_setting():
    a = Http2Finger.chrome()
    b = Http2Finger.chrome().replace_settings((4, 999999)) if hasattr(
        Http2Finger.chrome(), "replace_settings") else None
    # dataclass(frozen)：用构造方式改一个参数
    b = Http2Finger(settings=((1, 65536), (2, 0), (4, 999999), (6, 262144)),
                    window_update=15663105, pseudo_order=("m", "a", "s", "p"),
                    extra_segments=("0",))
    d = {c.field: c.status for c in a.diff(b)}
    assert d["h2.setting[4]"] == "mismatch"       # 精确定位到 INITIAL_WINDOW_SIZE
    assert d["h2.setting[1]"] == "match"
    assert d["h2.window_update"] == "match"


def test_missing_setting_detected():
    a = Http2Finger.chrome()
    b = Http2Finger(settings=((1, 65536), (2, 0), (4, 6291456)),   # 少了 6
                    window_update=15663105, pseudo_order=("m", "a", "s", "p"))
    d = {c.field: c.status for c in a.diff(b)}
    assert d["h2.setting[6]"] == "missing"        # 缺项绝不判 match（审计 P1）


def test_compare_fingerprints_expands_akamai_detail():
    baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))
    observed = dict(baseline)
    observed["akamai"] = REAL_AKAMAI.replace("6291456", "6291455")
    report = compare_fingerprints(observed, baseline)
    by = {f.field: f.status for f in report.fields}
    assert report.overall == "mismatch"
    assert by["h2.setting[4]"] == "mismatch"      # 展开到参数级
    assert by["akamai"] == "mismatch"
    assert by["ja3"] == "match"


def test_browser_profile_carries_three_layer_declaration():
    p = BrowserProfile.chrome()
    assert p.h2_finger is not None and p.h2_finger.to_akamai() == REAL_AKAMAI
    s = p.summary()
    assert s["declared_layers"] == {"tls": False, "h2": True}   # ja3/ja4 未声明 → False
