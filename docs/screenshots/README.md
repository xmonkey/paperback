# 截图

供主 README 引用。截图建议（macOS 可用 `Cmd+Shift+4` 区域截图，或浏览器开发者工具的「Capture full page」）：

| 文件 | 页面 | 截图状态建议 |
|---|---|---|
| `index.png` | 首页 `/` | 选了某 deck、显示历史 session 列表 |
| `worksheet.png` | 默写卷 `/session/<id>/worksheet` | 顶部排版控件可见，展示 3–4 张卡 |
| `grade.png` | 批改页 `/session/<id>/grade` | 一张卡 + 答案已显示 + 4 档按钮 + 进度条 |
| `ocr.png` | OCR 结果页 `/session/<id>/ocr` | 提交照片后，几张卡的识别结果 + 建议档位 |
| `settings.png` | OCR 设置 `/settings` | GLM 预设选中、base_url/model 已填、has_key 状态 |

截图后：

```bash
git add docs/screenshots/*.png
git commit -m "docs: add screenshots"
```

> 建议每张 PNG 控制在 200KB 内（裁剪关键区域 + 图片压缩工具如 ImageOptim），避免仓库膨胀。
