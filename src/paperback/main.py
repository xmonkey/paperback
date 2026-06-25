"""FastAPI 应用：路由、重试、flush、profile 一致性校验。

仅监听 127.0.0.1（单机工具，无认证）。
"""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from urllib.parse import quote

from . import ocr
from .anki_connect import AnkiConnect, AnkiConnectError
from .session import (
    Session,
    _data_dir,
    create_session,
    ensure_data_dir_writable,
    list_sessions,
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

# 启动时清理过期 OCR 图片（默认 > 365 天）
try:
    _OCR_CLEANED = ocr.cleanup_old_images(_data_dir())
except Exception:
    _OCR_CLEANED = 0


def _ocr_dir(sid: str) -> Path:
    """OCR 图片存储目录：~/.paperback/sessions/<sid>/ocr/。"""
    return _data_dir() / sid / "ocr"

_BACKOFF = (0.5, 1.0, 2.0)
_VALID_EASE = {1, 2, 3, 4}
_SCAN_LIMIT = 300  # generate 最多扫描的 due 卡片数（控 cardsInfo 性能）
_BATCH = 30        # cardsInfo 分批大小


class GradeBody(BaseModel):
    ease: int


def _anki() -> AnkiConnect:
    return AnkiConnect()


_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _html_has_cjk(html: str) -> bool:
    """html 纯文本是否含中文汉字（用于过滤中文释义卡）。

    中文释义必含汉字，故用 CJK 判断即可区分「英文单词/词组」与「中文释义」。
    不把中文标点算入：避免误伤「英文词组误用了中文逗号」的卡。
    """
    t = _STYLE_RE.sub("", html)
    t = _TAG_RE.sub("", t)
    return bool(_CJK_RE.search(t))


def _collect_cards(anki, ids: list[int], limit: int, filter_cjk: bool):
    """分批 cardsInfo + CJK 过滤，累积到 limit；最多扫描 _SCAN_LIMIT 个 id。

    避免对大 deck 一次性 cardsInfo 全部（AnkiConnect 在 Anki 主进程，会冻结 UI）。
    多数情况 1-2 批即够 limit。
    """
    cards = []
    skipped = 0
    scan = ids[:_SCAN_LIMIT]
    for i in range(0, len(scan), _BATCH):
        batch_cards, sk = anki.cards_info(scan[i : i + _BATCH])
        skipped += sk
        if filter_cjk:
            batch_cards = [c for c in batch_cards if not _html_has_cjk(c.back)]
        cards.extend(batch_cards)
        if len(cards) >= limit:
            break
    return cards[:limit], skipped


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
    sessions = list_sessions()
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
            "sessions": sessions,
        },
    )


@app.post("/generate")
def generate(
    deck: str = Form(...),
    limit: int = Form(20),
    filter_cjk: bool = Form(False),
):
    limit = max(1, min(100, limit))
    try:
        anki = _anki()
        ids = anki.due_card_ids(deck)  # 全部 due id（findCards 轻量）
    except AnkiConnectError as e:
        raise HTTPException(status_code=503, detail=f"无法连接 AnkiConnect: {e}")
    if not ids:
        return RedirectResponse(url=f"/?error=no_due&deck={quote(deck)}", status_code=303)
    # 分批 cardsInfo + CJK 过滤，累积到 limit；最多扫 300 个 id（控性能）
    cards, skipped = _collect_cards(anki, ids, limit, filter_cjk)
    if not cards:
        return RedirectResponse(
            url=f"/?error=no_match&deck={quote(deck)}",
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
            "ocr_config": ocr.config(),
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


@app.get("/session/{sid}/ocr")
def ocr_page(sid: str, request: Request):
    session = load_session(sid)
    if not session:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "ocr.html.j2",
        {"session": session, "ocr_config": ocr.config()},
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


# ---------- OCR 拍照批改 ----------


@app.post("/api/session/{sid}/ocr")
def ocr_session(sid: str, images: list[UploadFile] = File(...)):
    """接收 ≥1 张默写卷照片，逐图调视觉 LLM 识别 + 比对答案。

    每张图：预处理一次（EXIF 正向化 + 压缩）→ 存盘 + 喂 LLM。
    返回 {results, errors}；results 已把 index 映射为 card_id。
    """
    session = load_session(sid)
    if not session:
        raise HTTPException(status_code=404)
    if not images:
        raise HTTPException(status_code=400, detail="未提供图片")
    if not ocr.is_configured():
        raise HTTPException(status_code=400, detail="not_configured")

    cards = session.cards
    answers = ocr.build_answers(cards)
    index_to_cid = {i: c.card_id for i, c in enumerate(cards, 1)}

    ts = int(time.time())
    ocr_d = _ocr_dir(sid)
    ocr_d.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    errors: list[dict] = []
    for n, img in enumerate(images):
        raw = img.file.read()
        try:
            processed = ocr.preprocess_image(raw)
        except Exception as e:
            errors.append(
                {"image_index": n, "reason": "preprocess_error", "detail": str(e)}
            )
            continue
        fname = f"{ts}_{n}.jpg"
        (ocr_d / fname).write_bytes(processed)
        image_url = f"/ocr_img/{sid}/{fname}"
        image_b64 = base64.b64encode(processed).decode()
        try:
            parsed = ocr.call_llm(image_b64, answers)
        except ocr.OcrError as e:
            errors.append(
                {
                    "image_index": n,
                    "reason": e.reason,
                    "detail": str(e),
                    "image_url": image_url,
                }
            )
            continue
        for c in parsed.get("cards", []):
            idx = c.get("index")
            cid = index_to_cid.get(idx) if isinstance(idx, int) else None
            results.append(
                {
                    "card_id": cid,
                    "index": idx,
                    "ocr_text": c.get("ocr_text", ""),
                    "standard": c.get("standard", ""),
                    "suggested_ease": c.get("suggested_ease"),
                    "confidence": c.get("confidence"),
                    "note": c.get("note"),
                    "image_url": image_url,
                }
            )
    return {"results": results, "errors": errors}


@app.get("/ocr_img/{sid}/{file}")
def ocr_image_file(sid: str, file: str):
    """静态返回 OCR 图片（供结果页 <img> 展示）。仅本机 127.0.0.1 访问。"""
    if "/" in sid or "/" in file or ".." in sid or ".." in file:
        raise HTTPException(status_code=404)
    p = _ocr_dir(sid) / file
    if not p.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(str(p), media_type="image/jpeg")


def run():
    uvicorn.run("paperback.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
