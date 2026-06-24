# Paperback 产品规格说明书

> 基于 Anki 的默写工具。从指定 deck 取今日到期卡片，生成可打印默写卷，纸笔默写后手动批改，评分写回 Anki。

---

## 1. 概述

### 1.1 定位
Paperback 是 Anki 的配套默写工具。Anki 的复习模式是"看题→回想→翻面→自评"，而默写要求"把答案写出来"——这是一种更严格的回忆检验，特别适合单词拼写、古诗词、公式、定义等需要精确产出的内容。

### 1.2 解决的问题
- Anki 本身没有"默写"模式，只能靠心算自评
- 手动整理默写材料费时
- 默写结果难以反馈到 Anki 的调度系统

### 1.3 设计原则
- **不重新发明调度**：复习间隔、ease 因子全部交给 Anki 的 SM-2 算法，Paperback 只负责"采集默写信号 → 翻译成 Anki 评分"
- **纸笔优先**：默认产出可打印的默写卷，贴近真实默写场景，也为后续拍照识别留接口
- **轻量**：单机 Web 应用，无认证、无数据库、无前端框架
- **Anki 内容信任**：卡片 HTML 直接渲染，因为来源是用户自己的 Anki

---

## 2. 典型使用流程

```
1. 启动 Anki 桌面版（确保 AnkiConnect 插件运行在 8765）
2. 启动 Paperback：uvicorn paperback.main:app --port 8000
3. 浏览器打开 http://127.0.0.1:8000
4. 首页选择 deck → 点"生成默写卷"
5. 在 session 页：
   - 打印"默写卷"，纸上默写
   - 需要对照时打印"答案卷"
6. 打开"批改"页，逐张评分（1/2/3/4）
7. 评分实时写回 Anki
8. 完成后看统计
```

---

## 3. 功能规格

### 3.1 选 Deck
- 首页展示 Anki 中所有 deck（调 `deckNames`）
- 下拉选择 + limit 输入（默认 20，最大 100）
- 提交后进入 session 页

### 3.2 生成默写卷
- 查询条件：`"deck:<选中> is:due"`（今日到期）
- 通过 `findCards` 拿 cardId 列表，`cardsInfo` 拿详情
- 用 Anki 渲染好的正反面，**任意 note type 均支持**（见 §4.1）；`question` 为空的卡才跳过
- **过滤默写卡片**（首页 checkbox，默认勾选）：排除背面（answer）含中文汉字的卡，只保留答案是英文单词/词组的卡。判断用 CJK 范围 `[\u4e00-\u9fff]`，不含中文标点（避免误伤英文词组误用中文标点）。取消勾选则保留全部卡片。
- 截断到 limit
- 生成 session（持久化到 `~/.paperback/sessions/<id>.json`）
- session 页提供三个入口：默写卷、答案卷、批改；极少数渲染为空的卡会被跳过并显示数量

**无到期卡片时（重要）**：
- `findCards` 返回空 → **不创建 session**，重定向回首页 `/?error=no_due&deck=<urlencode>`
- 首页检测到 `error=no_due`，在表单上方显示提示框：
  - `📭 deck「<deck>」今日无到期卡片`
  - 建议文案："今天没有需要默写的卡片。可以选其他 deck，或明天再来。"
  - 附带"查看全部 deck"链接（滚动到下拉框）

### 3.3 默写卷（Worksheet）
- **用途**：打印后纸笔默写
- **布局**：每张卡片一行，左侧编号 + 正面，右侧填空区（3 条横线）
- **编号**：`#001` 起始，对应 session 中卡片顺序（索引+1）
- **打印**：A4、`page-break-inside: avoid` 避免跨页、`@media print` 隐藏导航
- **不显示**：背面答案

### 3.4 答案卷（Answer Key）
- **用途**：批改时对照
- **布局**：与默写卷相同的编号顺序，但显示正面 + 背面
- **可打印**：同默写卷

