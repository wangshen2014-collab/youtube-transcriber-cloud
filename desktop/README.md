# Windows 下载器

此目录是已安装应用的源码副本。`transcriber.py` 与 `audio_export.py` 必须放在同一目录。
现有虚拟环境依赖不变，仍使用 PyAV（av）、yt-dlp、faster-whisper。

右上角「转录文字（默认）」可切到「仅下载音频」。同一个转录范围选择器控制
下载全部/下载片段。源音频仍缓存，导出文件名使用视频标题。
完整导出保留源格式，部分导出为 AAC/M4A，时间戳从零开始。
默认输出 `C:\Youtube音频输出` 由安装目录 config.json 配置。
这里不提交本机 config.json、队列、音频或模型。

导出登记在 LOCALAPPDATA/YouTubeTranscriber/audio-exports.json，保留 7 天。
清理仅针对登记且未修改的音频；检查发生在启动与每小时。
已有 TXT/SRT、未登记文件和模型不会被删除。
