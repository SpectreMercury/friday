**OpenClaw / Hermes：任务理解、执行与恢复设计对照**

范围补充：本篇是机制对照，不是完整系统整改方案。当前应用的跨场景架构问题、评测范围和改造边界见 [系统架构审查](./system-architecture-review.md)；不应仅围绕点评失败案例实施本篇建议。

调研日期：2026-09-26。读取官方仓库文档和相关实现，并对照当前 qwen 工作区。本次是静态设计审阅，没有运行两套框架做同模型对测，也没有据此修改当前执行代码。

固定源码版本：OpenClaw `4e455c298b3aaa6d589f49ab3891af58c1f34472`；Hermes `cb399b56d964306bfad18b20edee30f88f777bf0`。下方外部链接均固定到这两个提交，避免 main 更新后结论失去依据。

**核心结论**

它们的理解、拆分和选工具仍主要由模型完成。区别在于：运行时把可用能力、执行过程、用户纠正和恢复机制组织起来，使模型能根据新证据不断调整。不能把它们简化为“多写几条不许放弃的提示词”，也不能认为挂上一个 planner 就解决了理解问题。

从我们当前代码看，最需要纠正的是把“目标”“计划”“工具调用”“交付结果”混在一起：选对工具不等于理解对目标，调用成功不等于任务完成，有回复不等于结果送达。

**1．它们如何理解和拆分任务**

| 设计点 | OpenClaw | Hermes | 对我们的意义 |
|---|---|---|---|
| 理解请求 | 组装系统提示、当前会话、工作区资料、skills 和实际工具能力；模型决定下一步 | 分层组装身份、项目上下文、技能索引、记忆和运行环境；模型决定下一步 | 没有证据表明一个固定分类器或强制计划接口能保证理解正确 |
| 是否先列计划 | 核心循环不要求每个请求先调用 planner；进度卡针对较复杂工作，且受工具和界面条件控制 | `todo_list` 面向多步骤工作；清单可读、更新、替换，含父子任务和状态 | 简单任务直接执行，复杂任务维护可修订计划 |
| 如何调整 | 工具结果和新消息回到循环；运行时有 steering 检查点 | 工具结果回到循环；清单失败项可取消并添加修订项 | 保持用户目标，允许替换实现路径 |
| 完成依据 | 提示要求结论有证据或明确阻碍；进度数字必须来自观察 | 清单说明要求核验后才能标完成，但状态仍由模型写入 | 提示约束有价值，但不是通用语义验收器 |