### 3.5 批改（Grade）
- **核心交互页**，详见 §6
- 用户对照纸面默写和屏幕答案，逐张给出 4 档评分
- 每次评分实时写回 Anki（`answerCards`）
- 支持刷新/断点续传（已评过的跳过）

### 3.6 评分写回 Anki
- 评分映射：1=again, 2=hard, 3=good, 4=easy（直接对应 AnkiConnect ease）
- 写回后 Anki 自动更新卡片的下次到期时间、复习状态、ease 因子

---

## 4. 数据模型

### 4.1 Card
```python
@dataclass
class Card:
    card_id: int      # Anki 卡片 ID（写回时用）
    front: str        # 正面 HTML（直接渲染）
    back: str         # 背面 HTML
    deck: str
    note_type: str    # Basic / Cloze / ...
```

**正反面来源**（`cardsInfo` 同时返回字段原始值与已渲染好的 `question`/`answer`）：

> **任意 note type 都支持**：直接用 Anki 已渲染好的 `question`/`answer` 作为正反面，无需字段映射。Anki 已处理模板方向（正反向卡都正确）与模板逻辑（含 Basic reversed、自定义单词 deck、G5 卡片等）。防答案泄露同样达成——`question` 本身就是正面，不含答案。

- **非 Cloze**：`front` = `question` 剥离 `<style>` 后的内容；`back` = `answer` 剥离 `<style>` 后、按 `<hr id=answer>` 分割取后半（无 `hr` 则整体）
- **跳过条件**：`question` 渲染为空的卡（极少见）跳过，概览页提示数量
- **Cloze 类型**：对 `Text` 字段做挖空解析，生成两份 HTML
  - 正则匹配 `{{c\d+::([^:]*?)(?:::([^}]*?))?}}`，捕获组 1 = 答案，捕获组 2 = 可选提示
  - `front`（挖空版）：替换为 `<span class="cloze-blank"></span>`（一条下划线）；有提示则前置 `<span class="cloze-hint">(提示)</span>`
  - `back`（答案版）：替换为 `<span class="cloze-answer">答案</span>`，高亮显示
  - **一张卡片的所有 cloze deletion 都挖空**（不区分 c1/c2 对应哪张卡），用户一次性默写全部空
  - 若 `Extra` 字段存在，追加到 `back` 末尾
- **Cloze 样式**（全局 CSS，见 §7.4）：
  - `.cloze-blank` → 内联下划线块（`min-width: 6em; border-bottom: 2px solid currentColor`）
  - `.cloze-hint` → 灰色斜体提示
  - `.cloze-answer` → 黄底加粗高亮

### 4.2 Session
存于 `~/.paperback/sessions/<id>.json`：
```json
{
  "id": "20260623_143000",
  "deck": "日语N3",
  "created_at": "2026-06-23T14:30:00",
  "cards": [
    {"card_id": 123, "front": "...", "back": "...", "note_type": "Basic"},
    ...
  ],
  "graded": {"123": 3, "124": 1},
  "pending": {
    "125": {"ease": 3, "ts": "2026-06-23T14:35:00", "attempts": 2}
  }
}
```
- `id`：生成时间戳 `YYYYMMDD_HHMMSS`
- `graded`：**已成功写回 Anki** 的评分，`{card_id_str: ease}`，支持断点续传
- `pending`：**尚未成功写回**的评分（网络失败缓存），含重试次数 `attempts`；写回成功后从此处移除并转入 `graded`

**并发写保护（防多页签 race condition）**：
- 所有 session 的读-改-写操作经 `threading.Lock` 串行化
- 文件写入用**原子写**：先写 `<id>.json.tmp`，再 `os.replace` 覆盖（避免半写损坏）
- 第一版默认 uvicorn 单 worker，进程内锁足够；多 worker 部署需改用文件锁（P2）

---

## 5. API 规格

