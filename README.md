# YouTube Transcriber Cloud / YouTube 云端视频转文字

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