源码依据：[OpenClaw 执行倾向提示](https://github.com/openclaw/openclaw/blob/4e455c298b3aaa6d589f49ab3891af58c1f34472/src/agents/system-prompt.ts#L389)、[进度卡提示及启用条件](https://github.com/openclaw/openclaw/blob/4e455c298b3aaa6d589f49ab3891af58c1f34472/src/agents/progress-card-system-prompt.ts)、[Hermes 任务清单实现](https://github.com/NousResearch/hermes-agent/blob/cb399b56d964306bfad18b20edee30f88f777bf0/tools/todo_tool.py#L221)、[提示组装](https://github.com/NousResearch/hermes-agent/blob/cb399b56d964306bfad18b20edee30f88f777bf0/website/docs/developer-guide/prompt-assembly.md)。

**关键区别：目标应稳定，计划应可修订。** 用户要求“找符合条件的店铺”是目标；查哪个数据源、调用哪个接口是方法。若先强制选 `web_search + web_read`，随后发现有结构化 API，计划应当能切换。限制用户未授权的目标变化，与锁死工具名单，是两件不同的事。这是本次对照得出的建议，不是声称两个项目都实现了同一个目标对象。

**2．执行循环，而非一次调用后发回复**

OpenClaw 的核心路径是：接收请求 → 会话排队与上下文准备 → 模型生成 → 执行工具 → 把结果写回上下文 → 再决策。核心循环还处理工具批次、取消、后续输入和执行边界，运行层在外围管理模型、超时和恢复。

Hermes 的 `AIAgent` 也采用持续循环，但把阶段拆成独立模块：请求准备、模型调用、响应处理、工具轮次、最终文本处理、错误恢复、最终状态整理。`turn_tool_round.py` 明确先保存工具调用记录，再执行有副作用的工具。恢复时能看到“原先准备做什么、实际执行到哪里”。

这类设计让失败成为下一次决策的输入。不过，保存调用记录本身不等于所有外部操作都具有 exactly-once 保证；发送、付款等操作仍需要各自的去重和回执机制。

依据：[OpenClaw 核心循环](https://github.com/openclaw/openclaw/blob/4e455c298b3aaa6d589f49ab3891af58c1f34472/packages/agent-core/src/agent-loop.ts)、[运行循环](https://github.com/openclaw/openclaw/blob/4e455c298b3aaa6d589f49ab3891af58c1f34472/src/agents/embedded-agent-runner/run-loop.ts)、[Hermes 主循环](https://github.com/NousResearch/hermes-agent/blob/cb399b56d964306bfad18b20edee30f88f777bf0/agent/conversation_loop.py)、[工具轮次](https://github.com/NousResearch/hermes-agent/blob/cb399b56d964306bfad18b20edee30f88f777bf0/agent/turn_tool_round.py)。

**3．工具与 API：让模型知道能做什么**

Hermes 用 registry 注册工具的 schema、处理函数和可用性检查；生成模型工具清单时过滤不可用工具。MCP、插件和内置工具汇入同一调用体系。搜索与网页提取再通过 provider 层连接不同服务，并区分配置选择和能力可用性。

OpenClaw 将搜索、轻量网页获取和浏览器交互区分开，搜索可接 API provider。文档还明确区分“配置存在”和“实际测试可用”；选择 provider 不代表已验证凭据、网络或本次调用路径。

因此，可借鉴的是**统一能力发现、服务适配和调用结果格式**。没有发现应当把“先搜索 → 再浏览器 → 最后找用户”当成所有任务的固定顺序，也不能把搜索 API 等同于大众点评业务 API。

对我们的建议：结构化数据任务优先使用已知可用、字段满足要求的 API；缺能力时先发现和核验适配器。只有观察到具体缺失的授权或输入，才要求用户补充。这个 API 优先原则是针对我们任务和用户偏好的建议，不是两套框架共有的强制规则。

依据：[Hermes 工具运行机制](https://github.com/NousResearch/hermes-agent/blob/cb399b56d964306bfad18b20edee30f88f777bf0/website/docs/developer-guide/tools-runtime.md)、[registry 实现](https://github.com/NousResearch/hermes-agent/blob/cb399b56d964306bfad18b20edee30f88f777bf0/tools/registry.py)、[web provider 路由](https://github.com/NousResearch/hermes-agent/blob/cb399b56d964306bfad18b20edee30f88f777bf0/tools/web_tools.py)、[OpenClaw 搜索设计](https://github.com/openclaw/openclaw/blob/4e455c298b3aaa6d589f49ab3891af58c1f34472/docs/tools/web.md)、[浏览器设计](https://github.com/openclaw/openclaw/blob/4e455c298b3aaa6d589f49ab3891af58c1f34472/docs/tools/browser.md)。

**4．失败如何处理：分清失败发生在哪层**

| 失败种类 | 核实到的机制 | 应借鉴什么 |
|---|---|---|
| 模型 API 超时、限流或认证错误 | 两者都有按错误类型处理的恢复与配置化模型/凭据回退路径 | 不把模型请求失败直接解释为用户任务无法完成 |
| 模型只说“我来做”，随后结束 | Hermes 检测部分应继续却停止、短碎片等情形，最多追加两次这类续跑提示；受配置和适用条件影响 | 对可观察的过早结束做有限恢复，而非见到“抱歉”就重试 |
| 工具结果后没有最终回复 | OpenClaw 在符合条件的已结束工具批次后补一次无工具总结，避免重跑已经执行的操作 | “补齐交付文本”与“重新执行任务”分开 |
| 重复调用没有进展 | OpenClaw 对调用和结果做归一化比较，可警告、阻断并给一次改变路径的机会 | 重复次数不是唯一依据，要看结果是否发生实质变化 |
| 上下文太长 | 两者有压缩与恢复机制；保存近期上下文、重要标识及工具调用/结果关系 | 用摘要续接执行现场，减少粗暴截断导致的丢目标 |

注意：OpenClaw 普通滚动工具循环检测默认关闭；压缩后循环保护在没有显式关闭时仍可生效。模型回退需要实际配置备用模型，不会凭空产生另一个可用服务。机制存在不代表每种部署都自动启用。

依据：[OpenClaw 模型故障转移](https://github.com/openclaw/openclaw/blob/4e455c298b3aaa6d589f49ab3891af58c1f34472/docs/concepts/model-failover.md)、[循环检测及默认值](https://github.com/openclaw/openclaw/blob/4e455c298b3aaa6d589f49ab3891af58c1f34472/docs/tools/loop-detection.md)、[循环检测实现](https://github.com/openclaw/openclaw/blob/4e455c298b3aaa6d589f49ab3891af58c1f34472/src/agents/tool-loop-detection.ts)、[回复收尾规则](https://github.com/openclaw/openclaw/blob/4e455c298b3aaa6d589f49ab3891af58c1f34472/docs/concepts/agent-loop.md)、[Hermes 错误恢复](https://github.com/NousResearch/hermes-agent/blob/cb399b56d964306bfad18b20edee30f88f777bf0/agent/turn_recovery.py)、[文本回复续跑检查](https://github.com/NousResearch/hermes-agent/blob/cb399b56d964306bfad18b20edee30f88f777bf0/agent/turn_final_response.py)。

**5．上下文和用户纠正：不能只拼接历史问答**

OpenClaw 会话排队明确区分 `steer`、`followup`、`collect`、`interrupt`。所读版本默认使用 steering：可接收纠正的运行将新消息带入当前执行，在边界处改变接下来的操作；不支持 steering 时再排队。正在执行的工具与尚未启动的调用分别处理。

Hermes 将工具调用和结果作为会话内容处理；任务清单带 revision，并支持从历史恢复、在压缩后保留活跃任务信息。技能承载可复用流程，记忆承载跨任务的稳定事实，避免把某次特殊任务永久写进通用规则。

这对应我们的实际问题：用户追问“为什么没完成”，应能定位刚才那个请求及其失败点；用户补充“用 API”，应能调整当前方案。仅靠“为什么”“图片”等词匹配，或把最近几段 assistant 文本当成事实，都不足以可靠完成这种关联。

依据：[OpenClaw 消息队列与 steering](https://github.com/openclaw/openclaw/blob/4e455c298b3aaa6d589f49ab3891af58c1f34472/docs/concepts/queue.md)、[核心循环检查点](https://github.com/openclaw/openclaw/blob/4e455c298b3aaa6d589f49ab3891af58c1f34472/packages/agent-core/src/agent-loop.ts#L414)、[上下文压缩](https://github.com/openclaw/openclaw/blob/4e455c298b3aaa6d589f49ab3891af58c1f34472/docs/concepts/compaction.md)、[Hermes 任务清单状态](https://github.com/NousResearch/hermes-agent/blob/cb399b56d964306bfad18b20edee30f88f777bf0/tools/todo_tool.py)、[上下文压缩实现](https://github.com/NousResearch/hermes-agent/blob/cb399b56d964306bfad18b20edee30f88f777bf0/agent/context_compressor.py)、[提示与记忆分层](https://github.com/NousResearch/hermes-agent/blob/cb399b56d964306bfad18b20edee30f88f777bf0/website/docs/developer-guide/prompt-assembly.md)。

**6．如何结束，以及哪些事情仍没有保证**

OpenClaw 把运行生命周期与消息送达事实分开。终结回执中保存 run/session/turn、实际模型、成功工具名和 `sourceReplyDelivered`。后者是外部源会话的送达记录，不是用户已阅读，也不是业务目标被正确满足。

Hermes 返回 `completed`、`failed`、`interrupted`、`turn_exit_reason` 等独立字段；对部分文件修改失败会追加核验提示。它还有结束检查扩展点，例如 verify-on-stop、代码修改后的 `pre_verify` hook、看板任务终结工具检查。这些都有具体适用条件，不能说每次查询都有一个自动审查员验证所有事实。

**两者都不能仅凭运行完成状态，证明“请求理解正确且结果满足全部约束”。** 所读代码提供了更好的事实记录、限定场景核验和扩展点；领域数据正确性仍需任务对应的验证方法。这也是我们不能只复制状态字段和重试次数的原因。

依据：[OpenClaw 终结回执类型](https://github.com/openclaw/openclaw/blob/4e455c298b3aaa6d589f49ab3891af58c1f34472/src/agents/agent-run-terminal-receipt.ts)、[Hermes 最终状态与文件修改提示](https://github.com/NousResearch/hermes-agent/blob/cb399b56d964306bfad18b20edee30f88f777bf0/agent/turn_finalizer.py)、[结束检查实现](https://github.com/NousResearch/hermes-agent/blob/cb399b56d964306bfad18b20edee30f88f777bf0/agent/turn_stop_gates.py)。

**7．当前 harness 的具体差距**

以下是当前工作区的代码事实和推论，包含我刚加入、但现在看来仍有设计缺陷的补丁。

| 当前实现 | 直接问题 | 应调整的方向 |
|---|---|---|
| 强制每轮先 `plan_task`，并拒绝第二次计划 | 第一次理解出错后不能正式修订计划；普通问答也增加开销 | 目标和约束单独保存；复杂任务按需规划，允许有记录的修订 |
| 计划通过列举 `required_tools` 表达验收条件 | “用了搜索和网页读取”不能证明拿到了正确城市、完整评分或符合条件的记录 | 验收应检查所需产物及关键约束，工具执行只是证据的一部分 |
| `answer` 模式允许零工具，分类由同一模型给出 | 仍可能把执行请求错分为普通问答，再通过工具清单校验 | 用真实任务评测发现误判；增加有依据的语义核验，不能把模型分类当事实 |
| 非媒体回答的校验主要排除特定坏文本 | 很多错误、空泛或偏题答案仍可通过 | 业务验收覆盖真实目标；允许部分完成并列明缺失 |
| 历史主要由 jobs 的问答重建；最近工具事实另作摘要 | 不是完整可恢复的会话轨迹，细节和任务关联可能丢失 | 保存规范化消息、工具结果、任务关系和用户纠正，压缩时保留关键状态 |
| 搜索连续失败后封住该工具；恢复仍大量依赖回复关键词 | 能止损，但未必能恢复；“有抱歉”不是可靠错误类型 | 将传输、认证、空数据、错误参数和无进展分别处理 |
| 12 轮、24 次工具预算，2048 输出上限，关闭 thinking | 多步骤任务和修复共享较小预算；可能影响表现，但本次未测得因果贡献 | 对任务类型、模型能力和耗时做对照评测，再决定参数和模型路由 |

本地依据：[执行计划与校验](../local_ai/execution.py:9)、[计划不可修改与搜索限制](../local_ai/pi_agent.py:59)、[普通回答校验](../local_ai/evidence.py:105)、[历史重建](../local_ai/store.py:88)、[模型与循环配置](../pi/runner.mjs:84)。

尤其需要承认：刚加的 `plan_task` 能减少直接复制旧失败通知的情况，但没有解决“理解和拆分错误”，还把方法选择与验收绑定得过死。不能把这项补丁称为已修复 harness。

**8．建议的改造顺序与验收方式**

这部分是建议，没有在本轮实施。

1. **先建立真实失败样本。** 包括截图里的请求、紧接着的“为什么没完成”、用户纠正方法、切换话题、简单一步任务。记录每个样本真正应交付什么，避免只测调用次数或非空输出。
2. **修正目标与计划的关系。** 保留原请求、约束、预期产物和用户补充；复杂任务清单可更新。普通任务不强制先做一轮规划。方法变化不应被当作降低验收标准。
3. **保存可恢复的执行轨迹。** 将用户消息、模型消息、工具调用及结果、失败分类和交付记录关联到同一任务；使追问能回到正确失败点。
4. **接入真实能力发现与 provider 层。** 优先补齐样本任务需要的 API 能力，验证返回字段和错误语义。不要仅向模型宣称“支持搜索/图片/视频”。
5. **按失败类型恢复。** 临时网络错误可退避；无数据可换来源；缺授权应保留工作并明确缺失；对外写入先检查是否已经执行，避免重复副作用。
6. **分开验收结果和发送结果。** 查询按原条件验证数据；文件操作检查真实文件；发送操作检查回执。正确报告部分完成或阻碍，不能因生成了一段文本就写入成功。

| 验收场景 | 必须观察到什么 |
|---|---|
| 要求真实数据筛选 | 来源可追溯，字段和单位齐全，条件计算正确；缺字段不得推测 |
| 用户问“为什么没完成” | 解释对应请求的真实失败点，并在授权范围内续接；不跳到旧媒体任务 |
| 用户纠正“用 API” | 更新当前方法，保留原目标和已取得的有效证据 |
| 一次工具失败 | 按错误类别修复或换路，不无意义重复，也不立即结束 |
| 模型只回复“我来处理” | 保持任务未完成，有限续跑；失败时报告实际阻碍 |
| 有工具调用但没有有效结果 | 不标为完成，不用调用次数冒充业务证据 |
| 外部操作完成后模型中断 | 续跑不重复该操作，交付依据已有结果 |
| 任务完成但发送失败 | 保留业务结果，仅处理交付失败；两种状态可分辨 |

建议用同一模型、同一工具后端、同一任务集比较当前实现与改造版，再单独比较模型/推理配置。这样才能知道收益来自 harness、工具覆盖还是模型能力；本次静态调研不能量化谁“聪明多少”。
