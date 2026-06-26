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
import threading
import time
from pathlib import Path
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


# 配置文件：~/.paperback/config.json（与 sessions/ 同根，不进 repo）
_config_lock = threading.Lock()


def _config_path() -> Path:
    sessions_dir = Path(
        os.environ.get(
            "PAPERBACK_DATA_DIR", str(Path.home() / ".paperback" / "sessions")
        )
    )
    return sessions_dir.parent / "config.json"


def _read_stored() -> dict:
    """读 config.json 的 ocr 段。文件不存在/解析失败 → {}（fallback env）。"""
    try:
        data = json.loads(_config_path().read_text(encoding="utf-8"))
        ocr = data.get("ocr")
        return ocr if isinstance(ocr, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_config(api_key: str | None, base_url: str, model: str) -> None:
    """写 ocr 段（保留其他顶层键）。api_key 空/None 时保留原值。原子写 + 锁。"""
    with _config_lock:
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        ocr = data.setdefault("ocr", {})
        if api_key:  # 空则保留原 key
            ocr["api_key"] = api_key
        ocr["base_url"] = base_url
        ocr["model"] = model
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(path)


def get_config() -> dict[str, str]:
    """合并后的 OCR 配置（存储 > env > 默认）。含 api_key 明文，仅内部用。"""
    s = _read_stored()
    return {
        "api_key": s.get("api_key") or _cfg("PAPERBACK_OCR_API_KEY"),
        "base_url": s.get("base_url") or _cfg("PAPERBACK_OCR_BASE_URL", DEFAULT_BASE_URL),
        "model": s.get("model") or _cfg("PAPERBACK_OCR_MODEL", DEFAULT_MODEL),
    }


def is_configured() -> bool:
    return bool(get_config()["api_key"])


def config() -> dict:
    """对外配置（不含 key 明文），供模板/API。保持 configured 字段兼容现有模板。"""
    c = get_config()
    return {
        "base_url": c["base_url"],
        "model": c["model"],
        "configured": "true" if c["api_key"] else "false",
        "has_key": bool(c["api_key"]),
    }


def test_connection(api_key: str, base_url: str, model: str) -> tuple[bool, str]:
    """用给定配置调一次最小 LLM 请求验证可达。返回 (ok, detail)。不入库。"""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }
    try:
        r = requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=15,
        )
    except requests.RequestException as e:
        return False, f"network: {e}"
    if r.status_code == 200:
        return True, "ok"
    return False, f"HTTP {r.status_code}: {r.text[:200]}"


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
    "你是默写批改助手。图片是一张默写卷的页，版面固定：每张卡左侧是印刷题目"
    "（可能含中文释义——那是给用户的提示，不是答案），右侧填空区是用户手写答案，"
    "左上角编号 #NNN。\n\n"
    "⚠ 关键规则：\n"
    "- ocr_text：\n"
    "  · 必须【只看图片手写区】识别，【绝不参考/复制/借鉴下方答案表】。\n"
    "  · 【保持手写原始语言，禁止翻译】——手写英文识别成英文，手写中文识别成中文，"
    "绝不把英文翻成中文释义、也不把中文翻成英文。\n"
    "  · 拼写按手写原样，禁止纠正（consold 就写 consold，不要变 console）。\n"
    "- standard：【只能从下方答案表原样复制】对应编号的答案。照片左侧印刷的中文释义"
    "是题目提示、绝不是 standard——即便你认为手写和答案表对不上，也禁止从照片印刷内容编造。\n\n"
    "对图中每张卡：\n"
    "1. 识别编号 NNN\n"
    "2. 按手写原样（原语言、原拼写）填 ocr_text\n"
    "3. 比对前先去掉 standard 末尾的词性标注括号（如 (v.) (n.) (n./v.) (phr.) 等），"
    "得到核心答案后再逐字符比 ocr_text：一致（或仅大小写/标点差异）→正确；"
    "任何拼写差异（错别字/漏字/多字/字母顺序错）→错，即使语义接近、即使像笔误\n"
    "4. 若 ocr_text 与 standard 语言不同或明显无关 → note 标"
    "\"⚠ 疑似照片与此 session 不匹配\"，suggested_ease=1\n"
    "5. suggested_ease 只输出 3（good，正确）或 1（again，错/拼写错/没写），不要 2 或 4\n"
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
    r"""解析 LLM 返回。先 json.loads，失败则取首个 { 到末个 } 之间子串再试。

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
    cfg = get_config()
    if not cfg["api_key"]:
        raise OcrError("not_configured")
    base_url = cfg["base_url"]
    model = cfg["model"]

    messages = _build_messages(image_b64, answers)
    payload = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
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
