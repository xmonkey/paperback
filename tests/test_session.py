"""Session 管理测试：CRUD、pending/invalid、原子写、并发安全。"""

from __future__ import annotations

import json
import threading

from paperback.models import Card
from paperback import session as S


def test_create_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERBACK_DATA_DIR", str(tmp_path))
    cards = [Card(1, "f1", "b1", "D", "Basic"), Card(2, "f2", "b2", "D", "Cloze")]
    s = S.create_session("D", cards)
    loaded = S.load_session(s.id)
    assert loaded is not None
    assert loaded.id == s.id
    assert loaded.deck == "D"
    assert len(loaded.cards) == 2
    assert loaded.cards[1].note_type == "Cloze"


def test_load_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERBACK_DATA_DIR", str(tmp_path))
    assert S.load_session("nope") is None


def test_mark_graded_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERBACK_DATA_DIR", str(tmp_path))
    s = S.create_session("D", [Card(i, "f", "b", "D", "Basic") for i in (1, 2)])
    s.mark_graded(1, 3)
    loaded = S.load_session(s.id)
    assert loaded.graded == {"1": 3}
    assert [c.card_id for c in loaded.remaining_cards()] == [2]


def test_mark_pending_increments_attempts(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERBACK_DATA_DIR", str(tmp_path))
    s = S.create_session("D", [Card(1, "f", "b", "D", "Basic")])
    s.mark_pending(1, 1)
    s.mark_pending(1, 1)
    loaded = S.load_session(s.id)
    assert loaded.pending["1"]["attempts"] == 2
    assert loaded.pending["1"]["ease"] == 1


def test_mark_graded_clears_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERBACK_DATA_DIR", str(tmp_path))
    s = S.create_session("D", [Card(1, "f", "b", "D", "Basic")])
    s.mark_pending(1, 1)
    s.mark_graded(1, 3)
    loaded = S.load_session(s.id)
    assert "1" not in loaded.pending
    assert loaded.graded == {"1": 3}


def test_mark_invalid_excludes_from_remaining(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERBACK_DATA_DIR", str(tmp_path))
    s = S.create_session("D", [Card(i, "f", "b", "D", "Basic") for i in (1, 2)])
    s.mark_invalid(1)
    loaded = S.load_session(s.id)
    assert "1" in loaded.invalid
    assert [c.card_id for c in loaded.remaining_cards()] == [2]


def test_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERBACK_DATA_DIR", str(tmp_path))
    s = S.create_session("D", [Card(i, "f", "b", "D", "Basic") for i in range(5)])
    s.mark_graded(1, 3)
    s.mark_pending(2, 1)
    s.mark_invalid(3)
    p = s.progress()
    assert p["total"] == 5
    assert p["graded"] == 1
    assert p["pending"] == 1
    assert p["invalid"] == 1
    assert p["remaining"] == 2


def test_atomic_write_valid_json(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERBACK_DATA_DIR", str(tmp_path))
    s = S.create_session("D", [Card(1, "f", "b", "D", "Basic")])
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    # 无残留 tmp 文件
    assert list(tmp_path.glob("*.tmp")) == []
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["deck"] == "D"
    assert data["graded"] == {}
    assert data["invalid"] == []


def test_no_tmp_left_after_write(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERBACK_DATA_DIR", str(tmp_path))
    s = S.create_session("D", [Card(1, "f", "b", "D", "Basic")])
    for i in range(10):
        s.mark_graded(1, (i % 4) + 1)
    assert list(tmp_path.glob("*.tmp")) == []  # 原子写无残留


def test_concurrent_writes_no_loss(tmp_path, monkeypatch):
    """100 线程并发评分 100 张不同卡片，不应丢失。"""
    monkeypatch.setenv("PAPERBACK_DATA_DIR", str(tmp_path))
    s = S.create_session("D", [Card(i, "f", "b", "D", "Basic") for i in range(100)])
    sid = s.id

    def grade(i):
        loaded = S.load_session(sid)
        loaded.mark_graded(i, (i % 4) + 1)

    threads = [threading.Thread(target=grade, args=(i,)) for i in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = S.load_session(sid)
    assert len(final.graded) == 100  # 无丢失


def test_list_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERBACK_DATA_DIR", str(tmp_path))
    S.create_session("A", [Card(1, "f", "b", "A", "Basic")])
    S.create_session("B", [Card(2, "f", "b", "B", "Basic")])
    sessions = S.list_sessions()
    assert len(sessions) == 2
    assert {x["deck"] for x in sessions} == {"A", "B"}


def test_list_sessions_ease_counts_and_remaining(tmp_path, monkeypatch):
    """已完成 session 带 ease 分布 + remaining=0；未完成 remaining>0。"""
    monkeypatch.setenv("PAPERBACK_DATA_DIR", str(tmp_path))
    # 完成的 session：3 张全评分
    done = S.create_session(
        "D", [Card(1, "f1", "b1", "D", "Basic"), Card(2, "f2", "b2", "D", "Basic"),
              Card(3, "f3", "b3", "D", "Basic")]
    )
    d = S.load_session(done.id)
    d.mark_graded(1, 1)
    d.mark_graded(2, 3)
    d.mark_graded(3, 4)
    # 未完成的 session
    S.create_session("E", [Card(10, "f", "b", "E", "Basic")])

    by_deck = {x["deck"]: x for x in S.list_sessions()}
    assert by_deck["D"]["remaining"] == 0
    assert by_deck["D"]["ease_counts"] == {"1": 1, "2": 0, "3": 1, "4": 1}
    assert by_deck["E"]["remaining"] == 1
    assert by_deck["E"]["ease_counts"] == {"1": 0, "2": 0, "3": 0, "4": 0}
