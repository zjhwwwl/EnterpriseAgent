# 本地开发与部署

## 当前支持范围

Phase 2 支持 **Windows 本机 FastAPI + Docker 中 PostgreSQL / Redis**。基础设施已实际运行，API 仍用项目 Python 虚拟环境；没有应用 Dockerfile、全栈 Compose、CI 运行、公网发布或生产部署证明。

默认监听 127.0.0.1。公开 seed 账号只适合本地模拟演示。不会连接真实支付或退款，也不会自动创建付费云资源。

## 开发环境与 D 盘

本机已安装 Docker Desktop 4.95.0，Engine 29.8.2、Compose 5.6.0；Linux 容器为 PostgreSQL 17.11-bookworm 和 Redis 8.4.7。

| 资源 | 位置 |
| --- | --- |
| 项目、Python 虚拟环境 | D:\Python\PycharmProjects\EnterpriseAgent |
| Docker Desktop 程序 | D:\tools\DockerDesktop |
| 实际 Docker / WSL 数据盘 | D:\DockerData |
| pip 缓存、TEMP / TMP | 项目 var/cache 与 var/tmp |
| 本地秘密、测试报告 | 项目 var，Git 忽略 |
| 后续模型 / npm 共享缓存 | 优先 D:\dev-cache 下分目录 |

