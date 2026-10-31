为 Friday 的 MiniMax H3 短视频编写连续动作、镜头和同步声音描述，保留首帧与用户原意。

## 当前能力

这是当前 session 可选的方法指导，不授予新权限。用于准备 `generate_video` 的 `prompt`；用户只要提示词时不启动生成。

当前使用 FL2VA 模型；无图片时为文生视频，已有执行路径支持一张首帧图时为首帧续写。`generate_video` 工具目前只有 `prompt`、`hd` 和可选的 `duration_seconds`（整数4–15，默认15），不能新增虚构的 image、width 等参数。只有实际作业已经绑定首帧时才引用它；用户上传图片不等于该图已成为首帧。

默认生成15秒（模型按17k+5对齐为362帧、24fps，导出裁齐目标时长）。默认 352×608，`hd=true` 为 480×864；两者都是竖幅，hd 不是官方 768p 或 2K。当前不提供末帧输入、Ref2VA 多模态参考、单镜头超过15秒或官方 2K 再生成。不同规格不能靠提示词实现；用户明确要求时说明差距，不把多镜头长剧情默默改成已满足的短片。

## 写作规则

保留用户指定的主体、数量、身份、服装、场景、动作、风格和台词。使用英文描述可见画面与声音；台词、歌词及屏幕文字保留原语言和标点。没有要求的台词、字幕、配乐或剧情不擅自加入。

围绕本次镜头的实际时长（默认15秒）安排一个清楚的连续镜头：初始位置 → 动作开始 → 连续变化 → 收尾状态。优先一个主体动作和一种镜头运动；说清方向、幅度与速度，避免同一时刻要求静止与旋转、推近与拉远。不要塞入超过实际时长的时间点。用户要求多镜头短剧时，使用 `video_capacity` 和 `generate_video_project` 先估算再分镜；过长台词应分配到多个镜头，不删改台词后声称完整实现。

有首帧时先注明该图在 0.00 秒对齐，并围绕已知的构图、人物和服装发展动作。不能凭空替换图中身份、颜色、空间布局或看不清的物件；没有视觉信息时只描述用户要求的动作与保持首帧一致，不能把猜测写成画面事实。

保持下列三个字段名及顺序。第一段以 `[Shot 1]` 开始，描述媒介、构图、主体、动作、镜头和有发生时点的声音；第二段概括环境与动作声；第三段只写观众听到的背景配乐。没有配乐时写 `N/A`。用户要求全程静音时两个声音字段均写 `N/A`，并保留其静音要求；生成后仍需核查实际音轨。

```text
integrated_multimodal_description: [Shot 1] ...

overall_soundscape: ...

non_diegetic_music: ...
```

说话者使用稳定编号，例如 `(S1)`；编号与声音特征放在台词标签外，台词形式为 `<d>[Chinese] 原台词</d>`。屏幕文字用双引号包围原文，并交代位置。不要自动翻译台词，也不要把对白重复放进环境音字段。没有人说话时不创建说话者编号。

## 提交与检查

提交上面的纯文本结构，不把原始 JSON 或提示词分析交给生成模型。工具排队不代表已成片。拿到视频后按用户要求核对动作连贯、主体稳定、镜头方向、实际时长及声音同步；没有观看/收听依据时不得宣称画质提升或音画一致。

原创文生视频示例：

```text
integrated_multimodal_description: [Shot 1] A static medium close-up frames a white ceramic cup on a wooden desk beside a window. The cup remains still while a thin stream of steam rises, then bends gently toward the window. Soft morning light stays steady for the entire shot.

overall_soundscape: Quiet room ambience with a faint breeze outside the window.

non_diegetic_music: N/A
```

## 来源与边界

这是适配 Friday 当前能力的原创摘要，不是 MiniMax 官方 skill 的完整副本。官方 H3-Context-IR 与 H3-Regenerate-2K 尚未完整开源；此技能只整理描述，不能补出缺失模型或保证复现官网结果。

- [MiniMax 官方可移植提示词 skill](https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing)
- [官方文本与首末帧提示词指南](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/base-en.txt)
- [官方系统模块与开放范围](https://github.com/MiniMax-AI/MiniMax-H3#system-overview)
- [上游 MiniMax H3 Community License Agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE)

## 视频制作前的讨论与确认

先与用户讨论并确定剧本，再确定角色照片、环境照片的选择及用途，最后展示逐镜头分镜和对应 prompt。缺少照片时询问用户是提供照片、先设计照片还是明确不用，不能自行决定，更不能先生成再讨论。用户说“做视频”不等于已确认剧本或参考图。

调用视频工具时用 character_photos、environment_photos 如实记录讨论后的选择，不能编造确认。提交后仅为待确认方案；用户确认完整方案前不渲染。当前 FL2VA 仅支持实际绑定的单张首帧，未接入多角色/环境参考图绑定；明确告诉用户选择记录与实际模型输入的区别。

每个镜头必须有独立的剧情进展、动作和运镜；不能复制同一 prompt 后只换编号。重复被拦截时返回讨论修订，不改种子或换编号绕过。确认后的各镜头 prompt 必须逐一对应渲染，修改方案后重新确认；未确认不恢复旧项目。
