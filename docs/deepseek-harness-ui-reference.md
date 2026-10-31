# Friday 聊天交互调整：DeepSeek Harness 参考

2026-09-28。参考的是 DeepSeek 官方开源项目 [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)，不是官方聊天 App 或社区仿制品。阅读版本：`21638c56315ae6a2b552d6091945d3144c9af32e`。只参考前端结构与交互层级，Friday 继续使用 Objective-C / UIKit，没有引入其 Web 运行时或替换后端。

## 源码观察与本次调整

| 官方源码 | 观察 | Friday 的对应调整 |
| --- | --- | --- |
| [MessageIconActions.module.css](https://github.com/deepseek-ai/deepseek-harness/blob/21638c56315ae6a2b552d6091945d3144c9af32e/packages/client/ui-chat/src/client/chat/MessageIconActions.module.css) | 消息尾部为透明小图标与次要时间文字；无 hover 的设备仍显示操作 | 复制回复由大按钮改为 16pt 图标，与状态/耗时同一行。手机触控范围保持至少 44pt |
| [ReasoningRow.module.css](https://github.com/deepseek-ai/deepseek-harness/blob/21638c56315ae6a2b552d6091945d3144c9af32e/packages/client/ui-chat/src/client/chat/ReasoningRow.module.css) | 默认单行摘要，详情按需展开 | 新增 FRDisclosureRow；任务、进度和错误详情可展开，刷新保留展开选择。仅展示现有任务状态，不虚构模型思考内容 |
| [MessageItem.module.css](https://github.com/deepseek-ai/deepseek-harness/blob/21638c56315ae6a2b552d6091945d3144c9af32e/packages/client/ui-chat/src/client/chat/MessageItem.module.css) | 用户气泡右对齐并限宽；附件由图标、文件名与元信息组成 | 调整用户气泡宽度和间距；文件收为一行，图片/视频先展示预览，再显示文件名与打开图标，移除重复类型标题与整排打开按钮 |
| [AssistantMarkdown.module.css](https://github.com/deepseek-ai/deepseek-harness/blob/21638c56315ae6a2b552d6091945d3144c9af32e/packages/client/ui-chat/src/client/chat/AssistantMarkdown.module.css) | 助手正文没有气泡外壳；排版与操作分层 | 正文基准 16pt，保留动态字号；列表内间距收紧，正文块间距独立；草稿不重复展示完成操作栏 |
| [CodeBlock.module.css](https://github.com/deepseek-ai/deepseek-harness/blob/21638c56315ae6a2b552d6091945d3144c9af32e/packages/client/ui-primitives/src/markdown/CodeBlock.module.css) | 代码区使用细小语言标签与低干扰复制操作 | 代码标题栏改为小字语言标记和透明复制图标；保留横向滚动与精确复制 |
| [ApprovalPanel.module.css](https://github.com/deepseek-ai/deepseek-harness/blob/21638c56315ae6a2b552d6091945d3144c9af32e/packages/client/ui-approval/src/client/ApprovalPanel.module.css) | 授权有独立层级，完整说明与操作明确分区 | 聊天中只留紧凑权限入口，去掉重复的继续请求卡片；完整授权仍使用用户要求的原生弹窗，默认半屏，可拉大，辅助功能大字号默认展开 |

保留 Friday 的黑白视觉；高亮不再铺到每一个操作和状态上。链接使用单独的浅蓝色，危险操作保留红色文字。控件的视觉尺寸与触控范围分别处理，避免为了轻量化而把按钮变得难点。

## 实现与验证

实现位于 `ios/Friday/Sources/FRChatComponents.{h,m}`、`FRMarkdownView.{h,m}` 和 `FRViews.m`。审批的 token/session 绑定、服务端校验、媒体鉴权、缓存及停止任务的确认逻辑保留。

- 组件与授权回归通过，包括首次开启加载字号、重复提交、失败重试、稍后处理，以及窄屏和大字号。
- Markdown 原生布局、复制和流式更新检查通过。
- 新增 9 项交互检查：任务单行摘要、展开/收起、刷新保留展开状态、流式不重复操作栏、44pt 触控、完成消息单一操作栏、权限不重复入口、紧凑文件行。入口：`--friday-demo --friday-fresh-demo --friday-interaction-check`。
- 原导航、键盘、草稿更新与阅读位置检查通过。
- Debug 模拟器与未签名 Release 设备构建通过。
- 原有 macOS 缓存合约测试限制仍见 `ios/Friday/COMPONENTS.md`，本次没有宣称全仓库测试通过。尚未安装实体 iPhone。

截图使用模拟器演示数据：[聊天](../ios/Friday/Previews/harness-inspired-chat.png)、[代码](../ios/Friday/Previews/harness-inspired-code.png)、[授权](../ios/Friday/Previews/harness-inspired-permission.png)。不是实际 Mac 操作或线上任务执行记录。

## 后续体验打磨

新增按需显示的“回到最新消息”按钮，沿用阅读锚点保持逻辑；复制回复和代码改用统一的短暂成功反馈；输入框在旋转或调整系统字号后重新计算高度。交互检查由 9 项扩展为 18 项，覆盖新消息到达时不抢阅读位置、主动返回底部、底部继续跟随和复制反馈恢复。上述交互检查、Markdown UI 回归与两种构建均通过，尚未安装实体 iPhone。

补充：空输入框按当前字体行高测量，避免大字号切回后保留旧的多行高度；空内容和短消息恢复单行均已加入回归检查。