程序目录和实际 WSL 数据盘已分别检查，避免仅移动程序而镜像仍占 C 盘。Windows 组件和少量用户配置仍可能位于 C 盘。Docker Desktop 管理自身 WSL 后端，没有为本项目另外安装 Ubuntu。安装源及配置依据为 [Docker Windows 官方说明](https://docs.docker.com/desktop/setup/install/windows-install/)。

## 首次启动

需要可执行的 Python 3.13 与运行中的 Docker Desktop。已有项目 .venv；首次克隆先在 PyCharm 配置 Python 3.13 虚拟环境。已有 Windows Python Launcher 时也可执行 `py -3.13 -m venv .venv`。bootstrap 不下载 Python 或安装 Docker。

```powershell
Set-Location 'D:\Python\PycharmProjects\EnterpriseAgent'
.\scripts\bootstrap.ps1
.\scripts\init-infra.ps1
.\scripts\migrate.ps1
.\scripts\migrate.ps1 -TestDatabase
.\scripts\seed.ps1
.\scripts\check.ps1
.\scripts\run.ps1
```

| 步骤 | 行为 |
| --- | --- |
| bootstrap | 检查 Python 3.13，按哈希安装依赖，可编辑安装项目；已有 .env 不覆盖 |
| init-infra | 生成 PostgreSQL / JWT 随机秘密，不覆盖已有文件；调用 Compose 并等待健康 |
| Compose | 仅 PostgreSQL / Redis，D 盘持久化目录和健康检查 |
| migrate | 默认迁移开发库；-TestDatabase 迁移独立测试库；-Check 检查模型差异 |
| seed | 默认本机当天日期，以 UTC 零点为锚；可 -AsOf / -Seed / -Orders |
| check | 导入本地配置，强制真实 PostgreSQL，Ruff、pytest、pip check、JSON / JUnit |
| run | 注入应用配置，移除迁移与测试 URL，运行 main.py，不自动迁移 / seed |

安装与检查脚本将 pip 缓存和临时目录放 D 盘项目 var。依赖使用 requirements 锁文件与 --require-hashes；可编辑安装使用 --no-deps --no-build-isolation，避免再次解析未锁定依赖。锁文件的 Windows Python 3.13 安装已按本阶段检查执行；Linux 应用依赖仍待全栈阶段验证。

运行依赖及 JWT 配置错误在启动时暴露；database / Redis 的暂时故障以就绪检查或 API 安全错误报告，不把未配置业务数据库默认为就绪。

## 数据库角色、连接与秘密

基础设施只绑定 loopback：

| 服务 | 本机连接 |
| --- | --- |
| PostgreSQL | 127.0.0.1:55432 |
| Redis | 127.0.0.1:56379 |
| API | 127.0.0.1:8000 |

本地密码、JWT Secret 和完整 URL 位于 **var/infra/**、**var/infra-secrets.env**，均被 Git 忽略；.env 也被忽略。init-infra 不覆盖已有文件，run / migrate / seed / check 会导入该私有文件。run 另外清除 API 不需要的迁移与测试 URL。空示例 .env 不包含业务数据库，直接 python main.py 此时 readiness=503；自行管理配置时可以直接启动，但须注入正确普通应用账号。

Redis 当前只绑定本机 loopback，不配置 Redis AUTH，适合本机隔离演示；不能把此配置直接暴露公网。示例不含实际数据库或 JWT 凭据，不打印 Compose 渲染配置、完整环境、秘密文件或驱动连接错误；命令使用文件 / 环境，秘密不放参数。

| 角色 | 用途 | 权限 |
| --- | --- | --- |
| ea_local_admin | 本地迁移、seed、角色初始化 | 初始化账号，不作为 API 连接 |
| ea_app | 开发 API | 普通非 owner，无建表和政策修改权限 |
| ea_test_app | 独立测试库 API / Service | 普通非 owner，不能 CONNECT 开发库 |

开发与测试使用独立数据库和角色，撤销公共 CONNECT 后分别授权。初始化账号权限不可作为业务 API 的权限证明。政策发布版本不可修改、审计只追加，以及退款唯一约束均由真实 PostgreSQL 验证。

测试只能针对显式测试库。不要将 EA_TEST_DATABASE_URL 指向开发库或任何已有业务库；测试事务和夹具会在测试库创建其专用记录。

## 配置说明

配置前缀 EA_，.env 用于本地开发；操作系统环境变量优先。敏感字段使用 SecretStr，数据库引擎关闭参数日志，日志保护已知秘密值。

| 配置 | 用途 |
| --- | --- |
| EA_APP_ENV | development / test / production |
| EA_HOST / EA_PORT | main.py 监听 |
| EA_LOG_LEVEL | 合法日志等级 |
| EA_DOCS_ENABLED | Swagger / OpenAPI 开关 |
| EA_CORS_ORIGINS | 精确 Origin JSON 数组 |
| EA_DATA_DIR | 文件目录，默认 var |
| EA_DATABASE_URL | API 普通账号，postgresql+psycopg |
| EA_MIGRATION_DATABASE_URL | 迁移与 seed 账号 |
| EA_TEST_DATABASE_URL | 独立测试库普通账号 |
| EA_TEST_MIGRATION_DATABASE_URL | 独立测试库迁移账号 |
| EA_REDIS_URL | 登录限流；配置后依赖不可用则登录 503 |
| EA_JWT_SECRET | 至少 32 字符，配置业务数据库时必须填写 |
| EA_JWT_EXPIRE_MINUTES | 默认 30 分钟 |
| EA_LOGIN_MAX_ATTEMPTS / EA_LOGIN_WINDOW_SECONDS | 默认单 IP 10 次 / 60 秒 |
| EA_DEMO_PASSWORD | 仅模拟 seed，公开默认 DemoOnly!2026 |
| EA_LLM_ENABLED / EA_DEEPSEEK_API_KEY | 预留，Phase 2 无模型 Client 或在线请求 |

production 字样不会自动提供生产认证方案或公网保护。模拟 seed 在 production 环境拒绝运行。Redis 未配置时登录仍可用于本机演示，但没有登录限流，不应在文档中把此时测试算作 Redis 边界已成立。

## 种子与时间

seed.ps1 默认使用本机当天日期、seed=42、500 订单，该日期以 UTC 零点为数据锚。本阶段实际使用 -AsOf 2026-10-12，得到 25 用户、30 商品、500 订单、1016 明细、456 物流、19 退款及工单、5 文档、41 审计。首笔 seed 在事务中执行并用 advisory lock 串行，已有 demo_customer 时返回现有统计，不覆盖或清空。

可通过 seed.ps1 的参数指定新的 UTC 基准日。业务事实在相同日期、随机种子和空库上可复现，密码 salt 和数据库序列值不属于业务事实比较目标。种子时间锚为 UTC 当日零点，不是本机本地时区零点。

API 退款资格使用当前 UTC 时间，固定数据会随日期超期；测试用注入时钟固定基准。对已 seed 数据改参数不会重新生成，如需另一批基准日，应创建新的本地库并显式迁移，不自动销毁现有卷。

## 健康、验证和关闭

```powershell
docker compose --env-file var/infra-secrets.env -f deploy/compose.yaml ps
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health/live'
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health/ready'
```

ready=200 要求 application、database、已配置 Redis 全部通过。数据库检查迁移版本、业务所需权限和运行角色；未配置、未迁移、缺少必要权限或含库/表 owner、超级用户、订单状态/审计超额写权限时均为 503。结果不包含连接信息，模型 / Chroma 不在当前范围。

完整检查执行 `scripts/check.ps1`，默认导入本地配置并传 --require-postgres，JUnit / JSON 保存为 var/test-results/phase2.xml 与 phase2.json。缺少测试库时不能伪报完整通过。`check.ps1 -SkipPostgres` 显式排除 PostgreSQL；直接 pytest 未配置测试库时会跳过对应项，两者均不能替代完整阶段验收。见 [阶段记录](phase-2.md)。

交互式 API 用 Ctrl+C 关闭。本次工作区已在 8000 保留后台 Demo API，启动另一个实例前可执行以下脚本；它根据 var/runtime/api.pid 校验仍是当前项目虚拟环境的 main.py，再停止该进程树，不按端口杀任意程序：

```powershell
.\scripts\stop.ps1
```

首次克隆没有这个 PID 文件时不会存在本次后台进程。重启使用 run.ps1。

本轮 stop.ps1 只完成静态解析和实际登记 PID 身份检查，保留的 Demo API 没有被该脚本停止；真实测试服务器进程树清理与停止监听由独立 e2e 用例验证，不能混为 stop.ps1 的执行记录。

暂时停止基础设施使用：

```powershell
docker compose --env-file var/infra-secrets.env -f deploy/compose.yaml stop
```

该命令保留卷与数据。不自动使用 down -v、drop database、prune 或清理不属于项目的文件。卷删除和已有库重置会丢数据，需要明确目标和确认。

## 后续交付

Phase 3 增加 Chroma、本地模型和索引；模型缓存放 D 盘，真实检查兼容、内存和预热时间。Phase 4 接入 DeepSeek，实际 usage 由 API 响应取得；密钥不传浏览器。Langfuse 可选、关闭，接入前裁剪 trace 内容。

Phase 9 完成 API / Worker / 前端镜像、全栈 Compose、Linux 依赖验证与 GitHub Actions，检查从干净环境启动、迁移、seed、重启和持久化。当前仅基础设施 YAML 和实际容器可用，不等同于全栈部署。

当前没有推送 GitHub、部署公网或创建付费云资源。未来发布先完成可审查构建和配置，再按用户授权执行。
