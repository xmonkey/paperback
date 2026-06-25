"""OCR：调视觉 LLM 识别默写卷手写内容并比对标准答案。

协议：OpenAI 兼容 /chat/completions，env 切 provider（默认 GLM glm-5v-turbo）。
本模块不依赖 FastAPI / session，纯函数式，便于单测（mock requests）与复用。

POC 验证（/tmp/probe_full.py + test1/test2.jpg）：GLM 按手写原样识别、
不纠拼写、编号对齐准；千问有幻觉（自动补全/纠错）已排除。
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from typing import Any

import requests
from PIL import Image, ImageOps

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-5v-turbo"
DEFAULT_MAX_IMAGE_PX = 2000
DEFAULT_RETAIN_DAYS = 365
_RETRY_DELAYS = (1.0, 3.0)  # 网络异常 / 5xx 重试间隔
_TIMEOUT = 90

_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_ANSWER_TRUNC = 80  # 标准答案喂 LLM 时截断，控 token


class OcrError(Exception):
    """OCR 调用失败。reason ∈ not_configured/network/http_error/parse_error。"""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


# ---------- 配置 ----------


def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def is_configured() -> bool:
    return bool(os.environ.get("PAPERBACK_OCR_API_KEY"))


def config() -> dict[str, str]:
    """当前 OCR 配置（供前端显示 provider/model）。"""
    return {
        "base_url": _cfg("PAPERBACK_OCR_BASE_URL", DEFAULT_BASE_URL),
        "model": _cfg("PAPERBACK_OCR_MODEL", DEFAULT_MODEL),
        "configured": "true" if is_configured() else "false",
    }


# ---------- 工具 ----------


def plain_text(html: str) -> str:
    """HTML → 纯文本（去 style/tag，折叠空白）。供构造答案表喂 LLM。"""
    t = _STYLE_RE.sub("", html)
    t = _TAG_RE.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def build_answers(cards: list) -> dict[int, str]:
    """从 Card 列表构造 {index: 标准答案纯文本}。index 从 1 开始 = #NNN。"""
    return {i: plain_text(c.back)[:_ANSWER_TRUNC] for i, c in enumerate(cards, 1)}


# ---------- 图像预处理 ----------


def preprocess_image(image_bytes: bytes, max_px: int | None = None) -> bytes:
    """EXIF 正向化 + 长边压缩到 ≤ max_px，JPEG q85 重编码。

    解决手机照片 EXIF orientation 隐患（不同 LLM 后端处理不一），
    控 token / 加速。不做纠偏/透视/二值化。
    """
    if max_px is None:
        max_px = int(_cfg("PAPERBACK_OCR_MAX_IMAGE_PX", str(DEFAULT_MAX_IMAGE_PX)))
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img)  # 按 EXIF 旋正
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    scale = max_px / max(w, h)
    if scale < 1:
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        img = img.resize(new_size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# ---------- Prompt（POC 验证版，照搬 /tmp/probe_full.py） ----------

_SYSTEM_PROMPT = (
    "你是默写批改助手。图片是一张默写卷的页，版面固定：每张卡左侧是印刷题目，"
    "右侧填空区是用户手写答案，左上角编号 #NNN。\n\n"
    "⚠ 关键规则（务必遵守）：\n"
    "- ocr_text 字段：必须【只看图片手写区】识别，【绝对不允许参考、复制或借鉴下方标准答案表】。"
    "即使手写像拼写错的常见词（如 consold 像 console、inesdeed 像 instead），也要识别成手写原样"
    "（consold/inesdeed），不要修正成你脑海中的正确拼写。宁可保留错误拼写。\n"
    "- standard 字段：从下方标准答案表原样复制对应编号的答案。\n\n"
    "对图中每张卡：\n"
    "1. 识别编号 NNN\n"
    "2. 只看图，识别手写原样填入 ocr_text（禁止参考标准答案、禁止修正拼写）\n"
    "3. 逐字符比对 ocr_text 与 standard：完全一致（或仅大小写/标点差异）→正确；"
    "任何拼写差异（错别字/漏字/多字/字母顺序错）→错，即使语义接近、即使像笔误\n"
    "4. suggested_ease：只输出 3（good，正确）或 1（again，错/拼写错/没写）。不要输出 2 或 4\n"
    '严格按 JSON 返回：{"cards":[{"index":1,"ocr_text":"手写原样","standard":"答案","suggested_ease":3}]}'
)


def _build_messages(image_b64: str, answers: dict[int, str]) -> list[dict]:
    answer_text = "\n".join(f"{i}: {a}" for i, a in answers.items())
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"标准答案表（编号 → 答案）：\n{answer_text}\n\n请判分图里的每张卡。",
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                },
            ],
        },
    ]


