# Friday 本地图片与视频质量

核对日期：2026-09-27；时长能力更新于2026-09-28。本记录区分提示词表达、模型与采样配置、媒体交付方式；在聊天中内嵌展示图片或视频，不会改变生成模型本身的质量。

## 实际模型与参数

以 `models.lock.json`、`workflows/image.json`、`workflows/video.json` 和 `local_ai/engine.py` 为准。

| 项目 | 当前值 | 边界 |
| --- | --- | --- |
| 图片扩散模型 | abenzerps/Qwen-Image-2.1-Uncensored-Quantized（原名 -GGUF），UC-Q8_0 | 社区转换/量化，不是官方 BF16 基准 |
| 图片编码器与 VAE | qwen3vl_8b_int8_convrot；qwen_image_2.1_vae_bf16 | 与模型一起影响结果 |
| 图片画布与采样 | 1024×1024；25 步，CFG 1，Euler/simple | 固定方图；负面提示词在 CFG 1 时不参与常规 CFG 运算 |
| 视频扩散模型 | Abiray/MiniMax-H3-Pruned-GGUF，FL2VA-Pruned-Q5_K_M | 文生视频／首帧路径；不是 Ref2VA |
| 视频编码器 | pottokao 的 Qwen3VL-32B-abliterated-Q3_K_S_vis | 社区改动并量化，带视觉塔 |
| 视频 LoRA | H3-PK-Parasyte-Turbo，强度 1.5 | 发布者称实验方案，使用 AdaLN LoRA 修正节点 |
| 视频采样 | 8 步，er_sde/beta57，video/audio shift 12/3 | 当前参数符合该 LoRA 发布者的建议起点，不能仅凭步数判错 |
| 视频画布 | 默认352×608；hd480×864 | 约0.21MP／0.41MP；hd 不等于官方768p或2K |
| 视频时长与输出 | 默认15秒：362帧、24fps，导出裁齐；H.264 MP4，原生音频解码 | 单镜头可选4–15秒；长片经持久化分镜队列生成并合并，不能靠prompt突破模型时长 |

官方 ComfyUI 的 Qwen 2.1 模板也使用1024方图、25步、CFG 1、Euler/simple。现有采样并非明显配置错误；不能把全部问题归因于步数，或直接提高 CFG、堆负面词。模板另有可选专用提示词增强器。[官方工作流](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_qwen_image_2_1_t2i.json)