### 5.1 AnkiConnect 调用（封装在 `anki_connect.py`）

所有调用 POST `http://localhost:8765`，body：
```json
{"action": "<action>", "version": 6, "params": {...}}
```
响应：`{"result": <data>, "error": null}`，error 非 null 时抛异常。

| 方法 | action | params | 返回 |
|---|---|---|---|
| `decks()` | `deckNames` | `{}` | `["Default", "日语N3", ...]` |
| `due_card_ids(deck, limit)` | `findCards` | `{"query": "deck:<x> is:due"}` | `[123, 456, ...]` 截断到 limit |
| `cards_info(ids)` | `cardsInfo` | `{"cards": [ids]}` | 解析为 `List[Card]` |
| `answer_cards(grading)` | `answerCards` | `{"answers": [{"cardId": id, "ease": e}]}` | `List[bool]` 每张是否成功 |

### 5.2 Web 路由

| 方法 | 路径 | 入参 | 行为 |
|---|---|---|---|
| GET | `/` | — | 渲染首页（deck 列表） |
| POST | `/generate` | form: `deck`, `limit` | 查卡片、建 session、重定向到 `/session/<id>` |
| GET | `/session/<id>` | path: id | session 概览页 |
| GET | `/session/<id>/worksheet` | path: id | 默写卷（可打印） |
| GET | `/session/<id>/answerkey` | path: id | 答案卷（可打印） |
| GET | `/session/<id>/grade` | path: id | 批改交互页 |
| POST | `/api/session/<id>/grade/<card_id>` | path: id, card_id; json: `ease` | 调 `answerCards` 写回，更新 session.graded，返回 `{ok, remaining, pending}` |
| POST | `/api/session/<id>/flush` | path: id | 批量重试 `session.pending` 中的评分，返回 `{flushed: n, still_pending: m}` |

**错误处理**：
- AnkiConnect 连不上 → 首页显示提示"请先启动 Anki 桌面版"
- session 不存在 → 404
- ease 不在 {1,2,3,4} → 400
- `POST /generate` 时 deck 无到期卡片 → 重定向 `/?error=no_due&deck=<x>`（见 §3.2）
- 单次 `grade` 写回失败 → 后端内部重试 3 次（见 §6.4），仍失败则缓存到 `pending` 并返回 `{ok: false, pending: true}`，前端不阻塞、继续下一张

---

## 6. 批改页规格（核心）

### 6.1 状态机
```
                 ┌──────────────────────────┐
   初始 ────────▶│ 状态 A：只显示正面        │
                 │  [显示答案] (Space)       │
                 └──────────┬───────────────┘
                            │ Space
                            ▼
                 ┌──────────────────────────┐
                 │ 状态 B：显示正面 + 背面   │
                 │  [1 again][2 hard]        │
                 │  [3 good][4 easy]         │
                 │  进度: 3/15              │
                 └──────────┬───────────────┘
                            │ 1/2/3/4
                            ▼
                    POST 写回 → 下一张 → 状态 A
                            │
                   （最后一张）
                            ▼
                 ┌──────────────────────────┐
                 │ 完成页：各档数量统计       │
                 └──────────────────────────┘
```

### 6.2 交互细节
- 卡片数据一次性嵌入页面 JSON，前端原生 JS 控制，避免逐张请求
- **快捷键**：
  - `Space`：A ↔ B 切换
  - `1` / `2` / `3` / `4`：评分（仅 B 状态有效）
  - `Enter`：默认 `1` (again)
- 评分后立即 `fetch POST` 写回，按钮短暂禁用避免误连击
- 已评分卡片（session.graded 中存在）跳过，从第一张未评的开始
- 全部完成显示统计：again/hard/good/easy 各几张

