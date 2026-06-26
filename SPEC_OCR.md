# 拍照批改（OCR）规格 — 方案 A：LLM 视觉端到端

> 状态：实现中（v1）。POC 已通过（`/tmp/probe_full.py` + test1/test2.jpg，GLM glm-5v-turbo）。以下为最终实现决策。

## v1 实现决策（2026-06-25 确认）

POC 结论：**GLM 老实**（`averge`/`progyramm`/漏写 `be doing sth.` 都按手写原样识别、判错准确）；**千问 qwen-vl-max 有幻觉**（把图上的 `capable` 补全成 `be capable of doing sth.` 判对、吃掉 `consold.`、把 `averge` 纠成 `average`）→ **provider 定 GLM，排除千问**。

| 维度 | 决策 |
|---|---|
| Provider | GLM（智谱），OpenAI 兼容协议，env 切换 |
| 默认 model | ⚠ `glm-5v-turbo`（POC 实测验证；原稿 `glm-4v-plus`） |
| Prompt | 照搬 probe_full.py 验证版：ocr_text 只看图、**禁止参考答案、禁止纠拼写**；逐字符比对；ease 只输出 1 或 3 |
| ⚠ 图像预处理 | 发图前两件（引入 Pillow）：① `ImageOps.exif_transpose()` 正向化（test1.jpg EXIF 标 `orientation=upper-right`，靠 LLM 后端转不可靠）；② 长边 > 2000px → resize + JPEG q85。**不做**纠偏/透视/二值化（原稿"预处理不做"作废） |
| ⚠ Python 依赖 | 新增 `Pillow`（原稿"无新增依赖"作废——压缩必须它） |
| env 读取 | 纯 `os.environ.get`，**不自动读 .env**（保留 `source .env_glm` 切 provider 的现有用法） |
| ⚠ 图片留存 | 默认 **365 天**（原稿 30），启动时按 mtime 自动清理 `~/.paperback/sessions/*/ocr/*` |
| ⚠ 隐私提示 | 上传页**静态文案**（不弹窗）：「照片将上传至 GLM 云端识别，原图本地留存 365 天」 |
| index→card_id | `session.cards[index-1].card_id`（`#NNN` = cards 序号+1） |
| 提交后 | 跳回 session 概览页看进度 |

### env

```
PAPERBACK_OCR_API_KEY        # 必填
PAPERBACK_OCR_BASE_URL       # 默认 https://open.bigmodel.cn/api/paas/v4
PAPERBACK_OCR_MODEL          # 默认 glm-5v-turbo
PAPERBACK_OCR_MAX_IMAGE_PX   # 默认 2000（长边压缩阈值）
PAPERBACK_OCR_RETAIN_DAYS    # 默认 365
```

### 文件清单

- 新建：`src/paperback/ocr.py`、`templates/ocr_upload.html.j2`、`templates/ocr_results.html.j2`、`tests/test_ocr.py`
- 改：`src/paperback/main.py`（OCR 路由 + 启动清理钩子）、`templates/session.html.j2`（入口按钮）、`pyproject.toml`（+pillow）

### v1 不做

隐私"已同意"持久化、QR/ArUco 定位、本地 OCR（PaddleOCR）、自动批量应用、~~provider 切换 UI~~（⚠ v1.1 已做，见下节）、confidence 二次校对模型

---

## 配置 UI + 本地存储（v1.1，2026-06-25）

> 把 OCR 配置从「env + 重启」改为「Web UI + 本地存储」，无需重启即可切 provider/key。env 用法保留作 fallback。

### 存储
- 文件：`~/.paperback/config.json`（与 `sessions/` 同根；`~/.paperback` 在 home，天然不进 repo）
- 结构：`{"ocr": {"api_key", "base_url", "model"}}`
- API key 明文存（单机工具、仅 127.0.0.1、无认证 = 攻击面 0，可接受）

### 优先级
`ocr.config()` 读取顺序：**本地存储 > env > 默认值**
- UI 配了用 UI 的；没配 fallback 到 env（保留 `source .env_glm` 用法不破坏）；都没有 → 默认 base_url/model，`is_configured()=false`

