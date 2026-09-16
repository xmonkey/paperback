"""AnkiConnect 客户端解析逻辑测试（mock invoke）。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from paperback.anki_connect import (
    AnkiConnect,
    AnkiConnectError,
    _cloze_to_answer,
    _cloze_to_blank,
)


def _anki():
    return AnkiConnect()


def test_cards_info_basic():
    a = _anki()
    fake = [
        {
            "cardId": 100,
            "modelName": "Basic",
            "deckName": "D",
            "question": "<style>.card{}</style><b>Q</b>",
            "answer": "<style>.card{}</style><b>Q</b><hr id=answer>A",
        }
    ]
    with patch.object(a, "invoke", return_value=fake):
        cards, skipped = a.cards_info([100])
    assert skipped == 0
    assert len(cards) == 1
    c = cards[0]
    assert c.card_id == 100
    assert c.front == "<b>Q</b>"  # 剥离 style
    assert c.back == "A"  # hr 之后
    assert c.note_type == "Basic"
    assert c.deck == "D"


def test_cards_info_cloze_no_answer_leak():
    a = _anki()
    fake = [
        {
            "cardId": 1,
            "modelName": "Cloze",
            "deckName": "D",
            "fields": {
                "Text": {"value": "首都は{{c1::東京}}です", "order": 0},
                "Extra": {"value": "日本の都市", "order": 1},
            },
        }
    ]
    with patch.object(a, "invoke", return_value=fake):
        cards, _ = a.cards_info([1])
    c = cards[0]
    assert "cloze-blank" in c.front  # 正面挖空
    assert "東京" not in c.front  # 答案不泄露
    assert "cloze-answer" in c.back
    assert "東京" in c.back
    assert "日本の都市" in c.back  # Extra 追加


def test_cards_info_cloze_hint():
    a = _anki()
    fake = [
        {
            "cardId": 1,
            "modelName": "Cloze",
            "deckName": "D",
            "fields": {"Text": {"value": "{{c1::東京::首都}}", "order": 0}},
        }
    ]
    with patch.object(a, "invoke", return_value=fake):
        cards, _ = a.cards_info([1])
    assert "(首都)" in cards[0].front  # 提示显示
    assert "cloze-hint" in cards[0].front


def test_supports_arbitrary_note_types_and_skips_empty():
    """任意 note type 都支持（用 question/answer）；question 为空才跳过。"""
    a = _anki()
    fake = [
        {  # 自定义类型 → 支持
            "cardId": 1,
            "modelName": "Image Occlusion Enhanced",
            "deckName": "D",
            "question": "<style>x</style><img src='q.png'>",
            "answer": "<style>x</style><img src='q.png'><hr id=answer><img src='a.png'>",
        },
        {  # Basic reversed 反向卡 → question 是释义，正反向正确
            "cardId": 2,
            "modelName": "Basic (and reversed card)",
            "deckName": "D",
            "question": "<style>x</style>v. 离开",
            "answer": "<style>x</style>v. 离开<hr id=answer>leave",
        },
        {  # question 为空 → 跳过
            "cardId": 3,
            "modelName": "Basic",
            "deckName": "D",
            "question": "   ",
            "answer": "",
        },
    ]
    with patch.object(a, "invoke", return_value=fake):
        cards, skipped = a.cards_info([1, 2, 3])
    assert [c.card_id for c in cards] == [1, 2]
    assert skipped == 1
    # 反向卡正反面正确（Anki 已渲染）
    assert cards[1].front == "v. 离开"
    assert cards[1].back == "leave"


def test_due_card_ids_query_and_limit():
    a = _anki()
    with patch.object(a, "invoke", return_value=[10, 20, 30, 40]) as m:
        ids = a.due_card_ids("My Deck", limit=2)
    assert ids == [10, 20]
    _, kwargs = m.call_args
    assert 'deck:"My Deck"' in kwargs["query"]
    assert "is:due" in kwargs["query"]


def test_due_card_ids_include_new():
    """include_new=True 时 query 加 (is:due or is:new)，拉全部未学新卡。"""
    a = _anki()
    with patch.object(a, "invoke", return_value=[1, 2]) as m:
        a.due_card_ids("My Deck", include_new=True)
    _, kwargs = m.call_args
    assert "(is:due or is:new)" in kwargs["query"]
    # 默认不带 is:new
    with patch.object(a, "invoke", return_value=[1]) as m:
        a.due_card_ids("My Deck")
    _, kwargs = m.call_args
    assert "is:new" not in kwargs["query"]


def test_deck_name_with_quote_escaped():
    # 含引号的 deck 名不应破坏 query 语法（第一版用双引号包裹，已满足基本场景）
    a = _anki()
    with patch.object(a, "invoke", return_value=[]) as m:
        a.due_card_ids("日语::N3", limit=5)
    _, kwargs = m.call_args
    assert 'is:due' in kwargs["query"]


def test_answer_cards_empty():
    a = _anki()
    with patch.object(a, "invoke", return_value=None) as m:
        assert a.answer_cards({}) == []
    m.assert_not_called()


def test_answer_cards_maps_order():
    a = _anki()
    gradings = {111: 3, 222: 1}
    with patch.object(a, "invoke", return_value=[True, False]) as m:
        result = a.answer_cards(gradings)
    assert result == [True, False]
    _, kwargs = m.call_args
    assert kwargs["answers"] == [{"cardId": 111, "ease": 3}, {"cardId": 222, "ease": 1}]


def test_invoke_raises_on_error_field():
    a = _anki()
    with patch("paperback.anki_connect.requests.post") as p:
        p.return_value.status_code = 200
        p.return_value.json.return_value = {"error": "boom", "result": None}
        with pytest.raises(AnkiConnectError):
            a.invoke("deckNames")


def test_invoke_raises_on_non_json(monkeypatch):
    a = _anki()
    with patch("paperback.anki_connect.requests.post") as p:
        p.return_value.status_code = 200
        p.return_value.json.side_effect = ValueError("no json")
        with pytest.raises(AnkiConnectError):
            a.invoke("deckNames")


def test_cloze_helpers():
    assert "<span class=\"cloze-blank\"></span>" in _cloze_to_blank("{{c1::x}}")
    assert "<span class=\"cloze-answer\">x</span>" in _cloze_to_answer("{{c1::x}}")
    # 多空全挖
    assert _cloze_to_blank("{{c1::a}} {{c2::b}}").count("cloze-blank") == 2


# ---------- 图片内嵌（<img src> → base64 data URI） ----------


def test_inline_images_replaces_local_media():
    a = _anki()
    a.invoke = lambda action, **kw: "QkFTRTY0" if action == "retrieveMediaFile" else None
    out = a._inline_images('<img src="test.png">')
    assert out == '<img src="data:image/png;base64,QkFTRTY0">'


def test_inline_images_keeps_remote_url():
    a = _anki()
    called = []
    a.invoke = lambda action, **kw: called.append(action) or "x"
    out = a._inline_images('<img src="https://e.com/x.png">')
    assert out == '<img src="https://e.com/x.png">'
    assert "retrieveMediaFile" not in called  # 远程图不调 AnkiConnect


def test_inline_images_missing_file_keeps_src():
    a = _anki()
    a.invoke = lambda action, **kw: False  # AnkiConnect 返回 false=不存在
    out = a._inline_images('<img src="nope.png">')
    assert out == '<img src="nope.png">'


def test_inline_images_data_uri_untouched():
    a = _anki()

    def raise_(*args, **kw):
        raise AssertionError("invoke 不该被调")

    a.invoke = raise_
    out = a._inline_images('<img src="data:image/png;base64,xxx">')
    assert out == '<img src="data:image/png;base64,xxx">'


def test_inline_images_mime_from_ext():
    a = _anki()
    a.invoke = lambda action, **kw: "Yg=="
    assert "image/svg+xml" in a._inline_images('<img src="x.svg">')
    assert "image/jpeg" in a._inline_images('<img src="x.jpg">')


# ---------- 音频占位符移除（默写是纸笔场景，播不了） ----------


def test_inline_images_strips_play_placeholder():
    a = _anki()
    a.invoke = lambda *args, **kw: None
    assert a._inline_images("hello [anki:play:a:0]") == "hello "
    assert a._inline_images("[anki:play:a:1] world") == " world"


def test_inline_images_strips_sound_tag():
    a = _anki()
    a.invoke = lambda *args, **kw: None
    assert a._inline_images("foo [sound:bar.mp3]") == "foo "
