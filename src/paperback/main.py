"""FastAPI 应用：路由、重试、flush、profile 一致性校验。

仅监听 127.0.0.1（单机工具，无认证）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from urllib.parse import quote

from .anki_connect import AnkiConnect, AnkiConnectError
from .session import (
    Session,
    create_session,
    ensure_data_dir_writable,
    load_session,
)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Paperback")

# 启动时校验数据目录可写
_DATA_DIR_OK = True
_DATA_DIR_ERR = ""
try:
    ensure_data_dir_writable()
except PermissionError as e:
    _DATA_DIR_OK = False
    _DATA_DIR_ERR = str(e)

_BACKOFF = (0.5, 1.0, 2.0)
_VALID_EASE = {1, 2, 3, 4}


class GradeBody(BaseModel):
    ease: int


def _anki() -> AnkiConnect:
    return AnkiConnect()


# ---------- 页面 ----------


@app.get("/")
def index(
    request: Request,
    error: Optional[str] = None,
    deck: Optional[str] = None,
    count: int = 0,
):
    decks: list[str] = []
    anki_error = None
    try:
        decks = _anki().decks()
    except AnkiConnectError as e:
        anki_error = str(e)
    return templates.TemplateResponse(
        request,
        "index.html.j2",
        {
            "decks": decks,
            "anki_error": anki_error,
            "error": error,
            "error_deck": deck,
            "error_count": count,
            "data_dir_ok": _DATA_DIR_OK,
            "data_dir_err": _DATA_DIR_ERR,
        },
    )


@app.post("/generate")
def generate(deck: str = Form(...), limit: int = Form(20)):
    limit = max(1, min(100, limit))
    try:
        anki = _anki()
        ids = anki.due_card_ids(deck, limit)
        cards, skipped = anki.cards_info(ids)
    except AnkiConnectError as e:
        raise HTTPException(status_code=503, detail=f"无法连接 AnkiConnect: {e}")
    if not ids:
        return RedirectResponse(url=f"/?error=no_due&deck={quote(deck)}", status_code=303)
    if not cards:
        return RedirectResponse(
            url=f"/?error=unsupported_type&deck={quote(deck)}&count={len(ids)}",
            status_code=303,
        )
    session = create_session(deck, cards)
    return RedirectResponse(
        url=f"/session/{session.id}?skipped={skipped}", status_code=303
    )


@app.get("/session/{sid}")
def session_overview(sid: str, request: Request, skipped: int = 0):
    session = load_session(sid)
    if not session:
        raise HTTPException(status_code=404, detail="session 不存在")
    return templates.TemplateResponse(
        request,
        "session.html.j2",
        {
            "session": session,
            "cards": session.cards,
            "progress": session.progress(),
            "skipped": skipped,
        },
    )


@app.get("/session/{sid}/worksheet")
def worksheet(sid: str, request: Request):
    session = load_session(sid)
    if not session:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "worksheet.html.j2",
        {"session": session, "cards": session.cards},
    )


@app.get("/session/{sid}/answerkey")
def answerkey(sid: str, request: Request):
    session = load_session(sid)
    if not session:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "answerkey.html.j2",
        {"session": session, "cards": session.cards},
    )


@app.get("/session/{sid}/grade")
def grade_page(sid: str, request: Request):
    session = load_session(sid)
    if not session:
        raise HTTPException(status_code=404)
    mismatched = _verify_deck(session)
    return templates.TemplateResponse(
        request,
        "grade.html.j2",
        {
            "session": session,
            "cards_json": json.dumps(
                [c.to_dict() for c in session.remaining_cards()],
                ensure_ascii=False,
            ),
            "progress": session.progress(),
            "mismatched": mismatched,
        },
    )


# ---------- API ----------


def _verify_deck(session: Session) -> Optional[list[dict]]:
    """profile/deck 一致性校验。返回 None 表示连不上 Anki（不阻断）；
    返回非空 list 表示有不一致，应拦截批改。"""
    try:
        decks = _anki().card_decks([c.card_id for c in session.cards])
    except AnkiConnectError:
        return None
    mismatched: list[dict] = []
    for c in session.cards:
        if c.card_id not in decks:
            mismatched.append({"card_id": c.card_id, "reason": "卡片已在 Anki 中删除"})
        elif decks[c.card_id] != session.deck:
            mismatched.append(
                {"card_id": c.card_id, "reason": f"deck 已变为「{decks[c.card_id]}」"}
            )
    return mismatched


def _try_answer(anki: AnkiConnect, card_id: int, ease: int, retries: int = 3):
    """指数退避重试。返回 (success, reason)。reason ∈ ok/invalid/network。"""
    for attempt in range(retries):
        try:
            results = anki.answer_cards({card_id: ease})
            if results and results[0]:
                return True, "ok"
            return False, "invalid"  # 卡片不存在
        except AnkiConnectError:
            if attempt < retries - 1:
                time.sleep(_BACKOFF[attempt])
    return False, "network"


def _flush_pending(session: Session, anki: AnkiConnect, retry_all: bool):
    """重试 pending。成功→graded；失效→invalid；网络失败→(retry_all 时 attempts+1)。"""
    pend = dict(session.pending)
    flushed = 0
    still = 0
    for cid_str, info in pend.items():
        cid = int(cid_str)
        try:
            results = anki.answer_cards({cid: info["ease"]})
            if results and results[0]:
                session.mark_graded(cid, info["ease"])
                flushed += 1
            else:
                session.mark_invalid(cid)
        except AnkiConnectError:
            if retry_all:
                session.mark_pending(cid, info["ease"])
            still += 1
    return flushed, still


@app.post("/api/session/{sid}/grade/{card_id}")
def grade_card(sid: str, card_id: int, body: GradeBody):
    session = load_session(sid)
    if not session:
        raise HTTPException(status_code=404)
    if body.ease not in _VALID_EASE:
        raise HTTPException(status_code=400, detail="ease 必须是 1-4")
    anki = _anki()
    success, reason = _try_answer(anki, card_id, body.ease)
    if success:
        session.mark_graded(card_id, body.ease)
        _flush_pending(session, anki, retry_all=False)  # 顺带 flush 一次
    elif reason == "invalid":
        session.mark_invalid(card_id)
    else:
        session.mark_pending(card_id, body.ease)
    return {"ok": success, "reason": reason, "progress": session.progress()}


@app.post("/api/session/{sid}/flush")
def flush(sid: str):
    session = load_session(sid)
    if not session:
        raise HTTPException(status_code=404)
    anki = _anki()
    flushed, still = _flush_pending(session, anki, retry_all=True)
    return {
        "flushed": flushed,
        "still_pending": still,
        "progress": session.progress(),
    }


def run():
    uvicorn.run("paperback.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