Pruned 名称本身不是画质损失证据：H3 官方说明 AdaLN 部分参数可预计算并移出推理。社区文件是否准确遵循等价处理，以及量化、编码器改动、LoRA各自的影响，需要同条件实测；本轮没有进行这类对照推理。[官方模型架构](https://github.com/MiniMax-AI/MiniMax-H3#model-architecture)

当前模型发布者资料：[图片 GGUF](https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-Quantized)、[视频 GGUF](https://huggingface.co/Abiray/MiniMax-H3-Pruned-GGUF)、[视频编码器](https://huggingface.co/pottokao/MiniMax-H3-TextEncoder-Qwen3VL-32B-abliterated-GGUF)、[视频 LoRA](https://huggingface.co/Plaguekind/H3-Lora)。这些是相应文件发布者的一手说明，不能当作 Qwen/MiniMax 官方质量认证。

## 官方已有能力

Qwen 官方公开了专门训练的9B PE-T2I和PE-I2I模型，以及重写代码和系统提示词。文生图模型输出英文成片描述及画幅，图片中的文字保留原文；编辑模型使用不同的输入及输出协议，两者不能混用。[增强器说明](https://github.com/QwenLM/Qwen-Image-2.1/blob/main/prompt_rewrite/README.md)

H3 官方系统包括输入整理 H3-Context-IR、本地 H3-Base，以及 H3-Regenerate-2K。前后两项未随当前基础模型完整开源，因此本地基础模型加 skill 不能宣称复现官方完整2K链路。[官方系统说明](https://github.com/MiniMax-AI/MiniMax-H3#system-overview)

官方 `h3-prompt-writing` 是无需外部API的文本技能，可以参考其格式。仓库其他八个风格技能依赖 MiniMax Hub 工具，不应直接当作本 harness 已有能力。[官方技能目录](https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills)

## 本地适配器与会话技能

Friday 的内建 prompt adapter 使用现有本地聊天模型整理生成描述，是受当前工作流约束的近似方案。它不是官方 Qwen PE 模型，也不是 MiniMax 托管的 Context-IR；本次没有新增模型权重、购买API或启用云端改写。

适配器位于媒体执行前的独立阶段，以当前作业原始prompt、媒体类型、真实规格和任务已启用的对应技能快照为输入。原始prompt保持不变；重写文本、技能版本、工作流、seed、模型清单和成功或回退状态保存在 `media_prompts` 表。恢复继续使用已经保存的决定。超时、无效输出或丢失带引号的原文时回退原prompt，不把错误说明或推理过程当作生成提示词。实际效果仍以生成结果为准，不能仅凭重写成功宣称画质提升。

`Engine.chat()` 与 `Engine.render()` 共用锁，改写不能在持有render锁时再次调用chat。改写阶段还应保留语音/GPU调度、取消与恢复语义，不因多一次模型调用引入长期持锁。

新增的 `image-prompting` 和 `h3-prompt-writing` 是独立文件里的方法指导，生产来源为 `~/.friday/skills/`（可配置 `FRIDAY_SKILLS_DIR`）。只有当前session启用并进入任务快照后，agent及媒体改写阶段才能读取。对应技能未启用时，详细的prompt原样使用；过短的prompt（拉丁词数+汉字数/2低于图片25、视频40，见 `SIMPLE_BRIEF_UNITS`）仍会用代码内置的最小补全要求（`EXPANSION_GUIDANCE`）改写，不读取技能库。技能已启用且prompt过短时，补全要求追加在技能之后。快照中的 `expansion` 字段记录是否触发。发布新技能不替所有session启用，也不加载其他session的历史或记忆。代码只保留JSON输出协议、校验和执行逻辑，不再内嵌图片/视频写作方法。详见[独立技能库](skills-library.md)。

技能内容自包含，第一行是简介，以适配当前 `Skills.list()` 和只读取单个 `SKILL.md` 的任务快照。没有依赖自动加载的references文件。启用选择是session配置，技能更新与服务源码发布分别维护。

## 编写重点与不能承诺的部分

图片优先补全空间位置、构图、材质和光源，保留明确主体、数量和可见文字；不为凑长度增加无关元素。最终描述使用当前方形画布。写“8K”不会增加像素，添加negative字符串不会在CFG 1时神奇修复画面。

视频围绕实际约5.17秒安排可完成的连续动作，说明镜头方向与声音发生关系。没有明确的多镜头要求时使用单镜头；有首帧时保留人物与布局，缺少视觉信息时不编造图中内容。用固定三字段描述画面、环境音、非场景内配乐。音画同步、身份稳定及文字准确仍需观看成片验证。[H3基础模式指南](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/base-en.txt)

## 可复查的质量比较

2026-09-27 部署验证：503项Python测试、49项PWA Node测试通过。使用实际本地聊天模型和独立技能库，在临时数据库里完成图片/视频两种改写；均得到合法结果并保存技能hash与固定seed，记录为 `runtime/media-prompt-v2-smoke-20260927.json`。这验证了技能加载和改写链路，没有运行同seed原prompt/增强prompt的图片或视频成片A/B，因此本轮不宣称已测得画质提升幅度。PWA另在320px和390px视口检查内嵌图片/视频/音频与布局，iPhone安装态仍以真机显示为准。

1. 选少量真实需求：单主体图、中文文字排版、简单图标；视频包含静态镜头、单一动作、首帧续写。保留用户原始文本及附件。
2. 固定模型文件和hash、工作流版本、seed、步数、采样器、画布、时长与LoRA，只比较原prompt和重写prompt。多个固定seed逐对比较，避免挑中偶然好样本。
3. 分开记录：用户条件符合度、文字准确性、构图、可见伪影；视频另记身份漂移、动作连贯、镜头符合度与音画同步。同时记录耗时与峰值内存。
4. 再单独比较分辨率、LoRA开关、编码器精度或扩散量化。不要一次替换多项后把全部提升归功于技能。没有取得本机对照结果时，报告为待验证。
5. 评估更高分辨率前检查Mac内存与实时语音调度。当前hd档不是最终质量上限，也不是官方2K再生成替代品。

### 2026-10-31 图片量化 Q4_K_M → Q8_0

`scripts/compare_image_models.py` 用4条描述×2个固定seed，只替换扩散权重，其余图与采样不变。Apple MPS 上 Q8_0 每张约120秒，Q4_K_M 约162秒（Q8更快）。肉眼比较：细节持平或略好，肖像皮肤与胡须过渡更自然；中文招牌四字均正确，但其中一个Q8样本多出一根背板横杆。差别属小幅改进，不是质变。脚本统计的进程RSS不含MPS显存，不能作为内存对比依据。据此默认切换为Q8_0，Q4_K_M文件保留以便回退。

## 来源许可记录

以上技能与说明是项目原创摘要和适配，附上游来源，不是大段复制的官方文档，也没有将官方权重或指南重新声明为MIT。

- Qwen上游采用 [Qwen Research License](https://github.com/QwenLM/Qwen-Image-2.1/blob/main/LICENSE)，并非通用Apache许可。
- H3模型及相关材料采用 [MiniMax H3 Community License Agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE)。社区文件单独显示的许可标记不等于替代基础模型条款。

本轮仅记录已核实的来源与许可名称；没有迁移模型或重新分发其权重。
