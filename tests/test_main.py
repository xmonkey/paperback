"""main.py 的过滤逻辑测试：CJK 判断 + generate checkbox 过滤。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from paperback.main import _html_has_cjk, app
from paperback.models import Card


def test_html_has_cjk():
    # 纯英文 → False（保留）
    assert _html_has_cjk("leave") is False
    assert _html_has_cjk("encourage") is False
    assert _html_has_cjk("get a problem with sth.") is False
    # 含中文汉字 → True（过滤）
    assert _html_has_cjk("n. 文本") is True
    assert _html_has_cjk("v. 离开；失踪") is True
    # HTML / style 不影响判断
    assert _html_has_cjk("<style>x</style>leave") is False
    assert _html_has_cjk("<b>n. 文本</b>") is True
    # 中文标点但无汉字 → False（不误伤英文词组误用中文标点）
    assert _html_has_cjk("leave，footprint") is False
    # 空 → False
    assert _html_has_cjk("") is False


def _cards():
    return [
        Card(1, "v. 离开", "leave", "think", "Basic", 1),      # 英文背面 → 保留
        Card(2, "text", "n. 文本", "think", "Basic", 0),        # 中文背面 → 过滤
        Card(3, "________", "encourage", "think", "Basic", 0),  # 英文背面 → 保留
    ]


def test_generate_filter_cjk_checked(tmp_path, monkeypatch):
    """勾选过滤 → 排除背面含中文的卡。"""
    monkeypatch.setenv("PAPERBACK_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    with patch("paperback.main._anki") as m:
        m.return_value.due_card_ids.return_value = [1, 2, 3]
        m.return_value.cards_info.return_value = (_cards(), 0)
        r = client.post(
            "/generate",
            data={"deck": "think", "limit": "10", "filter_cjk": "on"},
            follow_redirects=False,
        )
    assert r.status_code == 303
    sid = r.headers["location"].split("/")[2].split("?")[0]
    sf = list(tmp_path.glob(f"{sid}.json"))
    assert len(json.loads(sf[0].read_text())["cards"]) == 2  # leave + encourage


def test_generate_filter_cjk_unchecked(tmp_path, monkeypatch):
    """不勾选 → 全部保留。"""
    monkeypatch.setenv("PAPERBACK_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    with patch("paperback.main._anki") as m:
        m.return_value.due_card_ids.return_value = [1, 2, 3]
        m.return_value.cards_info.return_value = (_cards(), 0)
        r = client.post(
            "/generate",
            data={"deck": "think", "limit": "10"},  # 不带 filter_cjk
            follow_redirects=False,
        )
    assert r.status_code == 303
    sid = r.headers["location"].split("/")[2].split("?")[0]
    sf = list(tmp_path.glob(f"{sid}.json"))
    assert len(json.loads(sf[0].read_text())["cards"]) == 3  # 全部


def test_generate_filter_cjk_all_filtered(tmp_path, monkeypatch):
    """全被过滤 → no_match 重定向。"""
    monkeypatch.setenv("PAPERBACK_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    all_cjk = [
        Card(1, "x", "n. 文本", "d", "Basic", 0),
        Card(2, "y", "v. 离开", "d", "Basic", 1),
    ]
    with patch("paperback.main._anki") as m:
        m.return_value.due_card_ids.return_value = [1, 2]
        m.return_value.cards_info.return_value = (all_cjk, 0)
        r = client.post(
            "/generate",
            data={"deck": "d", "limit": "10", "filter_cjk": "on"},
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert "error=no_match" in r.headers["location"]