### UI
- 入口：首页「⚙ OCR 设置」→ 独立页 `/settings`（未来可扩展）
- provider 预设下拉（GLM 智谱 / 通义千问 / 自定义）→ 一键填 `base_url` + `model`
- 字段：base_url、model、api_key（`type=password` 遮蔽 + 「显示」切换）
- 「测试连接」按钮：用当前表单配置（不入库）调一次最小 LLM 请求，返回 ok / 错误原因
- key 字段：GET 不返明文只返 `has_key`；表单 key 框空（占位「已配置，留空不修改」），PUT 时空值=保留原 key

### 后端
- `GET /api/config` → `{ocr:{base_url, model, configured, has_key}}`（**不返 key 明文**）
- `PUT /api/config` → 存 `{api_key?, base_url, model}`（key 空则保留原）
- `POST /api/config/test` → 用提交配置（不入库）调一次最小 LLM，返回 `{ok, detail}`
- `ocr.py`：`config()`/`is_configured()`/`call_llm()` 从存储读，动态（每次读文件，不缓存）

### 健壮性
- 配置文件原子写（复用 `_atomic_write` 模式）+ 读写锁
- 文件不存在 / 解析失败 → 优雅 fallback 到 env，不崩

### 文件
- 改：`ocr.py`（config 读存储）、`main.py`（3 路由）
- 新建：`templates/settings.html.j2`
- 改：`templates/index.html.j2`（入口链接）

---

## 重复批改防护（v1.2，2026-06-25）

> 同一 session 重复拍照批改时，防止已批改的卡被重复写回 Anki。

### 问题
OCR API 原先返回 results 不带已批改状态，前端 `submitAll` 对所有识别到的卡
提交，`grade_card` 也不检查重复 → 对同一 session 上传两次照片并提交，同一张卡
被 `answerCards` 多次（调度被反复覆盖）。

### 决策
- **API 返回 graded_ease**：`POST /api/session/<sid>/ocr` 的 results 每条带
  `graded_ease`（从 `session.graded` 查；`null`=未批改）。
- **前端标灰 + 跳过**：已批改的卡整行灰显，标注「✓ 已批改 (ease=N)」，不显示
  评分按钮；`submitAll` 跳过这些卡；summary 显示「已批改 X（跳过）」。
- **不做重新评分**：与项目级「撤销评分不做」一致——重新默写请新建 session。

### Anki 数据安全
`answerCards` 本身幂等（应用 ease 到当前调度，不累加），重复调用不损坏数据，
但会覆盖调度。防护目的是防误操作，非数据完整性。

---

## 模型选型对比（2026-06-26 评估）

> 评估 GLM-OCR、GLM-4.1V-Thinking-FlashX 能否替代 glm-5v-turbo。结论：**不替代**。

### 测过的模型

| 模型 | 端点 | 范式 | 默写场景结论 |
|---|---|---|---|
| **glm-5v-turbo**（当前） | chat/completions | 视觉对话，能判分 | ✅ 老实原样识别、遵守 prompt、**网页端/API 判分一致**（2026-06-26 验证） |
| GLM-OCR | `/layout_parsing` | 纯 OCR，只识文本不判分 | ❌ 自动纠拼写（consolde→console） |
| GLM-4.1V-Thinking-FlashX | chat/completions | 视觉对话 + 思维链 | ⚠️ 判分智能但识别「选择性纠正」不可预测 |
| qwen-vl-max（千问） | chat/completions | 视觉对话 | ❌ 幻觉（把 capable 补成 be capable of doing sth. 判对） |
| 豆包 Doubao-Seed-2.1-pro | `/responses`（非 chat/completions） | 视觉对话 + thinking | ⚠️ 走 responses API（Paperback 不兼容）+ thinking 模型默写任务太重，chat/completions 调用超时 >300s，未完成测试 |

### 关键证据（test2.jpg 实测）

- **GLM-OCR**：识别「全对」（console / average / person），turbo「全错拼」（consold / averge / persien）——GLM-OCR 在自动纠正拼写，掩盖用户错误
- **GLM-4.1V-Thinking**：#6 `perseveren` 原样保留 ✓，但 #11 `console` / #12 `person` 疑似纠正 ⚠——**同一张图有时纠有时不纠**，不可预测
- **glm-5v-turbo**：一致地原样识别（averge / persien / consold 全保留错拼），可预测

### 决策

**继续 glm-5v-turbo**。默写核心要求是「可预测的原样识别」，不是「智能」——模型越聪明越倾向纠正拼写，反而把错的判对、掩盖用户错误。

### 判分智能的替代方案