### 6.3 默认值策略
- 第一版**不自动判对错**
- **默认评分 = `again(1)`**（严格模式：除非用户主动按 2/3/4 确认"写对"，否则回车按"重来"处理）。这对默写这种要求精确产出的场景更合理，避免无脑默认"会了"导致调度虚高
- 用户判断完全由自己点按钮决定
- 设计预留：未来接入 OCR/LLM 时，可在状态 B 多一个"建议评分"提示，用户可采纳或覆盖

### 6.4 容错与网络重试（关键健壮性）

**单次写回的自动重试**（在 POST `/api/.../grade/<card_id>` 内部）：
1. 调 `answerCards`，失败（连接错误 / HTTP 5xx / error 字段非 null）→ 重试
2. 指数退避：0.5s → 1s → 2s，最多 **3 次**
3. 仍失败 → 评分写入 `session.pending`（含 `attempts` 计数），返回 `{ok: false, pending: true}`
4. 前端收到 `pending: true` 时：
   - 在进度条旁显示橙色徽标 `⚠ N 张待补交`
   - **不阻塞**，直接进入下一张（用户默写节奏不被打断）

**区分失败原因（避免无效重试）**：
- **连接/网络错误**（连不上 AnkiConnect / 超时）→ 进 `pending`，提示"已缓存待重试，请确认 Anki 正在运行"
- **卡片不存在**（AnkiConnect 返回该 card false 或"card not found"）→ **不进 pending**（重试无用），标记为"卡片已失效（可能在 Anki 中被删除/同步/切换 profile）"，直接跳过并在统计页单列

**离线缓存的批量补交**：
- 每次 `grade` 成功写回新评分后，后端顺带 flush 一次 `pending`（仅一次尝试，避免拖慢响应）
- 批改完成统计页显示 `已写回 X / 待补交 Y`，若 Y > 0 提供 `[立即重试全部]` 按钮 → 调 `/api/session/<id>/flush`
- session 概览页若 `pending` 非空，顶部红色提示条："上次有 Y 张评分未写回 Anki，[重试]"

**断点续传**：
- 刷新批改页 → 跳过 `session.graded` 中已存在的卡片，从下一张继续
- `pending` 中的卡片视为未完成，刷新后仍会重新出现（因为还没成功写回）

**边界**：
- `pending.attempts` 超过 10 次仍失败 → 概览页提示"建议检查 Anki 是否运行"，不再无限重试
- 不提供"撤销上一张"（已写回 Anki，撤销需另调接口；第一版不做）

### 6.5 Profile / Deck 一致性校验（数据安全）

AnkiConnect 操作的是 Anki 当前打开的 profile。若用户生成 session 后在 Anki 端切换了 profile，写回会发到错误的 profile —— card_id 跨 profile 碰撞虽罕见，但一旦发生会把评分写进无关卡片，**数据错乱不可逆**。

**校验机制**：
- session 记录生成时的 `deck` 名（已有字段）
- **批改开始时**（打开 `/session/<id>/grade`）一次性校验所有卡片：对每张 card 调 `cardsInfo(card_id)`，检查返回的 `deckName == session.deck`
- **不一致** → 阻止批改，显示红色提示：
  - "⚠ 检测到 Anki 当前 profile 与生成此默写卷时不一致（卡片 `deckName` 不匹配）"
  - "请在 Anki 中切回对应 profile，或重新生成默写卷"
  - 不提供"强行写回"选项（安全优先）
- **单张写回失败时**也顺带做一次该校验，防止批改中途用户切了 profile

**为何不用 profile 名**：AnkiConnect 无稳定的"获取当前 profile 名"接口，而 `deckName` 校验更直接可靠（profile 切换后 deck 通常不存在或不同）。

**性能**：批改开始的批量校验走单次 `cardsInfo(所有 id)`，复用已有数据，几乎零额外开销。

---

## 7. 默写卷排版规格

