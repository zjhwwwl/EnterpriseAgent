# 本地开发与部署

## 当前支持范围

Phase 1 支持 Windows 本机启动 FastAPI 与自动化测试，不需要 Docker、PostgreSQL、Redis、Chroma 或 DeepSeek。当前没有 Dockerfile、全栈 Compose、公网部署或生产验证记录。

默认监听 `127.0.0.1`，用于本机开发。尚未提供认证和业务资源，不应把本阶段基础服务直接作为企业生产系统发布。

## Windows 本地安装

需要 Python 3.13。使用项目内 `.venv`，避免修改全局 Python 依赖。当前项目路径在 D 盘。当前工作区已有虚拟环境；首次克隆仓库时先在 PyCharm 选择 Python 3.13 并创建项目 `.venv`。若已有 Windows Python Launcher 和 Python 3.13，也可在仓库根目录执行 `py -3.13 -m venv .venv`。安装脚本使用已有解释器，不下载 Python。

推荐：

```powershell
Set-Location 'D:\Python\PycharmProjects\EnterpriseAgent'
.\scripts\bootstrap.ps1
.\scripts\check.ps1
.\.venv\Scripts\python.exe main.py
```

安装脚本先检查项目解释器为 Python 3.13，通过 `--require-hashes` 校验锁定依赖。安装和检查脚本均将 pip 缓存、TEMP/TMP 放入项目 `var/cache`、`var/tmp`。已有 `.env` 不覆盖；首次运行由 `.env.example` 创建。脚本不会安装 Docker、下载模型或调用付费模型 API。

也可以手动执行。下面假设项目 `.venv` 已存在：

```powershell
$env:PIP_CACHE_DIR = Join-Path (Get-Location) 'var\cache\pip'
$env:TEMP = Join-Path (Get-Location) 'var\tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:PIP_CACHE_DIR, $env:TEMP | Out-Null
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements/dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
.\.venv\Scripts\python.exe main.py
```

锁文件提供安装版本与分发文件哈希；开发锁包含测试、检查和可编辑构建需要的依赖。当前锁文件在 Windows Python 3.13 环境生成，Linux 安装尚未验证；后续 CI 与容器阶段会生成并验证 Linux 约束。`--require-hashes` 校验依赖文件，`--no-deps --no-build-isolation` 避免可编辑安装再次解析未锁定依赖。更新版本时应重新生成锁文件并运行检查，不能只修改声明文件。

服务启动后，另开 PowerShell：

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health/live'
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health/ready'
```

按 `Ctrl+C` 正常关闭。使用 Swagger 查看当前响应与错误契约。

## 配置约定

环境变量前缀为 `EA_`。`.env` 用于本地开发，不进入版本控制；部署环境由运行时注入配置。密钥使用 SecretStr，配置对象不能整体写入日志。

| 配置 | 行为 |
| --- | --- |
| `EA_APP_ENV` | 仅接受 development / test / production |
| `EA_HOST`、`EA_PORT` | 仓库 `main.py` 的 Uvicorn 监听配置 |
| `EA_LOG_LEVEL` | 合法日志等级，非法值启动失败 |
| `EA_DOCS_ENABLED` | 控制 Swagger / OpenAPI 文档入口 |
| `EA_CORS_ORIGINS` | JSON 数组，填写浏览器 Origin，不使用路径 |
| `EA_DATA_DIR` | 本地文件目录，默认 `var` |
| `EA_LLM_ENABLED` | 模型配置开关；Phase 1 不建立模型连接 |
| `EA_DEEPSEEK_BASE_URL`、`EA_DEEPSEEK_API_KEY` | 后续兼容接口配置；Key 默认空 |
| `EA_DATABASE_URL`、`EA_REDIS_URL` | 后续服务配置；Phase 1 不连接 |

配置错误应在启动时暴露，不悄悄回退到未预期设置。`production` 环境名称本身不表示系统已经满足生产条件，也不会自动建立未实现的安全功能。

`EA_LLM_ENABLED=true` 要求配置非空 `EA_DEEPSEEK_API_KEY`。这只验证未来模型配置的完整性，Phase 1 尚未提供模型 Client，开关不会触发模型请求。

Uvicorn 入口：

```powershell
.\.venv\Scripts\python.exe -m uvicorn enterprise_agent.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

推荐 `main.py` 启动，它使用项目 JSON 日志配置，并关闭包含原始 URL 的 Uvicorn access log。直接 Uvicorn CLI 使用其自身的启动/错误日志配置；示例关闭 access log，但不保证所有日志均为项目 JSON 格式。该命令监听参数由 CLI 决定。开发时可加 `--reload`，部署时不使用。不要依赖切换 Python 全局 PATH 来启动本项目。

## D 盘资源安排

后续需要工具时优先配置：

| 资源 | 目标位置 |
| --- | --- |
| 项目、虚拟环境、前端 node_modules | 当前项目目录 |
| 独立开发工具 | `D:\tools\` |
| Docker Desktop 程序 | `D:\tools\DockerDesktop\` |
| Docker / WSL 数据盘 | `D:\DockerData\` |
| pip、npm、模型共享缓存 | `D:\dev-cache\` 下分别存放 |
| 上传文件、索引、评估结果 | 项目 `var\` 与 `evals\results\` |

Docker 的程序位置和 WSL 数据盘分别设置；部分 Windows 组件和用户配置仍可能位于 C 盘。不能仅安装到 D 盘就认为镜像和卷数据也在 D 盘。

Docker Desktop 可管理自己的 WSL 后端，不需要为了 Docker 额外安装 Ubuntu；安装前检查虚拟化、WSL 与剩余空间，安装后验证引擎和实际数据位置。参见 [Docker Windows 安装说明](https://docs.docker.com/desktop/setup/install/windows-install/) 和 [WSL 后端说明](https://docs.docker.com/desktop/features/wsl/)。本阶段尚未执行这些安装或验证。

## 后续 Compose

Phase 2 先提供基础设施 Compose：PostgreSQL、Redis，以及随后需要的 Chroma。服务使用固定版本和健康检查，业务数据与文件采用明确的持久化位置。数据库迁移、种子和只读账号初始化作为显式步骤，避免 API 每次启动偷偷重建数据库。

Phase 9 完成 API、同仓 Worker、MCP 入口和前端的全栈打包，验证从干净环境启动、健康检查、迁移、种子、重启和持久化。Docker 检查使用实际引擎；仅生成 YAML 不算部署成功。

不得自动删除用户卷、重置业务库或清理不属于项目的镜像。可能造成数据损失的操作在文档中说明目标与影响，并在执行前取得确认。

## 模型与追踪

Phase 3 再安装并验证本地中文 Embedding / Reranker。模型缓存放 D 盘，首次下载和模型预热单独记录。CPU 内存与延迟通过本机测试确认，不预先承诺性能。

Phase 4 接入 DeepSeek，只有有效配置并启用后才运行在线调用。密钥不传给浏览器，不写日志、不提交 Git。实际调用可能产生 API 用量，记录 provider 返回的 usage。Langfuse 可选、默认关闭，接入前定义 trace 裁剪与脱敏策略。

## CI 与公网发布

Phase 9 增加 GitHub Actions，默认验证不含私密 Key 的检查，并用服务容器执行必要数据库集成。在线模型评估与日常基础 CI 分开，避免缺 Key 时伪报通过。

当前没有发布到 GitHub 或公网，也没有创建付费云资源。未来的公网部署先准备可审查构建、环境配置和验证记录，再按用户授权执行发布。
