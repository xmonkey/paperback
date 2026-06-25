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