### 7.1 布局
```
┌────────────────────────────────────────────┐
│ #001  正面内容（HTML 渲染）    │ 填空区    │
│                       ──────────────────── │
│                       ──────────────────── │
│                       ──────────────────── │
├────────────────────────────────────────────┤
│ #002  ...                                  │
└────────────────────────────────────────────┘
```
- 左右比例：约 6:4（正面 60%，填空 40%）
- 填空区默认 1 条 `border-bottom` 横线
- 编号字体加粗，左上角
- 卡片之间分隔线

### 7.2 打印 CSS
```css
@media print {
  @page { size: A4; margin: 15mm; }
  .no-print { display: none; }
  .card { page-break-inside: avoid; }
}
```

### 7.3 屏幕模式
- 顶部显示 deck 名、卡片数、"打印此页"按钮（`window.print()`）
- 屏幕预览与打印一致

### 7.4 Cloze 内容样式
全局 CSS（默写卷、答案卷、批改页共用）：
```css
.cloze-blank { display: inline-block; min-width: 6em; border-bottom: 2px solid currentColor; vertical-align: bottom; }
.cloze-hint { color: #888; font-style: italic; margin-right: 4px; }
.cloze-answer { background: #fff3a0; padding: 0 4px; border-radius: 3px; font-weight: 600; border: 1px solid #d4ab00; }
```
- `.cloze-answer` 加 `border` 兜底，防止打印时黄底丢失
- 打印 CSS 中可保留 `.cloze-answer` 高亮（答案卷需要）

### 7.5 超长内容策略
Anki 卡片可能含长段落、大图、多空，需避免破坏排版：

**全局内容约束**：
```css
.card-content img { max-width: 100%; height: auto; }
.card-content { overflow-wrap: break-word; word-break: break-word; }
.card-content pre, .card-content table { max-width: 100%; overflow-x: auto; }
```

**长卡判定与处理**（第一版按字符数估算，渲染后高度精确判定留到 P2）：
- 阈值：`front` 或 `back` 的去标签纯文本 > **500 字符**，或含 `img`，视为长卡
- **默写卷 / 答案卷**：
  - 长卡解除 `page-break-inside: avoid`（改为 `auto`），允许跨页，避免被挤出留大量空白
  - 卡片角标加 `⚠ 长卡` 提示
  - 填空区固定 3 行不变（用户已确认）；角标追加"内容较多，可另附纸"
- **批改页**：
  - 卡片内容区 `max-height: 60vh; overflow-y: auto`，超出滚动
  - 滚动容器底部加渐变遮罩提示"下方还有内容"

**异常巨大卡片兜底**（> 5000 字符）：
- 默写卷仍完整渲染（不截断，避免丢内容），但该卡可能占满整页
- 控制台 / session 日志记录该 cardId，便于后续优化

---

## 8. 非功能需求

### 8.1 安全与启动校验
- FastAPI 仅监听 `127.0.0.1`，不暴露到网络
- 卡片 HTML 用 Jinja2 `|safe` 渲染（内容来自用户自己的 Anki，可信）
- 不做用户认证（单用户本机工具）
- **启动时校验数据目录**：检查 `PAPERBACK_DATA_DIR`（默认 `~/.paperback/`）可创建且可写（尝试 mkdir + 写一个测试文件），失败则启动时在首页明确提示"数据目录不可写：<path>，请检查权限或设置 PAPERBACK_DATA_DIR"，而非崩溃

### 8.2 依赖
- Python 3.10+
- `fastapi`、`uvicorn`、`requests`、`jinja2`
- 前端：Pico.css（CDN 或本地）+ 原生 JS，不引入构建工具

### 8.3 配置
- AnkiConnect 地址默认 `http://localhost:8765`，可通过环境变量 `PAPERBACK_ANKI_URL` 覆盖
- session 目录默认 `~/.paperback/sessions/`，可通过 `PAPERBACK_DATA_DIR` 覆盖
- 写回重试次数默认 `3`，退避序列 `0.5s,1s,2s`，可通过 `PAPERBACK_GRADE_RETRIES`、`PAPERBACK_GRADE_BACKOFF` 覆盖
- 长卡字符阈值默认 `500`，可通过 `PAPERBACK_LONG_CARD_CHARS` 覆盖

