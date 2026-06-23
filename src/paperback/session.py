"""Session 管理：~/.paperback/sessions/<id>.json。

关联 generate 与 grade 的状态。含 threading.Lock + 原子写，防多页签 race condition。

并发安全策略：所有 mark_* 方法在锁内执行「重新读盘最新状态 → 合并修改 → 写盘」，
确保多个 Session 实例（如多个页签）并发评分时不会基于过期内存互相覆盖。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from .models import Card

DEFAULT_DATA_DIR = Path.home() / ".paperback" / "sessions"

# 进程内锁；第一版默认 uvicorn 单 worker 足够。多 worker 需文件锁（P2）。
_lock = threading.Lock()


def _data_dir() -> Path:
    return Path(os.environ.get("PAPERBACK_DATA_DIR", str(DEFAULT_DATA_DIR)))


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _dumps(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_data_dir_writable() -> None:
    """启动校验：数据目录可创建且可写。失败抛 PermissionError。"""
    d = _data_dir()
    d.mkdir(parents=True, exist_ok=True)
    probe = d / ".paperback_write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        raise PermissionError(f"数据目录不可写: {d} ({e})") from e


class Session:
    """单个默写会话的内存视图，写方法均落盘且以磁盘最新状态为准。"""

    def __init__(self, data: dict, path: Path):
        self._data = data
        self._path = path

    @property
    def id(self) -> str:
        return self._data["id"]

    @property
    def deck(self) -> str:
        return self._data["deck"]

    @property
    def cards(self) -> list[Card]:
        return [Card.from_dict(c) for c in self._data.get("cards", [])]

    @property
    def graded(self) -> dict[str, int]:
        return self._data.setdefault("graded", {})

    @property
    def pending(self) -> dict[str, dict]:
        return self._data.setdefault("pending", {})

    @property
    def invalid(self) -> list[str]:
        return self._data.setdefault("invalid", [])

    def remaining_cards(self) -> list[Card]:
        """尚未处理的卡片（排除已评分 / pending / 失效）。"""
        done = set(self.graded) | set(self.pending) | set(self.invalid)
        return [c for c in self.cards if str(c.card_id) not in done]

    def progress(self) -> dict:
        total = len(self._data.get("cards", []))
        return {
            "total": total,
            "graded": len(self.graded),
            "pending": len(self.pending),
            "invalid": len(self.invalid),
            "remaining": total - len(self.graded) - len(self.pending) - len(self.invalid),
        }

    def mark_graded(self, card_id: int, ease: int) -> None:
        with _lock:
            data = _read_json(self._path)
            key = str(card_id)
            data.setdefault("graded", {})[key] = ease
            data.setdefault("pending", {}).pop(key, None)
            self._data = data
            _atomic_write(self._path, _dumps(data))

    def mark_pending(self, card_id: int, ease: int) -> None:
        """记录到 pending（待重试）。若已存在则 attempts +1。"""
        with _lock:
            data = _read_json(self._path)
            key = str(card_id)
            entry = data.setdefault("pending", {}).get(key, {})
            entry["ease"] = ease
            entry["ts"] = _now_iso()
            entry["attempts"] = entry.get("attempts", 0) + 1
            data.setdefault("pending", {})[key] = entry
            self._data = data
            _atomic_write(self._path, _dumps(data))

    def mark_invalid(self, card_id: int) -> None:
        """标记卡片失效（已删除/deck 不匹配），不再重试。"""
        with _lock:
            data = _read_json(self._path)
            key = str(card_id)
            invalid = data.setdefault("invalid", [])
            if key not in invalid:
                invalid.append(key)
            data.setdefault("pending", {}).pop(key, None)
            self._data = data
            _atomic_write(self._path, _dumps(data))


def _atomic_write(path: Path, text: str) -> None:
    """原子写：临时文件 + os.replace，防半写损坏。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def create_session(deck: str, cards: list[Card]) -> Session:
    d = _data_dir()
    base = datetime.now().strftime("%Y%m%d_%H%M%S")
    sid = base
    i = 2
    while (d / f"{sid}.json").exists():
        sid = f"{base}_{i}"
        i += 1
    data = {
        "id": sid,
        "deck": deck,
        "created_at": _now_iso(),
        "cards": [c.to_dict() for c in cards],
        "graded": {},
        "pending": {},
        "invalid": [],
    }
    path = d / f"{sid}.json"
    with _lock:
        _atomic_write(path, _dumps(data))
    return Session(data, path)


def load_session(sid: str) -> Session | None:
    path = _data_dir() / f"{sid}.json"
    if not path.exists():
        return None
    with _lock:
        data = _read_json(path)
    return Session(data, path)


def list_sessions() -> list[dict]:
    d = _data_dir()
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json"), reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(
                {
                    "id": data["id"],
                    "deck": data["deck"],
                    "total": len(data.get("cards", [])),
                    "graded": len(data.get("graded", {})),
                    "pending": len(data.get("pending", {})),
                }
            )
        except (json.JSONDecodeError, KeyError):
            continue
    return out
