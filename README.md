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

1. 首页选 deck + 数量 → 生成默写卷
2. 在 session 页：
   - 打印「**默写卷**」，在纸上默写
   - 需要时打印「答案卷」对照
3. 点「开始批改」：
   - `Space` 显示答案
   - `1`–`4` 评分（`Enter` 默认 **1 重来**）
4. 评分实时写回 Anki；网络中断会缓存待补交，恢复后可一键重试

## 支持的卡片类型

| 类型 | 支持 | 说明 |
|---|---|---|
| Basic | ✅ | 第一个字段为正面，第二个为背面 |
| Cloze | ✅ | 自动挖空：默写卷下划线、答案卷高亮 |
| 其他（Image Occlusion 等） | ⏭ 跳过 | 避免答案泄露到正面；session 概览显示跳过数量 |

> 注：`Basic (and reversed card)` 的反向卡暂不支持（`cardsInfo` 不返回模板序号，无法可靠判断正反面）。

## 评分档位

| 键 | 档 | Anki ease |
|---|---|---|
| `1` | 重来 again | 1 |
| `2` | 困难 hard | 2 |
| `3` | 记得 good | 3 |
| `4` | 轻松 easy | 4 |

默认评分 = **1 重来**（严格模式：除非主动确认，否则按"不会"处理）。

## 安全性

- 仅监听 `127.0.0.1`，不暴露网络
- 批改前校验 Anki 当前 profile/deck 与生成时一致，不一致则拦截（防写错 profile）
- session 文件原子写 + 进程内锁，防多页签并发覆盖

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PAPERBACK_ANKI_URL` | `http://localhost:8765` | AnkiConnect 地址 |
| `PAPERBACK_DATA_DIR` | `~/.paperback/sessions` | session 存储目录 |

## 重启后恢复

系统重启后，代码 / 依赖 / session 数据都在磁盘，不丢。只需重启两个进程：

1. **手动启动 Anki 桌面版**，打开你要默写的 profile（确认 AnkiConnect 插件在运行，默认 8765）
2. **启动 Paperback 服务**：
   ```bash
   cd /Users/xiaobin/project/anki/paperback
   uv run paperback          # http://127.0.0.1:8000
   ```
3. 浏览器打开 <http://127.0.0.1:8000>

想先确认环境没坏，可跑一遍测试（应 22 passed）：
```bash
uv run pytest
```

> 说明：对话窗口重启后会关闭，但项目状态（架构决策、启动命令、已知约束）已记在 Claude 记忆里，新会话说一声"继续 paperback"即可接上。

## 测试

```bash
uv run pytest
```

## 技术栈

FastAPI · Jinja2 · 原生 JS · requests · uv（包管理）。无前端框架、无构建工具。
