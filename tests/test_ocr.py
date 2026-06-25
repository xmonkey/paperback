"""ocr.py 单测：纯函数（plain_text/build_answers/parse_json/preprocess/cleanup）
+ mock requests 的 call_llm 重试与错误分类。

不依赖真实 LLM API；preprocess_image 用合成图（不依赖 test1.jpg 测试素材）。
"""

from __future__ import annotations

import io
import json
import os
import time
from unittest.mock import patch

import pytest
import requests
from PIL import Image

from paperback import ocr
from paperback.models import Card


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """每个测试隔离配置文件（tmp 下无 config.json，确保 get_config fallback env）。"""
    monkeypatch.setenv("PAPERBACK_DATA_DIR", str(tmp_path / "sessions"))


# ---------- 辅助 ----------


class _Resp:
    """模拟 requests.Response。"""

    def __init__(self, status=200, text="", json_data=None):
        self.status_code = status
        self.text = text
        self._json = json_data or {}

    def json(self):
        return self._json


# ---------- 纯函数 ----------


def test_plain_text():
    assert ocr.plain_text("<style>x</style><b>hi</b>") == "hi"
    assert ocr.plain_text("<p>a <span>b</span> c</p>") == "a b c"
    assert ocr.plain_text("") == ""


def test_build_answers_index_from_1_and_truncate():
    cards = [
        Card(10, "q1", "<b>answer1</b>", "d", "Basic", 0),
        Card(20, "q2", "x" * 100, "d", "Basic", 0),
    ]
    a = ocr.build_answers(cards)
    # index 从 1 开始 = worksheet 的 #NNN
    assert a == {1: "answer1", 2: "x" * 80}  # 截断到 80


def test_parse_json_ok():
    assert ocr.parse_json('{"a":1}') == {"a": 1}


def test_parse_json_extract_from_noise():
    # LLM 偶尔在 JSON 前后带文字，提取首个 {...}
    assert ocr.parse_json('noise {"a":1} trailing') == {"a": 1}


def test_parse_json_nested_object():
    # 嵌套 object：非贪婪正则会截到内层 }，find/rfind 取最外层一对更稳
    assert ocr.parse_json('xx {"cards":[{"x":1}]} yy') == {"cards": [{"x": 1}]}


def test_parse_json_invalid_raises():
    with pytest.raises(json.JSONDecodeError):
        ocr.parse_json("no braces here")


# ---------- 图像预处理 ----------


def test_preprocess_image_resize_long_edge():
    img = Image.new("RGB", (4000, 3000), "white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    out = ocr.preprocess_image(buf.getvalue(), max_px=2000)
    out_img = Image.open(io.BytesIO(out))
    assert max(out_img.size) <= 2000
    assert out_img.size == (2000, 1500)  # 等比缩放


def test_preprocess_image_no_upscale():
    # 小图不放大
    img = Image.new("RGB", (500, 400), "white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    out = ocr.preprocess_image(buf.getvalue(), max_px=2000)
    out_img = Image.open(io.BytesIO(out))
    assert out_img.size == (500, 400)


# ---------- call_llm（mock requests） ----------


def test_call_llm_not_configured(monkeypatch):
    monkeypatch.delenv("PAPERBACK_OCR_API_KEY", raising=False)
    with pytest.raises(ocr.OcrError) as ex:
        ocr.call_llm("b64", {1: "ans"})
    assert ex.value.reason == "not_configured"


def test_call_llm_success(monkeypatch):
    monkeypatch.setenv("PAPERBACK_OCR_API_KEY", "k")
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)
    resp = _Resp(
        200,
        json_data={
            "choices": [
                {"message": {"content": '{"cards":[{"index":1,"suggested_ease":3}]}'}}
            ]
        },
    )
    with patch("paperback.ocr.requests.post", return_value=resp) as mock_post:
        result = ocr.call_llm("b64", {1: "answer"})
    assert result["cards"][0]["suggested_ease"] == 3
    # 校验请求结构
    args, kwargs = mock_post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer k"
    assert kwargs["json"]["response_format"] == {"type": "json_object"}
    assert kwargs["json"]["model"]  # 有 model 名


def test_call_llm_retry_on_network_then_ok(monkeypatch):
    monkeypatch.setenv("PAPERBACK_OCR_API_KEY", "k")
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)
    ok = _Resp(200, json_data={"choices": [{"message": {"content": '{"cards":[]}'}}]})
    with patch(
        "paperback.ocr.requests.post",
        side_effect=[requests.ConnectionError("boom"), ok],
    ):
        result = ocr.call_llm("b64", {})
    assert result == {"cards": []}


def test_call_llm_network_exhaust_retries(monkeypatch):
    monkeypatch.setenv("PAPERBACK_OCR_API_KEY", "k")
    sleeps = []
    monkeypatch.setattr(ocr.time, "sleep", lambda s: sleeps.append(s))
    # 3 次全失败（1 初试 + 2 重试）
    with patch(
        "paperback.ocr.requests.post",
        side_effect=requests.ConnectionError("boom"),
    ):
        with pytest.raises(ocr.OcrError) as ex:
            ocr.call_llm("b64", {})
    assert ex.value.reason == "network"
    assert sleeps == [1.0, 3.0]  # 重试了两次


def test_call_llm_4xx_no_retry(monkeypatch):
    monkeypatch.setenv("PAPERBACK_OCR_API_KEY", "k")
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)
    calls = []

    def fake_post(*a, **kw):
        calls.append(1)
        return _Resp(401, text="unauthorized")

    with patch("paperback.ocr.requests.post", side_effect=fake_post):
        with pytest.raises(ocr.OcrError) as ex:
            ocr.call_llm("b64", {})
    assert ex.value.reason == "http_error"
    assert len(calls) == 1  # 4xx 不重试


