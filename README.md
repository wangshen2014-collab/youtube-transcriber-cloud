# 音频转文字：本地下载 → 上传音频 → 云端转录

## 日常使用（推荐）

### 手机只粘贴链接（实验入口）

无需安装手机 App。登录 GitHub 后，用手机浏览器打开：

**[打开转录表单](https://github.com/wangshen2014-collab/youtube-transcriber-cloud/actions/workflows/transcribe.yml)**

选择 **Run workflow**，在 `youtube_url` 粘贴链接，`job_tag` 留空，直接运行。
默认转录全文；需要时填写 `start` / `end`。完成后在该次运行的 Artifacts 下载结果包。
这个入口由 GitHub Actions 直接下载 YouTube，不经过 Codex Cloud 的 YouTube 代理。
能否下载取决于 GitHub runner 实际访问结果，不能保证 YouTube 接受所有云端请求。

也可以在连接本仓库的 Codex 对话里发链接，说「用 GitHub 转录这个链接」：

```bash
python3 cloud_job.py --url 'https://www.youtube.com/watch?v=VIDEO_ID' --wait --download-dir outputs/github
```

链接提交只需要访问 `api.github.com`；此时无需上传音频，也不需要 `uploads.github.com`。
Codex 环境仍须允许 GitHub API 并有任务写入授权。结果下载需要 GitHub 文件重定向主机。
若 GitHub 下载也遇到登录/机器人限制，下面的本地下载再上传路径仍然可用。

**本地：** 打开 YouTube 视频转文字，右上角选择「仅下载音频」。
粘贴链接，默认下载完整音频；也可选择「自定义时间段」，再点击「下载音频」。
输出文件以 YouTube 视频标题命名，默认在 `C:\Youtube音频输出`。
片段导出为 M4A，例如 `视频标题 [00-00-30 至 00-03-30].m4a`。
完整音频复制原始音轨，不加载 Whisper；片段用现有 PyAV 导出 AAC，无需安装 ffmpeg 命令。
首次下载仍会缓存完整源音频，再导出所选范围。

**云端：** 选择连接本仓库的 Codex 环境，上传音频，直接说：

> 转录这个音频，默认完整转录，交给 GitHub 执行，返回结果并检查文字。

若只转部分，说「只转录 02:15–08:40」。本地已截取的片段在云端从 00:00 开始计时。
AGENTS.md 已包含这些日常默认操作，不需要每次粘贴长提示词。

**结果：** GitHub Actions 运行实际转录，完成后生成包含 TXT/SRT/JSON 的结果包，
原文件名保留在结果文件名中。**结果保留 60 天**，到期由 GitHub 自动删除。
结果不提交到 Git 历史，普通下载通常需要登录 GitHub。代理/插件不能直接读取时，
Codex 可通过已授权 API 下载，再解压分析；不能承诺任意浏览工具仅凭链接就能读取。

### 一条命令

提交工具仅使用 Python 标准库，不需要先在 Codex Cloud 下载模型：

```bash
# 全文（默认）：计算实际发生在 GitHub Actions
python3 cloud_job.py --audio-file '/path/to/上传音频.m4a' --wait --download-dir outputs/github

# 部分
python3 cloud_job.py --audio-file '/path/to/上传音频.m4a' --start 02:15 --end 08:40 --wait --download-dir outputs/github

# 中途关闭对话也不会停止远端计算。使用已返回任务 ID 查询/下载，不要重新提交同一份音频。
python3 cloud_job.py --job 'yt-job-返回的任务ID' --wait --download-dir outputs/github
```

现有 GitHub 登录若向任务提供可用的 Git 凭据，脚本会尝试复用。否则首次需要在
**Codex 环境的安全配置**中设置 `GH_TOKEN`，选择仅限此仓库的细粒度令牌，权限为
Contents: Read and write、Actions: Read and write（Metadata 只读）。
新建令牌可在 GitHub Settings → Developer settings → Personal access tokens → Fine-grained tokens。
不要把令牌贴进聊天、README、命令行参数或 Git 提交。
仓库公开，但开始运行任务仍需要写入授权；连接仓库不一定等于具备 API 授权。

首次需要启用仓库 Actions（若被禁用）。代码内置工作流，无需 Google 登录。
Codex Cloud 出口需要 `api.github.com`、`uploads.github.com` 和 GitHub 文件重定向主机，
GitHub Actions runner 另外下载模型；Codex Cloud 不需要访问 YouTube。

### 保留时间和公开范围

- 本地源音频缓存：沿用应用可选保留策略，默认最后使用后 7 天。
- 本地导出音频：导出后 7 天，到期在应用启动/每小时检查时永久删除；
  仅处理新版本登记且未被用户修改/替换的导出文件，不清理 TXT/SRT 或其他文件。
  应用未运行或电脑关闭时无法执行清理，会在下次运行处理；不保证整点物理删除。
- 输入音频：通过当前**公开仓库**的临时 Release 传输，属于公开文件。
  工作流结束后立即删除该 Release 与专用 tag，失败遗留每天检查并清理超过 7 天的记录。
  GitHub 定时任务可能延迟或被停用，因此这是清理策略，不是精确到秒的输入文件 TTL。
- TXT/SRT/报告：通过 Actions Artifacts 原生 `retention-days: 60` 保存。
  仓库设置的最大保留期限应至少 60 天；实际 `expires_at` 在查询结果中返回。
- 文字内容不写进运行日志/Git 历史。运行日志仍可能包含文件名、错误、语言和计时；
  它遵循仓库独立的日志保留设置。若需日志也 60 天，在 Settings → Actions → General 设置。
- 下载到手机、贴进聊天或保存在其他服务的副本，遵循各自保存规则，不会随结果包一起消失。
- 当前仓库公开。只提交你接受公开的音频/转录内容。

GitHub Runner 使用 240 分钟任务上限。失败时查看 Actions 日志；任务未成功不得声称已转录。
GitHub 环境是否支持下载 YouTube 尚未验证；日常入口直接用已上传音频。

## 原始 CLI 与开发说明

将 YouTube 音频交给 faster-whisper 转录，输出保留原语言的 TXT 和 SRT。
也支持直接读取上传到工作区的本地音频，跳过 YouTube 下载。
这是原 Windows 应用的独立命令行版本，适合在 Linux / Codex Cloud 进行实验。
不需要打开桌面窗口，也不调用 OpenAI 转录 API。

## 当前状态

- 已实现：YouTube 音频下载、按视频 ID 缓存、指定时间段、检查点续做、TXT/SRT、JSON 运行报告。
- 本地测试与真正的云端实测是两回事；请以任务生成的报告判断运行是否成功。
- 尚未实现：Google Drive 自动上传、ChatGPT Library 自动保存、跨云端任务共享队列、定时常驻服务。
- 没有假定云端有 GPU、固定 CPU 数、无限运行时间或无限缓存。

## 安装

建议 Python 3.11。Linux 在仓库根目录运行：

```bash
bash scripts/setup.sh
```

依赖保留 faster-whisper 1.2.1、CTranslate2 4.4.0（与现有本机版本一致）。
旧版 CTranslate2 依赖 pkg_resources，因此限制 setuptools<81。
PyAV 固定为 16.0.1；19.x 移除了 faster-whisper 1.2.1 解码时仍使用的 `metadata_errors` 参数。
yt-dlp 跟随安装时可用版本，实际版本写入报告；无需改变推理依赖即可更新它：

```bash
.venv/bin/python -m pip install --upgrade yt-dlp
```

## 使用

```bash
# 完整视频，默认 multilingual medium / 6 threads / beam 3 / CPU int8
.venv/bin/python transcribe_cli.py --url 'https://www.youtube.com/watch?v=VIDEO_ID'

# 只转录 2分15秒至8分40秒，明确中文
.venv/bin/python transcribe_cli.py --url 'https://www.youtube.com/watch?v=VIDEO_ID' \
  --language zh --start 02:15 --end 08:40

# 本地音频，自动检测语言，转录 00:30–03:30
.venv/bin/python transcribe_cli.py --audio-file '/path/to/uploaded-audio.mp3' \
  --start 00:30 --end 03:30
```

`--url` 与 `--audio-file` 必须且只能提供一个。本地文件直接交给音频解码器，
不调用 yt-dlp、不复制到 YouTube 音频缓存。文件必须存在、可读且非空。
两种输入都使用 multilingual medium / CPU int8 / 6线程 / beam 3，
`condition_on_previous_text=True`，VAD 关闭；默认自动检测语言。

把 VIDEO_ID 换成真实的 11 位视频 ID。支持 watch、youtu.be、shorts、live 链接。
时间可以是整数秒、分:秒、时:分:秒。结束留空表示到结尾，超过视频长度则截到结尾。
SRT 时间戳对应**原始视频时间**，不会从片段的零秒重新计时。
自动语言检测只分析所选音频。首次仍下载并解码完整音频，然后截取所选范围；
缩小范围节省的是模型转录计算，不保证省下载流量或完整音频解码内存。
本地音频同样先完整解码再裁剪；SRT 时间戳对应输入音频的原始时间。

每次完成的新任务使用唯一目录：

```text
outputs/<时间戳-随机ID>/
  <视频ID>.txt
  <视频ID>.srt
  run-report.json
```

报告包含模型参数、依赖版本、检测语言、音频长度、分阶段耗时和 Linux 进程峰值内存。
模型首次下载的时间算在 model_load；第二次需与首次冷启动区分。
本地音频输出文件名为 `audio-<内容SHA256前24位>.txt/.srt`，报告记录来源路径、
完整 SHA256、`download_skipped: true` 和零下载耗时。加载模型仍需已缓存模型或模型下载网络。
每次调用的耗时独立记录，续做报告不是整个历史任务的累计耗时。

## 暂停与续做

按 Ctrl+C 或发送 SIGTERM：在下载/模型加载返回、或语音片段保存后暂停，退出码 75。
也可指定 `--pause-file state/pause.flag`，创建该文件来请求暂停，恢复前删除它。
然后用相同链接、模型、语言、线程、beam、开始结束时间重新运行。
本地音频用相同 `--audio-file` 和参数续做；检查点按完整文件内容 SHA256 区分，
相同内容改名或移动后仍可续做，内容变化则创建新任务，避免混入旧文件的转录。
检查点保存在 state/checkpoints/，音频在 state/audio-cache/，模型在 models/。
可通过 --state-dir、--model-dir 或 YT_STATE_DIR、YT_MODEL_DIR 配置路径。

每个语音片段落盘后记录续做位置，恢复时以最近完成位置裁剪音频，
并用最近三段文字作提示。恢复后的分段/措辞可能与不中断运行不同。
强制杀进程可能丢失正在计算的片段，下次会从最近已保存的片段重算。
同一 state-dir 加进程锁；避免多个进程同时改写缓存和检查点。
完整 TXT/SRT 在任务完成后生成，暂停时主要保留 JSON 检查点。

云端试验缓存**目前不自动过期清理**，避免删掉实验中的音频。
需要清理时只处理已确认不再需要的任务缓存；模型与字幕是不同目录。
原 Windows 应用的保留策略未作修改。

## Codex Cloud 测试步骤

1. 在支持该功能的账户创建 Cloud environment，连接此仓库。
2. 让 Codex 执行 setup.sh，并检查导入和测试结果。
3. 配置环境网络：需要 Python 包仓库、模型下载服务、YouTube 及其音频 CDN。
   实际主机可能随重定向变化，按日志检查失败原因，不绕过网络或站点限制。
4. 提交一个普通话公开视频，先只转录 2–3 分钟，必须实际产生 TXT/SRT。
5. 检查文字、语言、SRT 时间戳和 run-report.json。
6. 对原来异常慢的视频测试同一范围，比较模型加载与转录耗时。
7. 测试一次暂停/恢复、同一视频音频缓存复用、第二个全新云端任务的模型缓存情况。
8. 云端开始运行后关闭自己的电脑，从手机查看是否独立完成。
9. 验证手机实际能取回文件；不要把任务目录自动等同于 ChatGPT Library。

可直接给 Codex 的提示：

> 请按 AGENTS.md 运行这个仓库，转录以下链接的 00:30 到 03:30，
> 语言自动检测，使用默认 medium/6线程/beam3。必须真正执行转录，
> 检查 TXT/SRT 内容与时间戳，返回文件和运行报告。不要修改默认参数。

仓库代码不需要 OpenAI API key。Codex 任务自身的使用额度由账户决定。
云端网络下载可能被 YouTube 拒绝；没有浏览器 Cookie 或登录自动化。
若模型下载或网络失败，不能报告为成功的转录测试。

## Google Drive / ChatGPT 存储

先跑通云端转录，再接入上传。Python 不会自动继承 ChatGPT 的 Google Drive 授权。
需要独立 OAuth 或已授权的云端文件工具。令牌必须放安全配置中，不提交 Git。
目前 CLI 只生成文件；需显式导出或用支持的工具保存到目标存储。

官方环境说明：https://learn.chatgpt.com/docs/environments/cloud-environments
