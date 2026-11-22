# 视频转录与总结 Skill：安装及调用

版本：2.0，2026-11-22。新技能名：`video-transcription`，不保留原来的财经专用名称。

## 三种路径

| 路径 | 适用场景 | 默认行为 |
| --- | --- | --- |
| 财经高精度转录 | 金融数字、术语和观点方向需要重点复核 | large-v3/beam5，TXT；明确“中高”则 large-v3/beam3 |
| 普通转录 | 普通财经、访谈、课程及其他题材 | medium/beam3，TXT；只说“转录”也采用此档 |
| 视频总结 | 只要基本观点和论据 | 优先已有文字或字幕，必要时内部识别；聊天交付总结，不默认交付或上传逐字稿 |

只有要求分析时才添加分析；同时要求转录和总结则交付两项并复用已有稿件。
题材不自动决定精度；其他题材明确要求高精度也遵循其要求。

## Cloud：推荐仓库级复用

技能完整目录为 `.agents/skills/video-transcription/`，包括 `SKILL.md`、
`agents/openai.yaml` 和 `references/`。合并到 main 后，在检出该版本的 Cloud 任务中可读取。
模型、语音识别依赖、计算配额和 GitHub 授权另行检查，安装技能不代表环境已就绪。
已有转录稿或字幕的总结不需要先安装 Whisper，也不需要 GitHub 写权限。

在技能选择器发现本技能的客户端可输入：

```text
$video-transcription 财经高精度完整转录附件，允许上传原仓库，返回 TXT。
$video-transcription 普通精度完整转录附件，不上传，返回 TXT。
$video-transcription 总结这个视频的主要观点和论据，不用逐字稿。
$video-transcription 总结这个视频，并分析论据是否支持结论。
```

若未显示技能，使用明确路径：

```text
请读取 .agents/skills/video-transcription/SKILL.md，
按其中“总结”路径处理本次视频，只给主要观点和论据，不要逐字稿。
```

## 其他聊天及项目，包括财经项目

该技能不限制项目题材。其他项目可读取本仓库的同一份技能及相应参考文件，
不需要为了财经项目另装一个财经专用技能。

仓库文件的长期入口（合并后有效）：
https://github.com/wangshen2014-collab/youtube-transcriber-cloud/blob/main/.agents/skills/video-transcription/SKILL.md

可复制到任意有 GitHub 读取能力的聊天：

```text
请从 wangshen2014-collab/youtube-transcriber-cloud 仓库 main 分支读取
.agents/skills/video-transcription/SKILL.md，
并读取当前模式需要的同目录 references 文件。
按该技能总结本次视频的基本观点和论据，不要逐字稿。
```

需要转录时把最后一句改成“普通精度完整转录附件，返回 TXT”或
“财经高精度完整转录附件，允许上传原仓库，返回 TXT”。
工具不能读取远程文件时可提供完整技能目录；只提供入口文件会缺少模式参考。
访问不到材料、没有云端音频识别能力时应说明缺项，不能仅凭技能开始或声称完成。

**合并只安装到这个仓库，不等于账号内所有聊天已注册技能。**
本机用户技能不自动同步到 Cloud。多个 Cloud 项目可共用以上读取入口，
或各自检出/安装完整技能目录；无需依赖当前聊天的历史记忆。
真正通过技能选择器跨所有聊天发现，需要客户端支持的账号级技能/插件安装，
且必须在该账号内实际安装并验证，不能用一次仓库合并代替。

## 本机副本与验收

本机安装遵循实际客户端/安装器的用户技能目录，保存完整 `video-transcription/` 目录。
本机与 Cloud 分别验收：文件完整、版本一致、参考链接可读、宿主是否发现。
当前安装器使用 `~/.codex/skills`；官方文档还列出 `~/.agents/skills`。
避免同时保存同名重复副本造成选择混淆。

检查 YAML、引用文件、三类请求的路由及上传范围即可；仅修改或安装技能不重跑历史音频。
复核保留原有预算及停止条件，不承诺准确率、费用或跨任务模型缓存。

官方说明：
- https://learn.chatgpt.com/docs/build-skills
- https://learn.chatgpt.com/docs/cloud
