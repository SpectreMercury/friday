# Friday 品牌

应用名称：Friday。Logo 为黑白极简机器人脸，只保留两只机械切角眼睛与一枚低置的短横嘴巴。眼睛和嘴巴采用统一的斜切缺口，强化科技感。眼睛与嘴巴之间保留约画布高度 22% 的黑色间距，表情更冷峻、克制。黑色背景、大面积留白，适配项目的黑色界面。

通过内置 imagegen 生成，最终原图保存在 `local_ai/pwa/friday-icon.png`。使用 macOS sips 仅导出尺寸，不改变画面设计：PWA 192/512 PNG 和 Apple Touch Icon 180 PNG。页面头像与主屏幕图标来自同一张原图。`icon.svg` 为内嵌 192 PNG 的兼容包装，不是矢量源文件。

Service Worker 公共资源缓存版本已更新为 `harness-public-v16`，新版本激活后更新缓存中的图标。此前的彩色 F 原图及设计说明保存在 `docs/brand/friday-icon-previous.png` 和 `docs/brand/friday-brand-previous.md`。第一版机器人脸及说明备份为 `docs/brand/friday-icon-robot-v1.png` 和 `docs/brand/friday-brand-robot-v1.md`。

## 最终生成提示词

```text
Use case: logo-brand. Edit the supplied Friday robot face logo, responding to this exact critique: it is not handsome or futuristic enough and the eyes and mouth are much too close. Substantially redesign the eye and mouth geometry while preserving the concept of ONLY white eyes and mouth on solid pure black. Create an exceptionally refined, cool futuristic android identity, technical precision and calm intimidating intelligence. Remove the organic curved angry cartoon eye shapes and crooked smirk. Two sleek angular robotic optical slits, each a bold elongated engineered polygon with disciplined 45-degree chamfers and a single purposeful black incision at the outer edge, subtle inward angle, symmetrical eyes, crisp horizontal lower edges. No pupils or eyebrows. Mouth is a separate, low-set, short horizontal white precision slot with chamfered ends and one tiny black interruption, a composed confident expression, no smile curve. CRITICAL PROPORTIONS: eyes occupy y=33 to 42 percent of square canvas; mouth occupies y=65 to 69 percent. Leave at least 22 percent of the entire canvas height as EMPTY BLACK SPACE between bottom of eyes and top of mouth; a much taller facial arrangement than the reference. Overall eye span x=20 to 80 percent; mouth span x=40 to 60 percent. Center face optically. Only eyes and mouth, no face/head contour, helmet, nose, antenna, framing or other decorations. Pure flat graphic design, only white #FFFFFF and black #000000, no gradients, gray fills, lighting effects, glow, textures, shadows, 3D or text. Strong coherent shape language with enough mass to remain recognizable as a 24px app icon. One single finished square logo, no mockup or options sheet.
```
