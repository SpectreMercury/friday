# 对话 → 行动：Logo 设计语言

## 产品概念

个人 AI 助手通过对话接收意图，并推进任务。核心表达为「说一句，向前一步」。对话气泡提供明确的交互含义，右上箭头表达执行与推进；两者融合为同一符号。

## 形状规则

- 圆角矩形与厚轮廓：亲和、稳定，提供头像尺寸下的视觉重量。
- 大面积内部留白：保留对话气泡识别，避免细节堆砌。
- 左下气泡尾与右上箭头形成方向关系，连接输入与行动。
- 电光蓝为主色意图，尽量让轮廓承载识别。

## 延展建议

界面卡片沿用圆角体系；执行状态动效可从气泡尾向右上箭头移动；主操作按钮沿用电光蓝。以上为设计建议，尚未实现。

## 当前状态

内置 imagegen 直接生成的 PNG 设计概念，未用 SVG 绘制。已检查图形：可见对话气泡和右上箭头。生成稿的箭头仍较大、局部连接圆角尚可进一步统一；概念表达并不等于记忆度已经验证。未替换现有应用图标。

文件：`conversation-action-v1.png`

## 完整提示词

```text
Use case: logo-brand.
Create one highly resolved, meaningful product logo for a personal AI assistant. The user rejected previous abstract marks because they did not communicate what the product does. The new mark must communicate CONVERSATION BECOMING ACTION at first glance.
Core design language: a friendly bold rounded-square SPEECH BUBBLE whose upper-right contour seamlessly becomes a short decisive NORTHEAST ARROW. This is ONE integrated silhouette, not two icons placed together. The lower-left speech tail makes conversation immediately recognizable. The upper-right arrow tip makes forward action recognizable. Inside the substantial blue body, carve a generous off-white simple rounded rectangular speech aperture. The aperture and outer contour share consistent corner radii; near the upper-right the aperture opens toward the arrow to create one purposeful directional gap. Aim for an optically balanced compact icon with substantial mass and clear large negative space, easy to recognize at 24px. Keep the speech bubble the dominant reading and the directional arrow a clever secondary discovery. Do not let it look like a refresh icon or generic download icon.
Color: saturated electric blue #2854FF as the single solid fill, on a warm clean off-white #F7F8FC background. No gradients; let the symbol carry the meaning.
Visual tone: intelligent, approachable, reliable, technically precise; a sophisticated AI assistant brand rather than finance or gaming. Flat professionally designed brand graphic with immaculate smooth curves and crisp edges. One rounded corner family, one consistent diagonal angle, no arbitrary ornament.
Composition: one large centered symbol occupying 58 percent of a square canvas, generous empty margins. Output only the finished logo image. No lettering, no caption, no wordmark, no sheet of alternatives, no presentation layouts, no mockups. No abstract blobs, random swooshes, flames, robot faces, eyes, sparkles, brain shapes, circuits, metallic effects, shadows, 3D, textures, watermarks or tiny details. Prioritize a clearly meaningful concept and distinctive clean silhouette over decoration.
```