Thinking 模型判分更合理（如 `wonder` vs `wonder (v.)` 判对，理解词性标注）。但这是 **prompt 问题不是 model 问题**——已通过改 prompt（比对前去掉 standard 末尾词性标注括号）让 turbo 也判对（commit 299ede4），不需要换 model。

---

> 以下为原设计稿，保留作背景。**以本节（v1 实现决策）为准**，差异处已在上表用 ⚠ 标注。

## Context

用户纸笔默写完成后，手机拍照上传，系统调用视觉 LLM 识别手写内容、与标准答案比对、给出评分建议；用户确认后写回 Anki。延续 Paperback「手动批改」理念——LLM 只建议，人最终拍板。

本方案复用现有 session（卡片/编号）与 grade 写回链路，新增最小必要的拍照上传与 LLM 调用。

## 总体流程

```
批改页 [拍照批改] → 上传页（手机调相机，可多页多张）
   → POST /api/session/<id>/ocr（逐图调视觉 LLM）
   → 结果确认页（每卡：原图 + 识别文本 + 标准答案 + 建议档位）
   → 用户逐卡确认/修正
   → 走现有 grade API 写回 Anki
```

## 关键决策（实现简单优先）

| 维度 | 决策 | 理由 |
|---|---|---|
| LLM 协议 | **OpenAI 兼容 `/chat/completions`** | GLM、千问等都提供兼容端点，一套代码用 `base_url` 切换 |
| 默认 provider | **GLM（智谱）`glm-4v-plus`** | 国区、视觉强、价格低；改 env 可切千问 |
| API key | 环境变量 `PAPERBACK_OCR_API_KEY` | 最简，不做 UI |
| 拍照输入 | 手机浏览器 `<input type="file" accept="image/*" capture="environment">` | 手机优先，直接调相机 |
| 编号对齐 | **LLM 从图中读 `#NNN`**，自动对齐 session 卡片 | 默写卷已印编号，无需用户手标页码 |
| 图像预处理 | **不做**（不纠偏/不透视变换） | 现代 LLM 对倾斜/光照容忍度够；用户尽量拍正 |
| 定位标记 | 不加 QR/定位框（P2 优化项） | 编号已够对齐 |
| 确认机制 | LLM 给建议，**用户逐卡确认/修正**，不自动应用 | 识别不可能 100%，人审符合产品理念 |
| 写回 | 复用现有 `POST /api/session/<id>/grade/<card_id>` | 不新增写回逻辑 |

## 默写卷改造（最小）

当前 `#NNN` 编号已存在，为确保 LLM 可靠识别：
- 编号字体**加粗、加大**（约 14pt+）
- 编号与卡内容有足够对比度
- 不改版面结构（保持左题目/右填空）

> 第一版不加 QR/定位框。若 LLM 读编号错误率高，再补定位标记（P2）。

## LLM 调用

**协议**：OpenAI 兼容 `POST {base_url}/chat/completions`，消息含 `{type:image_url, image_url:{url:"data:image/jpeg;base64,..."}}`。用现有 `requests`，**不引入 SDK**。
**默认 provider/model**：GLM（智谱）`glm-4v-plus`
**单次调用 = 1 张图 + 该 session 全部卡片的标准答案**

为何带全 session 答案：让 LLM 从图中读到的编号直接在答案表里查，无需后端预先切分"这页对应哪几张"。答案表是纯文本，token 成本可忽略。

### Provider 配置示例（任选其一，改 env 即可）

```bash
# GLM（智谱）— 默认
export PAPERBACK_OCR_BASE_URL=https://open.bigmodel.cn/api/paas/v4
export PAPERBACK_OCR_API_KEY=你的智谱 key
export PAPERBACK_OCR_MODEL=glm-4v-plus

# 通义千问
export PAPERBACK_OCR_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export PAPERBACK_OCR_API_KEY=你的 dashscope key
export PAPERBACK_OCR_MODEL=qwen-vl-max
```

> 模型名以各 provider 最新视觉模型为准，可在 env 里随时更换，不改代码。

### Prompt 结构

