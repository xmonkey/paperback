# 默写卷排版选项（v1，2026-06-26）

> worksheet 页可调排版，打印前实时预览。参考 Papercards addon（2042118948）的「可配置」思路（不照搬网格布局——Paperback 是行式默写卷）。

## 排版维度（3 个）

| 维度 | 选项 | 默认 | 实现 |
|---|---|---|---|
| 字号 | 紧凑(0.85) / 标准(1) / 宽松(1.15) | 标准 | CSS 变量 `--pb-font-scale`，作用于 `.sheet` 的 font-size |
| 方向 | 纵向 / 横向 | 纵向 | JS 动态改 `<style id="pb-page-style">` 的 `@page size` |
| 列数 | 单列 / 双列 | 单列 | CSS 变量 `--pb-cols`，`.sheet` 用 `grid-template-columns` |

填空区固定 1 行（曾考虑可配，已移除——默认够用）。

## UI
worksheet 页顶部 no-print 区，一行下拉（行数/字号/方向）。改完 JS 实时重渲染（无需重新生成 session）。打印时不显示控件。

## 存储
`localStorage`，**按 deck 持久化**（键 `pb_layout_<deck>_font` / `_orient` / `_cols`）——不同 deck 各自记住排版偏好。deck 名经 `encodeURIComponent` 处理。

## 不做
- ❌ **纸张选择（A4/Letter）** —— 浏览器打印对话框已能选且更权威，网页 `@page size` 只是建议会被覆盖；国区 A4 主流，无意义
- ❌ 网格 flashcard 布局（Papercards 那种裁切折叠）—— 和行式默写卷定位不同
- ❌ 卡片边框/颜色样式 —— 锦上添花

## 技术细节
- `@page size` 用 CSS 变量在 @page 里浏览器支持有限 → 改用 JS 设置独立 `<style id="pb-page-style">` 标签的 textContent
- `.blank` 固定 1 个 `<div class="line">`（模板静态，`.line` 的 border-bottom 横线样式在 base.html.j2）
- 字号用 CSS 变量 `--pb-font-scale`（普通元素变量支持 OK，`.sheet .card` 继承）
- deck 名经 `encodeURIComponent` 后拼进 localStorage key，避免空格/特殊字符问题
