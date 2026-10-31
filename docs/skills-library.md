# Friday 独立技能库

运行库放在 `~/.friday/skills/`，与服务源码、会话记录分开：

```text
~/.friday/skills/
├── documents/SKILL.md
├── research/SKILL.md
├── image-prompting/SKILL.md
└── h3-prompt-writing/SKILL.md
```

参考 [Hermes 的独立技能目录方式](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/skills.md)，这里以每个技能一个目录、一份可独立编辑的正文作为真实来源。当前 Friday loader 用第一行作简介，单份正文最多16000字符；不是 Hermes 全套安装器或脚本运行器，不宣称兼容其所有 frontmatter 和工具。

`FRIDAY_SKILLS_DIR` 指定运行库路径（支持 `~`）。显式传给 Store 的 `skills_root` 优先，方便隔离测试；开发环境未配置时保留项目种子目录作为后备。生产配置固定到独立目录，不在每次启动时覆盖。

## 安装与更新

```sh
.venv/bin/python scripts/install_skills.py
```

安装只补缺，不覆盖任何已存在的 SKILL.md。项目 `assistant-skills/` 是新安装的分发种子；升级服务源码不会自动覆盖 `~/.friday/skills/` 中的迭代版本。日常更新编辑独立目录里的文件，可用编辑器的原子保存，或写完临时文件后替换 SKILL.md。新任务无需重启服务即可读取新版。

每次读取按正文 SHA-256 标识版本。App 的技能库展示当前文件版本；session 启用名单通过 revision 检查更新。不要将连接器凭据、账号授权或 Mac 管理权限写进技能正文。

## 执行和恢复

- 新任务只捕获所属 session 启用的技能。未启用的技能不进入模型上下文。
- 任务已排队、执行中或重启恢复时使用任务快照；文件后来被修改或删除，不改变该任务。
- 数据库保存正文副本是任务执行快照，真实可编辑来源仍是独立 SKILL.md。
- 图片和视频的生成方法从对应已启用技能读取。代码只处理输入/输出协议、参数约束、校验、持久化和调用模型。
- 未启用对应媒体技能时直接使用原 prompt；改写失败时也回退原 prompt。不会为一次媒体任务悄悄开启技能。
- 新 session 默认为空；发布新技能不改变其他 session 的配置。

如需回滚某个技能，将它的旧正文恢复到独立库即可，后续新任务使用旧版；已经开始的任务继续自己的快照。
