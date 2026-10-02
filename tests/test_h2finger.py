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


# ---------- JA3 稳健对比（2026-10-02/03 两天完整实测串：集合一致，仅顺序洗牌） ----------

YESTERDAY_JA3 = ("771,4865-4866-4867-49195-49199-49196-49200-52393-52392-49171-49172-156-157-47-53,"
                 "17613-45-10-11-23-51-16-0-65281-13-5-43-35-27-65037-18,4588-29-23-24,0")
TODAY_JA3 = ("771,4865-4866-4867-49195-49199-49196-49200-52393-52392-49171-49172-156-157-47-53,"
             "65037-16-11-45-65281-18-10-43-5-51-17613-35-0-13-23-27,4588-29-23-24,0")


def test_grease_ids_and_real_extensions():
    from stealth_fetch.fingerprint import GREASE_IDS
    assert 0x0A0A in GREASE_IDS and 0xFAFA in GREASE_IDS and len(GREASE_IDS) == 16
    # 实测纠偏：两天漂移的 17613/65037 不是 GREASE，是真实扩展（ALPS 旧版 / ECH）
    assert 17613 not in GREASE_IDS and 65037 not in GREASE_IDS


def test_real_two_day_ja3_is_match_after_normalization():
    """真实两日完整串：集合一致仅顺序洗牌 → 归一化+顺序不敏感后必须 match。"""
    from stealth_fetch.fingerprint import compare_fingerprints
    r = compare_fingerprints({"ja3": TODAY_JA3}, {"ja3": YESTERDAY_JA3})
    f = next(x for x in r.fields if x.field == "ja3")
    assert f.status == "match", f.note
    assert "顺序不敏感" in f.note


def test_synthetic_set_difference_reports_evidence():
    """合成的真实集合差异：如实 mismatch 并把差异 ID 列进证据。"""
    from stealth_fetch.fingerprint import compare_fingerprints
    changed = TODAY_JA3.replace("-17613", "").replace("65037-", "", 1)  # 去掉两个真实扩展（65037 在段首）
    r = compare_fingerprints({"ja3": changed}, {"ja3": TODAY_JA3})
    f = next(x for x in r.fields if x.field == "ja3")
    assert f.status == "mismatch"
    assert "17613" in f.note and "65037" in f.note


def test_ja3_order_shuffle_alone_is_match():
    """仅扩展顺序洗牌（集合一致）不误报。"""
    from stealth_fetch.fingerprint import compare_fingerprints
    shuffled = ",".join([TODAY_JA3.split(",")[0], TODAY_JA3.split(",")[1],
                         "-".join(reversed(TODAY_JA3.split(",")[2].split("-")))])
    r = compare_fingerprints({"ja3": shuffled}, {"ja3": TODAY_JA3})
    assert next(x for x in r.fields if x.field == "ja3").status == "match"


def test_real_cipher_change_still_mismatched():
    """真实差异（密码套件变了）仍要报 mismatch。"""
    from stealth_fetch.fingerprint import compare_fingerprints
    changed = TODAY_JA3.replace("4865-4866-4867", "4865-4866")
    r = compare_fingerprints({"ja3": changed}, {"ja3": TODAY_JA3})
    assert next(f for f in r.fields if f.field == "ja3").status == "mismatch"
