# Phase 1 验收记录

日期：2026-10-12。环境：Windows 11、项目内 Python 3.13.15 虚拟环境。

## 实际交付

标准 src 包布局、纯应用工厂、ASGI/CLI 入口、Pydantic Settings、SecretStr、JSON 日志和递归脱敏、请求 ID、明确 Origin 的 CORS、统一应用错误、服务信息与健康接口、文档开关、依赖锁文件、D 盘缓存脚本，以及 README、六份要求文档和 MIT 许可证。

## 执行证据

在仓库根目录运行的两条脚本均返回退出码 0：

```powershell
.\scripts\bootstrap.ps1
.\scripts\check.ps1
```

| 检查 | 实际结果 |
| --- | --- |
| Ruff lint | 通过 |
| Ruff format | 27 个 Python 文件格式检查通过 |
| pytest | **75 passed**；0 失败、0 错误、0 跳过；本次控制台耗时 2.44 秒 |
| pip check | `No broken requirements found.` |
| 真实 CLI / HTTP | 实际 Uvicorn 子进程经 loopback HTTP 验证服务、健康、OpenAPI、Swagger HTML、404 和请求 ID；测试后关闭进程 |

测试还覆盖嵌套凭据、日志格式错误、非法配置、CORS 500 响应，以及 20 个并发请求的上下文隔离。没有调用 DeepSeek 或模拟模型。

本机原始记录：`var/test-results/phase1.xml`；摘要：`var/test-results/phase1-summary.json`。运行产物不提交 Git。上述耗时属于本次基础测试，不是 API 性能或 Agent Evaluation 指标。

实际安装版本包括 FastAPI 0.143.0、Pydantic 2.14.1、pydantic-settings 2.15.0、Starlette 1.7.0、Uvicorn 0.54.0、pytest 9.1.1 和 pytest-asyncio 1.4.0；完整版本与哈希以锁文件为准。

## 环境事项与未验证范围

- 独立命令执行器出现 `helper_unknown_error: setup refresh had errors`，安装与最终验证通过 PyCharm 终端完成。这不表示已修复命令执行器本身。
- 沙箱创建的 `.git` 与 PyCharm 终端用户不同。提交使用 `git -c safe.directory=D:/Python/PycharmProjects/EnterpriseAgent ...` 对当前仓库进行命令级信任；没有修改全局 Git 信任设置。遇到相同所有者提示时可使用同一参数。
- 健康检查只证明应用生命周期就绪，没有连接数据库、Redis、Chroma 或模型。
- Linux、Docker、数据库、RAG、LangGraph、MCP、业务授权、在线模型和 Evaluation 均未执行，属于后续阶段。
- Swagger 验证限于 HTML 和 OpenAPI 的真实 HTTP 响应，未执行浏览器截图或前端交互验证。
- 没有发布到 GitHub、公网或创建付费资源。

## 下一阶段

Phase 2 实现 PostgreSQL/Alembic、业务实体、固定种子数据、认证、订单与退款 Service，使用真实数据库验证归属、金额、事务、并发和幂等。基础设施 Compose 将提前加入；Docker 安装及数据盘优先放 D 盘。
