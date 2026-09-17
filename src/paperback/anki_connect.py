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

# 正反面来源：直接用 AnkiConnect 已渲染好的 question/answer 字段。
# 这样任意 note type（Basic / Basic (and reversed) / 自定义模板）与正反向卡
# 都能拿到正确正反面，无需字段映射。Cloze 例外——用自定义挖空解析
# （默写卷下划线样式比 Anki 的 [...] 占位更清晰）。
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_HR_ANSWER_RE = re.compile(r"<hr[^>]*id=[\"']?answer[\"']?[^>]*>", re.IGNORECASE)

# 渲染残留占位符，纸面无法呈现：
# - 音频：[anki:play:a:N]（新版渲染）与 [sound:...]（标准 sound 标签），直接删
# - 打字题输入框：[[type:Front]] / [[type:Type Answer]] 等（{{type:...}} 模板残留）。
#   front 里替换成 cloze-blank 下划线（句中嵌时标出默写位，与 Cloze 挖空样式统一）；
#   back 里删掉（答案卷要的是答案，输入位无意义）
_PLACEHOLDER_RE = re.compile(
    r"\[anki:play:[^\]]*\]|\[sound:[^\]]*\]", re.IGNORECASE
)
_TYPE_RE = re.compile(r"\[\[type:[^\]]*\]\]", re.IGNORECASE)

# Cloze 挖空：{{cN::答案}} 或 {{cN::答案::提示}}。用 .*? 非贪婪，DOTALL 兼容多行。
_CLOZE_RE = re.compile(r"\{\{c(\d+)::(.*?)(?:::(.*?))?\}\}", re.DOTALL)

# 图片内嵌：Anki 卡片 <img src="x.png"> 引用 collection.media，独立浏览器/webview
# 加载不到。渲染前替换成 base64 data URI。
_IMG_SRC_RE = re.compile(r'(<img\s+[^>]*src=")([^"]+)(")', re.IGNORECASE)
_REMOTE_PREFIXES = ("http://", "https://", "data:")
_MIME_BY_EXT = {
    "jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif",
    "webp": "webp", "svg": "svg+xml", "bmp": "bmp", "ico": "x-icon",
}


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

    def due_card_ids(self, deck: str, limit: int | None = None, include_new: bool = False) -> list[int]:
        """到期卡 id。include_new=True 时加 is:new（全部未学新卡，不受 Anki 每日上限约束）。

        排除 buried/suspended：is:due 会命中 due 日已过的埋藏卡（当天不出队但搜索命中），
        挂起卡同理；两者都不该进默写卷。
        """
        cond = "(is:due or is:new)" if include_new else "is:due"
        query = f'deck:"{deck}" {cond} -is:buried -is:suspended'
        ids = [int(i) for i in self.invoke("findCards", query=query)]
        if limit is not None:
            ids = ids[:limit]
        return ids

    def cards_info(self, card_ids: list[int]) -> tuple[list[Card], int]:
        """获取卡片详情并解析。

        返回 (Card 列表, 跳过的数量)。
        正反面优先取 AnkiConnect 已渲染好的 question/answer（支持任意 note type
        与正反向卡）；Cloze 用自定义挖空解析。question 为空的卡被跳过。
        """
        if not card_ids:
            return [], 0
        raw = self.invoke("cardsInfo", cards=list(card_ids))
        cards: list[Card] = []
        skipped = 0
        for item in raw:
            note_type = item.get("modelName", "")
            deck_name = item.get("deckName", "")
            try:
                if note_type == "Cloze":
                    front, back = _render_cloze(item.get("fields", {}))
                else:
                    front, back = _render_from_qa(item)
            except Exception:
                skipped += 1
                continue
            if not front.strip():
                skipped += 1
                continue
            front = self._inline_images(front, is_front=True)
            back = self._inline_images(back)
            cards.append(
                Card(
                    card_id=int(item["cardId"]),
                    front=front,
                    back=back,
                    deck=deck_name,
                    note_type=note_type,
                    ord=int(item.get("ord", 0)),
                )
            )
        return cards, skipped

    def _inline_images(self, html: str, is_front: bool = False) -> str:
        """把 <img src="x.png"> 的相对 media 路径替换成 base64 data URI。

        Anki 卡片 img 引用 collection.media 文件，独立浏览器/webview 加载不到。
        远程 URL / 已是 data: 的保留；文件不存在或 AnkiConnect 错则保留原 src。
        同时清渲染残留：音频占位符删；[[type:...]] 输入框在 front 换成
        cloze-blank 下划线（标出默写位）、在 back 删（答案卷不需要输入位）。
        """
        if not html:
            return html
        html = _PLACEHOLDER_RE.sub("", html)
        html = _TYPE_RE.sub(
            '<span class="cloze-blank"></span>' if is_front else "", html
        )

        def repl(m: re.Match) -> str:
            prefix, src, suffix = m.group(1), m.group(2), m.group(3)
            if src.startswith(_REMOTE_PREFIXES):
                return m.group(0)
            clean = src.split("#")[0].split("?")[0]  # 去 hash / query
            try:
                b64 = self.invoke("retrieveMediaFile", filename=clean)
            except AnkiConnectError:
                return m.group(0)
            if not b64 or b64 is False:
                return m.group(0)
            ext = clean.rsplit(".", 1)[-1].lower() if "." in clean else ""
            mime = _MIME_BY_EXT.get(ext, "jpeg")
            return f"{prefix}data:image/{mime};base64,{b64}{suffix}"

        return _IMG_SRC_RE.sub(repl, html)

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


def _strip_style(html: str) -> str:
    """剥离 Anki 渲染内容里的 <style> 块。"""
    return _STYLE_RE.sub("", html)


def _render_from_qa(item: dict) -> tuple[str, str]:
    """用 AnkiConnect 已渲染好的 question/answer 取正反面。

    任意 note type 与正反向卡都适用：question 即该卡正面；answer 含
    正面 + <hr id=answer> + 背面，取 hr 之后作 back（无 hr 则整体）。
    """
    q = _strip_style(item.get("question", "")).strip()
    a = _strip_style(item.get("answer", ""))
    parts = _HR_ANSWER_RE.split(a, maxsplit=1)
    back = parts[1].strip() if len(parts) > 1 else a.strip()
    return q, back


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
