# 当前源码部署说明

本项目是比赛模拟银行。资金、交易、商户与短信通道均为模拟；公开基金资料和NAV另有来源及日期，不代表接入真实银行或支付。正式体验入口为 `http://localhost:3000/`。

## 1. 准备源码与配置

需要 Docker Engine／Docker Desktop、Compose v2及首次构建所需网络，启动前让Docker处于运行状态。源码须包含 `backend/`、`frontend/`、`config/`、`scripts/`、`services/sensevoice/`、`data/wealth/wealth-compact-806-v1.zip`、`docker-compose.yml`、`.env.example`。默认启动包含SenseVoice CPU语音服务，首次需下载模型，详见 [语音部署](voice_input.md)。

在源码根目录首次复制 `.env.example` 为 `.env`。已有 `.env` 不覆盖；真实Key、私人配置和业务库不放入提交压缩包。已有账本必须保留原加密密钥及持久卷，不通过删除数据库解决启动失败。

Docker方案无需在宿主机另装Python、Node或PostgreSQL。解压后进入包含 `docker-compose.yml` 的目录，先执行：

```text
docker --version
docker compose version
docker info
```

三条均应正常返回；最后一条不能出现无法连接Docker守护进程的错误。Windows建议使用Docker Desktop的Linux容器模式。若提示找不到docker，完成安装后重新打开终端；不要先运行启动脚本。

Windows PowerShell首次准备配置：

```powershell
if (!(Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
notepad .env
```

Linux／macOS终端首次准备配置：

```bash
test -f .env || cp .env.example .env
nano .env
```

也可使用任意文本编辑器。保留示例其余配置，至少将以下两项占位符换成自己的值，不把中文说明原样当作密钥：

```dotenv
LLM_MODE=auto
LLM_API_KEY=替换为你可用的DeepSeek密钥
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
INTERNAL_BANK_TOKEN=替换为至少32字符的独立内部随机密钥
```

内部密钥可在PowerShell执行 `[guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')`，或在有OpenSSL的终端执行 `openssl rand -hex 32` 生成，再将输出填入 `INTERNAL_BANK_TOKEN`。它不是DeepSeek密钥；不要将输出公开或截图提交。保存文件名为 `.env`，不要成为 `.env.txt`。

`LLM_MODEL` 使用当前服务预设，实际可用模型与权限须与服务商账户匹配；有需要按 [模型配置说明](model_configuration.md) 修改。完整体验NOVA自然语言理解、问答和任务规划需要可用DeepSeek API及到服务地址的网络；无Key时auto进入离线模式，手动工作台仍可用，不能据此判定AI功能已通过。模型失败不代表业务操作一定未执行，应核对原任务和操作记录。

JWT／知识签名密钥留空时由服务端在受保护的持久目录生成；多实例需配置一致的强密钥。默认短信仅为本地模拟发送，“查看模拟短信”及订阅测试恢复按当前比赛范围保留，不声称生产级认证。数据库统一为PostgreSQL `banking_agent`；Compose内连接地址由服务配置注入，不要将本机localhost连接串误当容器地址。

## 2. 启动

Windows在这份源码根目录执行 `start_bank.cmd`，或：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_bank.ps1
```

Linux／macOS执行 `bash scripts/start_bank.sh`。启动器复用本机 `.deps/bank-runtime.override.yml`（若存在），构建前后端和SenseVoice语音服务、安装匹配固定资料并等待银行服务健康。语音模型有独立的下载／加载就绪状态，需按下文单独确认。源码压缩包没有Git元数据时启动器使用 `source-archive` 标识。固定资料与升级说明见 [安装与旧库保护](wealth_compact_upgrade.md)。

首次构建需要拉取镜像及下载软件依赖，耗时取决于网络和机器；不要把构建期间尚未开放网页当作启动完成。默认部署包含语音。仅在排障或暂时只体验文字时，可跳过语音构建：

```powershell
# Windows PowerShell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_bank.ps1 -SkipVoice
```

```bash
# Linux / macOS
bash scripts/start_bank.sh --skip-voice
```

这不会删除既有语音服务。后续需要语音时重新运行不带跳过参数的启动器。

更新源码或 `.env` 后重新执行启动器。仅重启旧容器不会加载后来改动的应用源码，`docker compose restart`也不能替代配置变更后的容器重建。手动Compose操作必须沿用运行容器的 `com.docker.compose.project.config_files` 标签所列配置，不能静默切换账本。

## 3. 确认启动结果并体验

打开 `http://localhost:3000/health`，检查服务健康与 `portfolio_ready=true`；`source_revision`用于识别源码版本。健康通过只说明服务和资料就绪，不能证明DeepSeek密钥有效。

