# Friday 品牌

应用名称：Friday。Logo 为黑白极简机器人脸，只保留两只斜切的白色眼睛与一枚微微上挑的嘴角。眼睛外缘略圆、内缘利落，表情冷静、自信，带一点桀骜。黑色背景、大面积留白，适配项目的黑色界面。

通过内置 imagegen 生成，最终原图保存在 `local_ai/pwa/friday-icon.png`。使用 macOS sips 仅导出尺寸，不改变画面设计：PWA 192/512 PNG 和 Apple Touch Icon 180 PNG。页面头像与主屏幕图标来自同一张原图。`icon.svg` 为内嵌 192 PNG 的兼容包装，不是矢量源文件。

Service Worker 公共资源缓存版本已更新为 `harness-public-v15`，新版本激活后更新缓存中的图标。此前的彩色 F 原图及设计说明保存在 `docs/brand/friday-icon-previous.png` 和 `docs/brand/friday-brand-previous.md`。

## 最终生成提示词

```text
Use case: logo-brand. Create a finished square logo for Friday, an AI assistant. User brief: a very cool handsome robot face, extremely simple, just eyes and mouth, black and white, strong graphic design. One single centered mark on edge-to-edge pure black #000000 opaque background. EXACTLY THREE disconnected pure white graphic shapes: two boldly cut narrow angular eyes and one small confident angular smirk below. The eyes are horizontally elongated, subtly inward sloping with refined softened outer corners, robotic and poised, cool rather than furious. The mouth is a short geometric off-center smirk, deliberately designed, not teeth and not a big cheerful smile. Evoke a calm intelligent robot with a little attitude using only these eyes and mouth, no face outline or head silhouette at all. Optically balanced, crisp flat vector-like shapes, high-end minimal identity, generous negative space. Overall face occupies about 58 percent of canvas width and 33 percent of canvas height, centered optically. Bold enough to read at 24 pixels. Only black and white; no gray fills, no gradients, glow, shading, outlines, metal, 3D, texture, framing, antenna, nose, eyebrows, ears, helmet, letters, text, watermark or extra decorative details. One final logo only, no option grid or mockup.
```