**system**：
```
你是默写批改助手。图片是一张默写卷的页，版面固定：每张卡左侧是印刷的题目，
右侧填空区是用户的手写答案，左上角有编号 #NNN。
请对图中每张卡：
1. 识别编号 NNN
2. 严格按手写原样识别填空区内容（**禁止自动修正拼写**：用户写成 "recrod" 就识别成 "recrod"，不要"智能"纠正成 "record"。这点极重要，否则会掩盖拼写错误导致误判）
3. 与标准答案严格比对（默写要求精确产出）：
   - 拼写完全一致 → 正确
   - 拼写错误 / 错别字 / 漏字 / 多字 → 算错（即使语义接近、即使像笔误）
   - 仅大小写 / 标点差异 → 可算对
4. suggested_ease：**只输出 3（good，拼写正确）或 1（again，拼写错/错别字/漏字/多字/没写）**。不要输出 2 或 4——那两档由用户人工调整
严格按 JSON schema 返回，不要额外解释。
```

**user**（含图 + 文本）：
```
标准答案表（编号 → 答案）：
1: 東京
2: <p>the capital of Japan</p>
...

请返回每张识别到的卡的判分。
```

请求体建议带 `response_format: {"type": "json_object"}`（GLM/千问兼容端点均支持），降低解析失败率。

### 输出 JSON schema

```json
{
  "cards": [
    {
      "index": 1,
      "ocr_text": "用户手写识别结果",
      "standard": "东京",
      "correct": true,
      "suggested_ease": 3,
      "confidence": 0.9,
      "note": "可选：简短理由，如'错别字：京写成京'"
    }
  ]
}
```
- `confidence` < 0.6 的条目前端标黄，提示用户重点核对
- 未识别到的编号不出现；识别到但答案表里没有的，LLM 应标 `note: "编号不在答案表"`

## 后端 API（新增 1 个）

### `POST /api/session/<id>/ocr`
- **入参**：`multipart/form-data`，字段 `images`（一个或多个文件）
- **流程**：
  1. 校验 `PAPERBACK_OCR_API_KEY`（及 `PAPERBACK_OCR_BASE_URL`）已配置，否则返回 `{error: "no_key"}`
  2. 加载 session，构造答案表 `{index: card.back}`
  3. 对每张图：压缩长边 ≤ 2000px → base64 → 调视觉 LLM（图 + 答案表 + prompt）→ 解析 JSON → 收集 `cards`
  4. 把 `index` 映射回 `card_id`（session.cards 的索引 +1 = NNN）
  5. 原图存到 `~/.paperback/sessions/<id>/ocr/<ts>_<n>.jpg` 供结果页展示（留存 30 天，见下）
- **返回**：
  ```json
  {
    "results": [
      {"card_id": 123, "index": 1, "ocr_text": "...", "standard": "...",
       "suggested_ease": 3, "confidence": 0.9, "note": "...",
       "image_url": "/ocr_img/<session>/<ts>_0.jpg"}
    ],
    "errors": [{"image_index": 0, "reason": "api_timeout"}]
  }
  ```

**答案取哪个面**：Basic 卡默写"看正面写背面"，所以标准答案 = `card.back`。第一版固定用 back，后续可按 note type 配置。

### `GET /ocr_img/<session>/<file>`（新增，静态服务）
- 返回 `~/.paperback/sessions/<id>/ocr/<file>`，供结果页 `<img>` 展示
- 仅本机访问（127.0.0.1）

## 前端

### 1. 上传入口
- `grade` 页（或 session 概览页）加「📸 拍照批改」按钮
- 点开 → 上传页：`<input type="file" accept="image/*" capture="environment" multiple>`
- 手机优先；桌面端退化为选文件
- 上传中显示进度（多图逐个调 LLM，耗时几秒/张）

### 2. 结果确认页（核心交互）
逐卡列表，每卡一行：
```
#001  [缩略图]  你写: 東京        标准: 東京      [1][2][3✓][4]   confidence 0.95
#002  [缩略图]  你写: tokio       标准: Tokyo     [1][2✓][3][4]   confidence 0.7  ⚠ 核对
```
- `ocr_text` 可编辑（LLM 认错时用户改）
- 4 档按钮，高亮 `suggested_ease`；用户可改
- confidence < 0.6 标黄
- 底部「全部按建议提交」/「逐张提交」
- 提交 = 循环调现有 `POST /api/session/<id>/grade/<card_id>`（ease = 用户最终选定值）

### 3. 失败项处理
- `errors` 里的图：显示「该页识别失败，[重试] / [手动批改]」
- 编号对不上的卡：显示「未对齐，请手动选择 card_id 或跳过」