成功判据按顺序核对：

1. 启动命令正常结束并输出 `Ready: http://localhost:3000`，没有构建或健康检查错误。
2. `/health` 返回JSON，`status` 为 `ok`、`portfolio_ready` 为 `true`。Windows还可执行 `Invoke-RestMethod 'http://localhost:3000/health'`；Linux／macOS可执行 `curl -fsS http://localhost:3000/health`。
3. 首页可加载并登录；若仅服务健康但资料未就绪，不进行理财验收。
4. 按下文发送一个真实查询，并确认没有未启用模型、认证、余额不足或网络错误。服务商返回的错误要先解决，再判断AI体验。

访问首页，展开“演示账号帮助”；通用种子示例为 `13800000001 / password123`，仅适用于已按当前种子初始化的库。已有自定义库使用原账号，不通过重置账本找回演示账号。新用户按页面注册。

语音就绪检查：登录后打开NOVA，等待SenseVoice模型下载／加载完成；如页面提示初始化中，稍后点“重试语音”。允许麦克风权限，录制一句“查看我的账户余额”，结束后应将识别文字填入输入框，尚未发送；由本人核对后再发送。首次下载失败或语音未就绪不影响文字输入，但不能据银行健康就绪认定语音通过。详细网络、权限和排障步骤见 [语音输入说明](voice_input.md)。

登录后可先查询“现在北京时间几点”，再从“更多示例”选择查询。能收到基于工具结果的模型回复才构成一次真实连接体验；不要用离线或模拟测试结果代替它。交易仍须本人确认与TOTP或模拟短信验证，不因登录或点击示例而自动授权。理财需本人填写并确认风险问卷，不能由部署脚本代填。

## 4. 常见问题与停止

| 现象 | 处理 |
|---|---|
| Docker不可用或3000／8000端口冲突 | 启动Docker，检查端口与Compose状态，不停止不相关进程 |
| 模型未启用 | 检查Key不是占位符、`LLM_MODE`不是offline；配置修改后重建容器 |
| 密钥认证、额度或网络错误 | 核对服务商账户、接口地址、模型权限和网络；不在日志或截图中暴露Key |
| 理财资料未就绪 | 查看backend启动日志和固定资料包完整性，沿用升级及备份指引，不删除卷 |
| 验证码错误或安全锁定 | 按页面要求使用当前操作的有效验证码，等待锁定结束，不绕过MFA |
| 请求中断或结果未知 | 先查原任务及操作记录，不能将超时直接理解为未执行并重复提交 |

全新部署且没有本机覆盖文件时，可在根目录排查及停止：

```text
docker compose -f docker-compose.yml ps
docker compose -f docker-compose.yml logs --tail 100 backend frontend
docker compose -f docker-compose.yml stop
```

若存在 `.deps/bank-runtime.override.yml`，每条命令均在 `-f docker-compose.yml` 后增加 `-f .deps/bank-runtime.override.yml`。既有运行环境若使用其他配置，先查容器 `com.docker.compose.project.config_files` 标签，沿用其全部配置；不要靠改库名、删卷或重新初始化绕过错误。日志对外分享前先遮盖密钥和个人资料。

重新启动仍使用第2节启动器。不要执行删除卷的命令；PostgreSQL账本、持久密钥和本机固定资料／备份是不同存储，应一起保留。日常体验服务、独立开发预览和隔离测试环境应分开。逐步体验见 [评委操作用例](../testing/test_cases.md)，代码测试命令见 [开发者测试索引](../testing/automated_tests.md)。本说明按启动脚本和既有运行记录核对，本轮未另建全新机器做完整部署验收。