# ---------- LLM 调用 ----------


def parse_json(content: str) -> dict:
    """解析 LLM 返回。先 json.loads，失败则取首个 { 到末个 } 之间子串再试。

    用 find/rfind 而非贪婪正则：贪婪 \{.*\} 对多 object 会匹配过头，
    非贪婪 \{.*?\} 对嵌套 object 会从外层 { 截到内层 }。find/rfind 取最外层
    一对大括号，对 response_format=json_object 的单 object 返回最稳。
    """
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end > start:
            return json.loads(content[start : end + 1])
        raise


def call_llm(image_b64: str, answers: dict[int, str]) -> dict[str, Any]:
    """已 base64 编码的图 + 答案表 → 调 LLM → 返回解析后 dict。不含预处理。

    失败抛 OcrError（reason: not_configured/network/http_error/parse_error）。
    """
    if not is_configured():
        raise OcrError("not_configured")
    base_url = _cfg("PAPERBACK_OCR_BASE_URL", DEFAULT_BASE_URL)
    model = _cfg("PAPERBACK_OCR_MODEL", DEFAULT_MODEL)

    messages = _build_messages(image_b64, answers)
    payload = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {os.environ['PAPERBACK_OCR_API_KEY']}"}
    url = f"{base_url.rstrip('/')}/chat/completions"

    last_err: Exception | None = None
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
        except requests.RequestException as e:
            last_err = e
            if attempt < len(_RETRY_DELAYS):
                time.sleep(_RETRY_DELAYS[attempt])
                continue
            raise OcrError("network", str(e)) from e

        if resp.status_code >= 500:
            last_err = RuntimeError(f"HTTP {resp.status_code}")
            if attempt < len(_RETRY_DELAYS):
                time.sleep(_RETRY_DELAYS[attempt])
                continue
            raise OcrError("http_error", f"HTTP {resp.status_code}: {resp.text[:200]}")
        if resp.status_code != 200:
            # 4xx（key 错等）不重试
            raise OcrError("http_error", f"HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return parse_json(content)
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            raise OcrError("parse_error", f"{e}; body={resp.text[:200]}") from e

    raise OcrError("api_error", str(last_err))


def grade_image(image_bytes: bytes, answers: dict[int, str]) -> dict[str, Any]:
    """原图 bytes → 预处理 → 调 LLM。便捷封装。

    路由层若需同时存盘压缩图，应分别调 preprocess_image + call_llm，避免重复预处理。
    """
    processed = preprocess_image(image_bytes)
    image_b64 = base64.b64encode(processed).decode()
    return call_llm(image_b64, answers)


# ---------- 留存清理 ----------


def cleanup_old_images(sessions_dir, retain_days: int | None = None) -> int:
    """清理 sessions_dir/<sid>/ocr/* 中 mtime > retain_days 天的文件。返回删除数。

    由应用启动时调用。
    """
    if retain_days is None:
        retain_days = int(_cfg("PAPERBACK_OCR_RETAIN_DAYS", str(DEFAULT_RETAIN_DAYS)))
    threshold = time.time() - retain_days * 86400
    removed = 0
    for ocr_dir in sorted(sessions_dir.glob("*/ocr")):
        if not ocr_dir.is_dir():
            continue
        for f in ocr_dir.iterdir():
            if f.is_file() and f.stat().st_mtime < threshold:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
    return removed
