"""数据模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Card:
    """Anki 卡片解析后的表示。

    front/back 已完成 Cloze 挖空/填答处理，是可直接 ``|safe`` 渲染的 HTML。
    """

    card_id: int
    front: str
    back: str
    deck: str
    note_type: str  # Basic / Basic (and reversed card) / Cloze / 自定义
    ord: int = 0  # 模板序号：0=正向，>=1 反向

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Card:
        return cls(
            card_id=int(data["card_id"]),
            front=data["front"],
            back=data["back"],
            deck=data["deck"],
            note_type=data["note_type"],
            ord=int(data.get("ord", 0)),
        )
