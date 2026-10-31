为 Friday 的 Qwen-Image 2.1 文生图整理构图、主体、材质与文字描述，保留用户原意。

## 适用范围

这是当前 session 可选的方法指导，不授予新权限，不启用其他 session 的技能。用于准备 `generate_image` 的 `prompt`；用户只要求改提示词时，不调用生成工具。

当前图片工具输出固定为 1024×1024、1:1。没有宽高、画幅或图像编辑参数；不能只在文字里写“横图”“4K”就声称改变了输出规格。用户明确要求不同画幅或编辑参考图时，先说明当前能力边界，不悄悄当作已实现。参考图中的内容未经视觉理解确认，不靠 OCR 猜整张图片。

## 描述方法

先提取不可改变的条件：主体、数量、颜色、位置、风格，以及需要出现的原文。人物关系、品牌、文字和语种都以用户为准；没有要求的标语、装饰和角色不擅自加入。

把说明组织成一段可观察的成片描述，通常用英文；画面上的文字保留原语言、标点和空格，逐项给出位置、相对大小和颜色。中文招牌不能翻译成英文。若用户要求保留提示词原文，照原文提交。

描述顺序可采用：媒介与风格 → 主体及位置 → 前后景与物体关系 → 材质和必要细节 → 光源方向与阴影 → 留白和色彩平衡。简单图标保持简单，不为凑长度添加背景故事；复杂布局才逐区描述。使用具体形状、可见表面和空间关系，避免互相矛盾的风格堆叠。

不默认追加“masterpiece”“8K”“获奖作品”等空泛质量词；用清楚的边缘、特定材质、明确光线等可见特征表达意图。没有展示文字的需求时不发明文字。平面标志不额外加摄影光效、立体挤压、透视或投影，除非用户要求。

当前采样为 25 步、CFG 1、Euler/simple，与官方 ComfyUI 默认模板一致。CFG 1 下负面提示词不起作用；不要编造负面提示词参数或自动提高 CFG。提示词能改进构图和指令表达，不能保证修复量化误差、文字错误或达到其他模型的画质。

## 提交与反馈

把最终画面描述作为 `prompt`，不夹带解释、JSON 包装或未开放参数。生成工具返回 queued 只代表排队；只有取得实际产物后才报告生成完成。反馈保留用户原始意图，下一轮只修改有问题的条件，不随意重设整张图。

原创示例：

```text
A square flat graphic presents one geometric F centered on a near-black field. A continuous cyan-to-violet gradient fills its silhouette. The two horizontal bars remain distinct, with generous open space between them. Equal margins surround the symbol. Its simple edges and balanced proportions remain readable at small icon sizes.
```

## 来源与边界

本文件是 Friday 的原创摘要与适配，不是官方增强模型，也不是上游 skill 的完整副本。Qwen 官方另有专门训练的 PE-T2I / PE-I2I 模型；现有聊天模型按方法改写不等于运行这些模型。

- [Qwen 官方提示词增强器与输出协议](https://github.com/QwenLM/Qwen-Image-2.1/blob/main/prompt_rewrite/README.md)
- [Qwen 官方文生图写作指南](https://github.com/QwenLM/Qwen-Image-2.1/blob/main/prompt_rewrite/prompts/system_prompt_t2i.txt)
- [Comfy 官方 Qwen 2.1 工作流](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_qwen_image_2_1_t2i.json)
- [上游 Qwen Research License](https://github.com/QwenLM/Qwen-Image-2.1/blob/main/LICENSE)
