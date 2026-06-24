"""AnkiConnect HTTP 客户端封装。

所有 Anki 交互的唯一出口。通过 POST http://localhost:8765 调用 AnkiConnect 插件。
"""

from __future__ import annotations

import os
import re
from typing import Any

import requests

from .models import Card

DEFAULT_URL = "http://localhost:8765"
ANKI_CONNECT_VERSION = 6

# 第一版支持的 note type 白名单。
# 注意：Basic (and reversed card) 暂不支持 —— cardsInfo 不返回模板序号，
# 无法可靠判断正/反向，强行解析可能导致答案位置错乱。
SUPPORTED_NOTE_TYPES: set[str] = {"Basic", "Cloze"}

# Cloze 挖空：{{cN::答案}} 或 {{cN::答案::提示}}。用 .*? 非贪婪，DOTALL 兼容多行。
_CLOZE_RE = re.compile(r"\{\{c(\d+)::(.*?)(?:::(.*?))?\}\}", re.DOTALL)


class AnkiConnectError(Exception):
    """AnkiConnect 调用失败（连接/HTTP/返回 error）。"""


class AnkiConnect:
    def __init__(self, url: str | None = None, timeout: float = 10.0):
        self.url = url or os.environ.get("PAPERBACK_ANKI_URL", DEFAULT_URL)
        self.timeout = timeout

    def invoke(self, action: str, **params: Any) -> Any:
        """通用调用，error 非 null 抛 AnkiConnectError。"""
        payload = {"action": action, "version": ANKI_CONNECT_VERSION, "params": params}
        try:
            resp = requests.post(self.url, json=payload, timeout=self.timeout)
        except requests.RequestException as e:
            raise AnkiConnectError(f"无法连接 AnkiConnect ({self.url}): {e}") from e
        if resp.status_code != 200:
            raise AnkiConnectError(f"AnkiConnect 返回 HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as e:
            raise AnkiConnectError(f"AnkiConnect 响应非 JSON（端口可能被占）: {e}") from e
        if data.get("error"):
            raise AnkiConnectError(str(data["error"]))
        return data.get("result")

    def decks(self) -> list[str]:
        return self.invoke("deckNames")

    def due_card_ids(self, deck: str, limit: int = 20) -> list[int]:
        query = f'deck:"{deck}" is:due'
        ids = self.invoke("findCards", query=query)
        return [int(i) for i in ids[:limit]]

    def cards_info(self, card_ids: list[int]) -> tuple[list[Card], int]:
        """获取卡片详情并解析。

        返回 (白名单内的 Card 列表, 被跳过的数量)。
        非 Basic/Cloze 类型被跳过，避免答案泄露到正面。
        """
        if not card_ids:
            return [], 0
        raw = self.invoke("cardsInfo", cards=list(card_ids))
        cards: list[Card] = []
        skipped = 0
        for item in raw:
            note_type = item.get("modelName", "")
            if note_type not in SUPPORTED_NOTE_TYPES:
                skipped += 1
                continue
            fields = item.get("fields", {})
            deck_name = item.get("deckName", "")
            if note_type == "Cloze":
                front, back = _render_cloze(fields)
            else:
                front, back = _render_basic(fields)
            cards.append(
                Card(
                    card_id=int(item["cardId"]),
                    front=front,
                    back=back,
                    deck=deck_name,
                    note_type=note_type,
                )
            )
        return cards, skipped

    def card_decks(self, card_ids: list[int]) -> dict[int, str]:
        """返回 {card_id: deck_name}，用于 profile/deck 一致性校验。

        不存在的 card（被删除）不会出现在返回中，调用方可据此判断。
        """
        if not card_ids:
            return {}
        raw = self.invoke("cardsInfo", cards=list(card_ids))
        return {int(i["cardId"]): i.get("deckName", "") for i in raw if "cardId" in i}

    def answer_cards(self, gradings: dict[int, int]) -> list[bool]:
        """批量写回评分。

        gradings: {card_id: ease}，ease ∈ {1,2,3,4}。
        返回与 gradings 顺序一致的 bool 列表；False 表示该卡写回失败
        （通常是卡片在 Anki 中已不存在）。
        """
        if not gradings:
            return []
        answers = [{"cardId": cid, "ease": ease} for cid, ease in gradings.items()]
        # AnkiConnect 实际参数名是 "answers"（非部分文档所写的 "cards"，实测确认）
        return [bool(x) for x in self.invoke("answerCards", answers=answers)]


def _render_basic(fields: dict) -> tuple[str, str]:
    """Basic：按 order 排序取前两个字段作正/背面。"""
    ordered = sorted(fields.values(), key=lambda v: v.get("order", 0))
    vals = [v.get("value", "") for v in ordered]
    front = vals[0] if len(vals) > 0 else ""
    back = vals[1] if len(vals) > 1 else ""
    return front, back


def _render_cloze(fields: dict) -> tuple[str, str]:
    """Cloze：Text 字段挖空解析；Extra 追加到 back。"""
    text = fields.get("Text", {}).get("value", "")
    extra = fields.get("Extra", {}).get("value", "")
    front = _cloze_to_blank(text)
    back = _cloze_to_answer(text)
    if extra.strip():
        back += f'<hr id="extra">{extra}'
    return front, back


def _cloze_to_blank(text: str) -> str:
    """挖空版：{{c1::答::提示}} → (提示)_____。"""

    def repl(m: re.Match) -> str:
        answer, hint = m.group(2), m.group(3)
        if hint:
            return (
                f'<span class="cloze-hint">({hint})</span>'
                f'<span class="cloze-blank"></span>'
            )
        return '<span class="cloze-blank"></span>'

    return _CLOZE_RE.sub(repl, text)


def _cloze_to_answer(text: str) -> str:
    """答案版：{{c1::答::提示}} → <span class="cloze-answer">答</span>。"""

    def repl(m: re.Match) -> str:
        answer = m.group(2)
        return f'<span class="cloze-answer">{answer}</span>'

    return _CLOZE_RE.sub(repl, text)