## 评分建议规则（写进 prompt）

| 情况 | suggested_ease |
|---|---|
| 拼写正确 | **3 good** — LLM 输出 |
| 拼写错 / 错别字 / 漏字 / 多字 / 没写 | **1 again** — LLM 输出 |
| 2 hard / 4 easy | 用户人工调整（LLM 不输出）|

> 注意：Paperback 默认 ease 仍是 `again(1)`（严格模式）。LLM 建议只是默认选中，用户可下调回 again。

## 隐私与成本

- **隐私**：图片发送到所配置的视觉 LLM provider（GLM/千问等）云端处理。首次使用时弹一次性提示：「照片将上传至 <provider> 用于识别，本地留存 30 天。确定继续？」
- **图片留存**：原图存 `~/.paperback/sessions/<id>/ocr/`，**保留 30 天**；应用启动时自动清理修改时间 > 30 天的文件。
- **成本估算**：GLM/千问视觉模型一页约 1500–2500 token，单页约 ¥0.03–0.08；一个 session 几页约 ¥0.1–0.3。国区 provider 通常较便宜。
- **key 缺失**：上传页显示「未配置 PAPERBACK_OCR_API_KEY / BASE_URL，请见 README」

## 错误处理

| 场景 | 处理 |
|---|---|
| `PAPERBACK_OCR_API_KEY` / `BASE_URL` 未配 | 上传页提示，不允许提交 |
| API 超时 / 限流 | 自动重试 2 次（间隔 1s/3s）；仍失败该图进 `errors` |
| LLM 返回非法 JSON | 尝试提取 `{...}` 修复一次；不行则该图标 `parse_error` |
| 编号读错 / 对不上答案表 | 该条进结果但 `card_id=null`，用户手动指定或跳过 |
| 图片过大 | 后端压缩到长边 ≤ 2000px 再发（控 token） |
| 手写极潦草 LLM 认不出 | `confidence` 低，标黄让用户核对 |

## 依赖与配置（新增）

- **Python 依赖**：**无新增**（复用现有 `requests`，走 OpenAI 兼容 HTTP）
- **环境变量**：
  - `PAPERBACK_OCR_API_KEY`（必填，provider 的 key）
  - `PAPERBACK_OCR_BASE_URL`（默认 `https://open.bigmodel.cn/api/paas/v4`，即 GLM）
  - `PAPERBACK_OCR_MODEL`（默认 `glm-4v-plus`）
  - `PAPERBACK_OCR_MAX_IMAGE_PX`（默认 2000，长边压缩阈值）
  - `PAPERBACK_OCR_RETAIN_DAYS`（默认 30，图片留存天数）

## 边界（第一版明确不做）

- ❌ 不做 provider 切换 UI（改 env 切换 GLM/千问，足够简单）
- ❌ 不做 BYOK 的 Web UI（key 只走环境变量）
- ❌ 不做图像纠偏/透视变换/二值化
- ❌ 不做 QR/ArUco 定位标记
- ❌ 不做"自动批量应用"（必须用户逐张或一次确认）
- ❌ 不做本地/开源 OCR（PaddleOCR 等作为 P2 备选）
- ❌ 不做 ocr_text 的二次校对模型
- ❌ 不支持跨 session 批量

## 验收标准

- [ ] 手机浏览器能调相机拍照上传（桌面端能选文件）
- [ ] 上传后 LLM 识别返回，结果页正确展示每卡
- [ ] 编号对齐准确率 ≥ 90%（用 Basic + 单词 deck 各测一页）
- [ ] 中英文手写识别可用（明显潦草的标低 confidence）
- [ ] 用户可改 ocr_text 与档位，提交后正确写回 Anki（reps/due 变化）
- [ ] key 缺失 / API 失败有清晰提示，不崩
- [ ] 隐私提示首次出现
- [ ] 图片大尺寸自动压缩
- [ ] 留存 > 30 天的图片在启动时被清理
- [ ] 切换 GLM ↔ 千问仅需改 env，不改代码

## 后续迭代（P2+）

- QR/ArUco 定位标记 → 提升对齐鲁棒性，支持裁剪单卡展示
- 本地 OCR（PaddleOCR）作为离线兜底
- 图像自动纠偏
- 批量自动应用（高 confidence 且全对的，一键全 good）
- 识别结果回填到 session，供复盘
