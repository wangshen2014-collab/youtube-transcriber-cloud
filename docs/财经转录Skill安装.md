# 财经转录 Skill：安装与作用范围

维护日期：2026-11-22。技能名：`finance-transcription`。

## 当前仓库安装

技能位于 `.agents/skills/finance-transcription/SKILL.md`，界面元数据位于同目录的 `agents/openai.yaml`。
按官方规范，这是仓库级技能目录。它是流程说明，不包含Whisper模型、云端算力、GitHub凭据，也不会自动取得上传授权。

打开本仓库的Codex任务后，可输入：

```text
$finance-transcription 中高精度完整转录本次附件。
允许上传 wangshen2014-collab/youtube-transcriber-cloud 的 transcripts 分支，返回TXT。
```

若托管客户端的技能选择器没有显示它，使用明确路径：

```text
请读取 .agents/skills/finance-transcription/SKILL.md，并按该技能完整转录附件。
中高精度，允许上传原仓库，返回TXT链接。
```

`AGENTS.md` 已提供同样的显式调用入口。没有调用本技能的普通请求仍遵循原来的medium/beam3默认。
仓库目录符合规范，不等于当前托管平台已把它注册进账号的Skill列表；必须区分文件安装、CLI发现和平台目录发现。

## 本机、云端与账号级的区别

| 方式 | 位置与作用范围 |
| --- | --- |
| 仓库级 | 本仓库的 `.agents/skills/finance-transcription/`；技能提交并合并进所选分支后，新克隆/检出的本仓库可取得文件。本机和云端都可持有这份仓库文件，但具体客户端的自动发现要验证。 |
| 本机用户级 | 用户电脑的 `~/.agents/skills/finance-transcription/`；作用于那台机器上支持该目录的Codex项目，不自动复制到云端机器。 |
| 当前云端文件 | 只改当前工作区，不能据此保证全新环境会保留；持久复用应依赖已合并的仓库文件或受支持的插件分发。 |
| 插件分发 | 官方推荐用于超出单仓库的可安装分发；需要在相应客户端或账号中实际安装并验证，不是把文件放到GitHub就自动启用。 |

安装位置和计算位置不同：本机存有Skill，不表示要用本机CPU转录。本技能仍要求在可验证的Codex Cloud执行器中计算。普通聊天若没有执行器，仅有Skill不能执行语音识别。

## 可选：手工安装到本机用户目录

先把本仓库 `.agents/skills/finance-transcription/` 整个目录下载或克隆到本机，再复制到用户技能目录。无需ZIP，无需管理员权限，也不需要改已安装的Windows视频下载程序。

Windows PowerShell示例；把来源路径替换为真实目录：

```powershell
New-Item -ItemType Directory -Force -Path "$HOME\.agents\skills" | Out-Null
Copy-Item -LiteralPath '本机仓库完整路径\.agents\skills\finance-transcription' -Destination "$HOME\.agents\skills\finance-transcription" -Recurse
```

如果目标已存在，先比较并备份自己的修改，不盲目覆盖。安装后检查技能列表或显式调用；若客户端未刷新，重启该客户端。
当前云端代理没有访问用户本机文件系统，不能把在云端复制文件说成已经装到了用户电脑。

## 复核与成本控制

1.0修订为1.1，补充了主模型启动次数、复核秒数、按风险排序和停止重跑的条件。
精度档是可复现配置约定，不是准确率或费用承诺；机器复核不是人工听写。
只审核或安装Skill不需要重跑历史视频。应校验YAML、目录、提示词、CLI参数，以及是否实际被当前宿主发现。

本机安装、平台账号级插件注册、全新云端聊天发现属于不同验收项，不能用一次本地文件检查替代。

## 官方依据

- [Skill格式、发现路径和调用方式](https://developers.openai.com/codex/skills)
- [插件分发和安装说明](https://developers.openai.com/codex/plugins/build)
