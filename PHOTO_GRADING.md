# 拍照批改（OCR）· 测试中

> ⚠️ **此功能仍在测试，识别准确率和体验未达预期，暂不推荐日常使用。**手动批改更可靠。以下供尝鲜 / 反馈用。
>
> 返回主项目：[Paperback · Anki 默写本](README.md)

手机拍默写卷上传 → 视觉 LLM（默认 GLM `glm-5v-turbo`）识别手写 + 比对标准答案 → 给建议档位 → 你确认后写回 Anki。**LLM 只建议，人最终拍板。**

## 配置（网页 `/settings` 页）

首页点「⚙ OCR 设置」→ 选 provider 预设（GLM 智谱 / 通义千问 / 自定义）→ 填 API key → 「测试连接」→ 保存。配置存 `~/.paperback/config.json`，**立即生效无需重启**。也支持环境变量 `PAPERBACK_OCR_*` 作 fallback（网页配置优先）。

## 为何默认 GLM

实测 5 个视觉模型（详见 [SPEC_OCR.md](SPEC_OCR.md) §模型选型对比）：

| 模型 | 默写场景 |
|---|---|
| **GLM `glm-5v-turbo`**（默认） | ✅ 按手写原样识别、不纠拼写 |
| 千问 `qwen-vl-max` | ❌ 幻觉（把 `capable` 补成 `be capable of doing sth.` 判对）|
| GLM-OCR | ❌ 自动纠拼写（`consolde→console`）|
| GLM-4.1V-Thinking | ⚠️ 识别「选择性纠正」不可预测 |

默写要「原样识别」，模型越聪明越倾向纠正拼写，反而把错的判对。GLM 的"笨"是稀缺优势。

## 流程

选图/拍照（可多张）→ 后端 EXIF 正向化 + 压缩到长边 2000px → 调 GLM → 逐卡展示（缩略图 + 你写 + 标准 + 建议档位，可逐张改）→「全部按建议提交」→ 写回 Anki。同一 session 重复拍照时，已批改的卡自动标灰跳过（防重复写回）。

## 隐私

照片上传至所配置的 provider 云端识别，原图本地留存 365 天后自动删除。不配 key 则 OCR 入口不显示，不影响其他功能。
