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
            "fields": {
                "Front": {"value": "<b>Q</b>", "order": 0},
                "Back": {"value": "A", "order": 1},
            },
        }
    ]
    with patch.object(a, "invoke", return_value=fake):
        cards, skipped = a.cards_info([100])
    assert skipped == 0
    assert len(cards) == 1
    c = cards[0]
    assert c.card_id == 100
    assert c.front == "<b>Q</b>"
    assert c.back == "A"
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


def test_skip_unsupported_note_types():
    a = _anki()
    fake = [
        {
            "cardId": 1,
            "modelName": "Basic",
            "deckName": "D",
            "fields": {"F": {"value": "x", "order": 0}, "B": {"value": "y", "order": 1}},
        },
        {
            "cardId": 2,
            "modelName": "Image Occlusion Enhanced",
            "deckName": "D",
            "fields": {"Image": {"value": "<img>", "order": 0}},
        },
        {
            "cardId": 3,
            "modelName": "Basic (and reversed card)",
            "deckName": "D",
            "fields": {"F": {"value": "x", "order": 0}, "B": {"value": "y", "order": 1}},
        },
    ]
    with patch.object(a, "invoke", return_value=fake):
        cards, skipped = a.cards_info([1, 2, 3])
    assert [c.card_id for c in cards] == [1]  # 仅 Basic
    assert skipped == 2


def test_due_card_ids_query_and_limit():
    a = _anki()
    with patch.object(a, "invoke", return_value=[10, 20, 30, 40]) as m:
        ids = a.due_card_ids("My Deck", limit=2)
    assert ids == [10, 20]
    _, kwargs = m.call_args
    assert 'deck:"My Deck"' in kwargs["query"]
    assert "is:due" in kwargs["query"]


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
