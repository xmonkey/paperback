# Paperback

基于 Anki 的默写工具：从指定 deck 取今日到期卡片，生成可打印默写卷，纸笔默写后手动批改，评分写回 Anki。

> 完整规格见 [SPEC.md](SPEC.md)

## 前置要求

1. **Anki 桌面版**处于运行状态
2. 已安装 **[AnkiConnect](https://foosoft.net/projects/anki-connect/)** 插件（默认端口 8765）
3. Python 3.10+（由 uv 自动获取）

## 快速开始

```bash
uv sync
uv run paperback          # 监听 http://127.0.0.1:8000
```

浏览器打开 <http://127.0.0.1:8000>

## 使用流程

1. 首页选 deck + 数量（可勾选「过滤默写卡片」，默认勾选，排除背面是中文释义的卡）→ 生成默写卷
2. 在 session 页：
   - 打印「**默写卷**」，在纸上默写
   - 需要时打印「答案卷」对照
3. 批改（二选一）：
   - **手动批改**：「开始批改」→ `Space` 显示答案 → `1`–`4` 评分（`Enter` 默认 **1 重来**）
   - **拍照批改**：手机拍默写卷上传 → GLM 视觉识别 + 自动建议档位 → 确认后写回（需配置，见下）
4. 评分实时写回 Anki；网络中断会缓存待补交，恢复后可一键重试

## 支持的卡片类型

直接使用 Anki 已渲染好的卡片正反面（AnkiConnect 的 `question`/`answer`），**支持任意 note type**：

| 类型 | 支持 | 说明 |
|---|---|---|
| Basic / Basic (and reversed card) | ✅ | 正反向卡都正确（Anki 已处理模板方向）|
| Cloze | ✅ | 自定义挖空：默写卷下划线、答案卷高亮 |
| 自定义模板（单词 deck 等） | ✅ | 只要 Anki 能渲染正反面即可 |
| Image Occlusion 等图片型 | ✅ 技术上支持 | 但遮挡类不一定适合默写，由用户判断 |

> 若极少数卡的正面渲染为空，生成时跳过并在概览页提示数量。

## 评分档位

| 键 | 档 | Anki ease |
|---|---|---|
| `1` | 重来 again | 1 |
| `2` | 困难 hard | 2 |
| `3` | 记得 good | 3 |
| `4` | 轻松 easy | 4 |

默认评分 = **1 重来**（严格模式：除非主动确认，否则按"不会"处理）。

## 拍照批改（OCR，可选）

手机拍默写卷上传，调用视觉 LLM（默认 GLM `glm-5v-turbo`）识别手写内容并比对标准答案，给出建议档位；你确认后写回 Anki。**LLM 只建议，人最终拍板。**

**配置**：走 OpenAI 兼容协议，改 env 可切 provider。

```bash
# GLM（智谱，默认 · 推荐）
export PAPERBACK_OCR_API_KEY=你的智谱 key
# 以下可选，省略即用默认值：
# export PAPERBACK_OCR_BASE_URL=https://open.bigmodel.cn/api/paas/v4
# export PAPERBACK_OCR_MODEL=glm-5v-turbo
```

> **为何推荐 GLM 而非千问**：实测 GLM 按手写原样识别、不纠拼写；千问 `qwen-vl-max` 有幻觉——会把拼错的词（如 `capable`）自动补全/纠正成标准答案（`be capable of doing sth.`）并判对，掩盖拼写错误，默写场景不可用。

配置后重启 Paperback，session 概览页会出现「📸 拍照批改」按钮。

**流程**：选图/拍照（可多张）→ 后端 EXIF 正向化 + 压缩到长边 2000px → 调 GLM → 逐卡展示（缩略图 + 你写 + 标准 + 建议档位，可逐张改）→「全部按建议提交」→ 写回 Anki。

**隐私**：照片会上传至所配置的 provider 云端识别，原图本地留存 365 天后自动删除。不配置 key 则 OCR 入口不显示，不影响其他功能。

## 安全性

- 仅监听 `127.0.0.1`，不暴露网络
- 批改前校验 Anki 当前 profile/deck 与生成时一致，不一致则拦截（防写错 profile）
- session 文件原子写 + 进程内锁，防多页签并发覆盖

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PAPERBACK_ANKI_URL` | `http://localhost:8765` | AnkiConnect 地址 |
| `PAPERBACK_DATA_DIR` | `~/.paperback/sessions` | session 存储目录 |
| `PAPERBACK_OCR_API_KEY` | （无） | **拍照批改必填**：视觉 LLM provider 的 API key |
| `PAPERBACK_OCR_BASE_URL` | `https://open.bigmodel.cn/api/paas/v4` | OpenAI 兼容端点（默认 GLM 智谱） |
| `PAPERBACK_OCR_MODEL` | `glm-5v-turbo` | 视觉模型名 |
| `PAPERBACK_OCR_MAX_IMAGE_PX` | `2000` | 上传前长边压缩阈值（控 token） |
| `PAPERBACK_OCR_RETAIN_DAYS` | `365` | 原图本地留存天数（启动时自动清理过期） |

## 重启后恢复

系统重启后，代码 / 依赖 / session 数据都在磁盘，不丢。只需重启两个进程：

1. **手动启动 Anki 桌面版**，打开你要默写的 profile（确认 AnkiConnect 插件在运行，默认 8765）
2. **启动 Paperback 服务**：
   ```bash
   cd /Users/xiaobin/project/anki/paperback
   uv run paperback          # http://127.0.0.1:8000
   ```
3. 浏览器打开 <http://127.0.0.1:8000>

想先确认环境没坏，可跑一遍测试（应 44 passed）：
```bash
uv run pytest
```

## 测试

```bash
uv run pytest
```

## 技术栈

FastAPI · Jinja2 · 原生 JS · requests · uv（包管理）。无前端框架、无构建工具。
