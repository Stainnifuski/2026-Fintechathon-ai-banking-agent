# NOVA 本地语音输入

NOVA使用SenseVoice在本地CPU服务中完成录音转文字，不自动发送、不调用 Agent、不确认业务、不做语音朗读。识别结果追加到当前输入框，由本人检查名称、代码、金额后发送；保留当前对话和任务上下文。切换会话、关闭面板或取消识别会丢弃迟到结果，不写入另一个对话。

## 默认部署与使用

已有 `.env` 和数据库原样保留。Windows 双击根目录 `start_bank.cmd`，或执行 `powershell -NoProfile -File scripts/start_bank.ps1`；Linux/macOS 执行 `bash scripts/start_bank.sh`。两者会构建现有银行前后端和独立 `sensevoice` CPU 服务，语音转写无需GPU或额外语音API密钥；发送后的自然语言理解与任务规划仍须按[部署说明](deployment.md)配置DeepSeek API。首次构建需要访问 Docker Hub、GitHub、PyTorch CPU wheel 源及 PyPI；首次模型下载需要访问 Hugging Face。模型缓存到独立命名卷，之后复用，不进入 Git。首次准备不能承诺半小时或离线完成。

进入 `http://localhost:3000`，登录后打开 NOVA。模型首次下载／加载时显示提示，可继续打字，准备好后点击“重试语音”。点击麦克风开始录音，再次点击结束；最长45秒、4MB。中文、粤语、英文、日文、韩文由模型自动识别。没有声音、格式错误、过长、服务忙或超时会明确提示，不发送对话。原输入不会被清空，超出输入框2000字限制时拒绝追加，不截断金额或代码。

录音需浏览器支持 `getUserMedia` 和 `MediaRecorder`，并允许麦克风权限；优先使用新版Chrome／Edge。`localhost` 可直接使用，远程访问必须配置HTTPS，否则浏览器不允许录音。拒绝权限时按浏览器地址栏的站点设置重新允许。浏览器不支持、权限拒绝或识别服务停机时，文字聊天继续可用。

默认启动包含语音服务。若排障或暂时只体验文字，可跳过语音构建：PowerShell 使用 `scripts/start_bank.ps1 -SkipVoice`；Linux/macOS 使用 `bash scripts/start_bank.sh --skip-voice`。不会删除已缓存模型或修改已有账本。

无需Docker时，使用[本机原生部署的SenseVoice步骤](deployment.md#55-sensevoice终端三仓库根目录)，在独立Python环境运行同一语音适配服务。

## 独立控制与排障

以下是公用配置命令。本机若有 `.deps/bank-runtime.override.yml`，必须在 `-f docker-compose.yml` 后、`build`／`up`等子命令前加入 `-f .deps/bank-runtime.override.yml`，以保留原账本选择；启动器自动处理这一点，切勿因升级更换数据库。

```bash
docker compose -f docker-compose.yml build sensevoice
docker compose -f docker-compose.yml up -d --no-deps sensevoice
docker compose -f docker-compose.yml logs --tail 60 sensevoice
docker compose -f docker-compose.yml restart sensevoice
```

模型初始化失败后修复网络再重启服务。`sensevoice` 为 unhealthy 不会使银行健康检查失败，也不阻止文字聊天。不要通过 `down -v` 清理故障，它会删除其他数据库和模型卷。

同一Compose网络内地址为 `http://sensevoice:50000`，银行后端通过 `SENSEVOICE_URL` 连接；Docker配置已提供默认值。当前Compose同时将语音端口仅映射到宿主机回环地址 `127.0.0.1:50000`，不绑定所有网卡。原生Python后端连接该服务时使用 `SENSEVOICE_URL=http://127.0.0.1:50000`。如有覆盖配置，以合并后的实际映射为准。前端只调用3000同源接口，不接触内部地址或密钥。

`GET /api/v1/nova/voice/status`：登录后读取识别就绪状态。`POST /api/v1/nova/voice/transcriptions`：登录后上传原始音频body，Content-Type为浏览器实际录音格式，成功返回 `{text,model}`。支持WebM、MP4、OGG、WAV、MP3，内部FFmpeg内存解码为16kHz单声道；客户端不能传入模型路径、命令、用户身份或执行参数。同用户一次一个请求，银行代理全局最多两个，CPU服务串行识别，繁忙立即提示重试而不排长队。

## 版本、数据与隔离

- 复用官方 [SenseVoice](https://github.com/QwenAudio/SenseVoice) 模型实现，源码固定为 `586bbdb2b052f4fe19322581825228b6810a9fb3`；其MIT许可证保留在镜像及 `services/sensevoice/UPSTREAM_LICENSE.txt`。本项目的 `server.py` 是有界上传／就绪／内存解码适配，不是自行训练的识别模型。
- `FunAudioLLM/SenseVoiceSmall` 模型固定为 `3847d57b6bdf2dd8875cb1508d2af43d80a16bf7`，来自官方模型库；模型许可按该版本模型卡单独适用。模型权重不提交、不混入基金离线包。
- CPU镜像固定基础镜像digest、torch／torchaudio匹配CPU版本及 `requirements.lock`。不采用上游CUDA基础镜像，也不直接解析其浮动requirements；锁定FunASR并注册固定源码，禁用模型仓库在运行时安装requirements或导入未固定的远程代码。只支持已验证的平台，其他CPU架构首次需自行验证依赖wheel可用性。
- 录音仅经浏览器→本人银行代理→内部本地识别服务处理，不提交外部识别API。音频不落盘，转写接口不持久保存音频或文字，不记录音频／转写正文日志。用户手动发送后，文字才进入原NOVA聊天保存及模型处理流程。银行既有访问审计仅记录路由、状态与耗时，不记录音频body。
- 独立模型缓存卷及运行环境，不导入银行数据库或密钥。CPU默认2线程；没有将官方GPU吞吐数据冒充本机性能。部署与实测范围见本期验证记录。

## 可提交范围

提交 `services/sensevoice/`、Compose与启动器、银行语音代理、前端录音代码、测试和本说明即可。不要提交 `.env`、`.deps`、模型缓存／权重、运行数据库、测试录音或本人音频。队友拉取同一版本后重建镜像，首次联网自动准备模型，不复制任何本机私有路径。