def test_call_llm_5xx_retries(monkeypatch):
    monkeypatch.setenv("PAPERBACK_OCR_API_KEY", "k")
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)
    with patch(
        "paperback.ocr.requests.post",
        side_effect=[
            _Resp(503, text="busy"),
            _Resp(200, json_data={"choices": [{"message": {"content": '{"cards":[]}'}}]}),
        ],
    ):
        result = ocr.call_llm("b64", {})
    assert result == {"cards": []}


def test_call_llm_parse_error(monkeypatch):
    monkeypatch.setenv("PAPERBACK_OCR_API_KEY", "k")
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)
    resp = _Resp(200, json_data={"choices": [{"message": {"content": "not json"}}]})
    with patch("paperback.ocr.requests.post", return_value=resp):
        with pytest.raises(ocr.OcrError) as ex:
            ocr.call_llm("b64", {})
    assert ex.value.reason == "parse_error"


# ---------- 留存清理 ----------


def test_cleanup_old_images(tmp_path):
    ocr_dir = tmp_path / "s1" / "ocr"
    ocr_dir.mkdir(parents=True)
    old = ocr_dir / "old.jpg"
    old.write_text("x")
    new = ocr_dir / "new.jpg"
    new.write_text("x")
    long_ago = time.time() - 400 * 86400  # 400 天前
    os.utime(old, (long_ago, long_ago))

    removed = ocr.cleanup_old_images(tmp_path, retain_days=365)

    assert removed == 1
    assert not old.exists()
    assert new.exists()


def test_cleanup_skips_nonexistent_dir(tmp_path):
    # 无 ocr 目录时不崩
    assert ocr.cleanup_old_images(tmp_path, retain_days=365) == 0


# ---------- 配置存储 ----------


def test_save_and_get_config():
    ocr.save_config("secret-key", "http://x/v1", "m1")
    cfg = ocr.get_config()
    assert cfg == {"api_key": "secret-key", "base_url": "http://x/v1", "model": "m1"}


def test_get_config_stored_over_env(monkeypatch):
    # 存储优先于 env
    monkeypatch.setenv("PAPERBACK_OCR_API_KEY", "envkey")
    ocr.save_config("storedkey", "http://stored/v1", "storedmodel")
    cfg = ocr.get_config()
    assert cfg["api_key"] == "storedkey"
    assert cfg["base_url"] == "http://stored/v1"
    assert cfg["model"] == "storedmodel"


def test_get_config_fallback_env(monkeypatch):
    # 无存储时 fallback env；env 也无则用默认
    monkeypatch.setenv("PAPERBACK_OCR_API_KEY", "envkey")
    monkeypatch.setenv("PAPERBACK_OCR_MODEL", "envmodel")
    cfg = ocr.get_config()
    assert cfg["api_key"] == "envkey"
    assert cfg["model"] == "envmodel"
    assert cfg["base_url"] == ocr.DEFAULT_BASE_URL  # env 无 → 默认


def test_save_config_empty_key_preserves_original():
    ocr.save_config("orig", "http://a/v1", "m")
    ocr.save_config(None, "http://b/v1", "m2")  # key=None 保留原
    cfg = ocr.get_config()
    assert cfg["api_key"] == "orig"
    assert cfg["base_url"] == "http://b/v1"  # 其他字段更新
    assert cfg["model"] == "m2"


def test_config_public_has_no_key_plaintext():
    ocr.save_config("verysecret", "http://x/v1", "m")
    c = ocr.config()
    assert "verysecret" not in str(c)  # key 明文不外泄
    assert c["has_key"] is True
    assert c["configured"] == "true"
    assert c["base_url"] == "http://x/v1"


def test_config_file_not_found_falls_back(monkeypatch):
    # config.json 不存在 → _read_stored 返回 {}，不崩
    monkeypatch.delenv("PAPERBACK_OCR_API_KEY", raising=False)
    assert ocr.is_configured() is False
    assert ocr.get_config()["base_url"] == ocr.DEFAULT_BASE_URL


def test_save_config_preserves_other_top_level_keys():
    # config.json 含其他顶层键时，写 ocr 段不破坏它们
    path = ocr._config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"other": {"x": 1}}', encoding="utf-8")
    ocr.save_config("k", "http://x/v1", "m")
    import json as _json

    data = _json.loads(path.read_text(encoding="utf-8"))
    assert data["other"] == {"x": 1}  # 其他键保留
    assert data["ocr"]["api_key"] == "k"


# ---------- 测试连接（mock requests） ----------


def test_test_connection_success(monkeypatch):
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)
    with patch("paperback.ocr.requests.post", return_value=_Resp(200)):
        ok, detail = ocr.test_connection("k", "http://x/v1", "m")
    assert ok is True


def test_test_connection_http_fail(monkeypatch):
    with patch(
        "paperback.ocr.requests.post",
        return_value=_Resp(401, text="invalid api key"),
    ):
        ok, detail = ocr.test_connection("k", "http://x/v1", "m")
    assert ok is False
    assert "401" in detail


def test_test_connection_network_fail(monkeypatch):
    with patch(
        "paperback.ocr.requests.post",
        side_effect=requests.ConnectionError("timeout"),
    ):
        ok, detail = ocr.test_connection("k", "http://x/v1", "m")
    assert ok is False
    assert "network" in detail