---

## 9. 边界（第一版明确不做）

- ❌ 不直接读写 Anki 的 `.apkg` / `.anki2` 文件，只走 AnkiConnect
- ❌ 不引入前端框架（React/Vue）和构建工具
- ❌ 不做用户认证、多用户
- ❌ 不自动判对错（OCR、LLM 都是后续迭代）
- ❌ 不做"撤销已写回的评分"
- ❌ 不重新设计 Anki 调度算法，完全复用 SM-2

### 9.1 经评审确认的设计取舍（非缺陷）
以下两项在评审中被提出，经分析**无需处理**，记录在此避免重复讨论：

- **`cardsInfo` 批量性能**：limit 默认 20、本机调用、返回纯文本 HTML（图片是 `<img src>` 引用非内联），单次通常 < 1s。仅在 `generate` 前端加 loading 动画即可，无需分批查询。
- **`answerCards` 逐张写回**：这是**有意设计**，非性能问题。本机 localhost RTT < 5ms，无网络瓶颈；逐张写回是为支持断点续传、pending 缓存、随时停止。AnkiConnect 的批量能力已用于 `flush` pending。

---

## 10. 后续迭代路径

| 优先级 | 迭代项 | 备注 |
|---|---|---|
| P1 | 拍照上传 + OCR 识别 | 默写卷编号体系让定位容易 |
| P1 | LLM 语义判定 + 规则混合的自动批改 | 给评分建议，用户确认 |
| P2 | 默写卷排版选项（每卡一页、双栏、横线密度） | |
| P2 | Cloze 按卡片粒度区分挖空 | 当前一张卡片挖所有空；按 cardId 对应的 cN 精准挖空 |
| P2 | 多 worker 文件锁 | 当前单 worker 用 threading.Lock；多 worker 需 fcntl 文件锁 |
| P3 | 配置持久化、deck 收藏、统计面板 | |
| P3 | 多 deck 批量默写 | |

---

## 11. 验收标准（第一版）

- [ ] 首页能列出 Anki 全部 deck
- [ ] 选中 deck 生成 session，卡片数为今日到期数（受 limit 限制）
- [ ] 默写卷打印预览：A4、不跨页、正面/填空左右排布、Anki HTML 正确渲染
- [ ] 答案卷显示正面 + 背面，编号与默写卷一致
- [ ] Cloze 卡片：默写卷上挖空显示（含提示），答案卷上高亮显示答案
- [ ] 批改页：空格切换答案，1/2/3/4 评分，Enter 默认 again
- [ ] 评分后 Anki 桌面版能看到卡片下次到期时间被更新
- [ ] 刷新批改页能恢复进度（已评过的跳过）
- [ ] **无到期卡片**：选空 deck 后回首页显示明确提示，不创建空 session
- [ ] **长卡排版**：超长卡片在默写卷允许跨页、批改页可滚动，不撑破布局
- [ ] **网络中断重试**：批改中关掉 Anki → 评分缓存到 pending、不阻塞；重启 Anki 后点"重试"能成功写回
- [ ] **Profile 一致性**：生成 session 后切到别的 Anki profile，再批改时被拦截并提示，不会误写
- [ ] **任意 Note Type 支持**：Basic reversed / 自定义单词 deck 等都能生成默写卷，正反向正确、答案不泄露到正面
- [ ] **并发写保护**：同 session 开两个批改页签，评分不会互相覆盖丢失
- [ ] **失效卡片**：批改中途在 Anki 删除某卡，该卡被标记"已失效"跳过，不进无限 pending
- [ ] AnkiConnect 连不上时首页有清晰提示
- [ ] `pytest` 通过（mock AnkiConnect）
