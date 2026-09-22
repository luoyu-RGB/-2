# 个人理财助手 · AI 增强版

> 由「数据库课程设计」升级而来的 **AI 个人理财助手**：双数据库适配（GaussDB/PostgreSQL + SQLite）、
> 对话式记账、消费可负担性分析、理财资料问答。所有金额与结论都来自数据库查询与确定性计算，
> 大模型只负责理解意图与组织语言。

[![tests](https://img.shields.io/badge/tests-63%20passed-brightgreen)](#测试与验收)

---

## 能力一览

| 能力 | 说明 | 需要 API Key |
| --- | --- | --- |
| 账本管理 | 账户、收支类别（两级）、交易增删改查、账户间转账 | 不需要 |
| 预算管理 | 月度分类预算、执行率与超支状态、复制上月预算 | 不需要 |
| 资产负债 | 资产/负债明细、净资产视图 | 不需要 |
| **消费可负担性分析** | 结合真实账本算到手总成本、应急金月数、需攒钱月数并给结论 | 不需要 |
| **安全计算器** | `ast` 白名单 + `Decimal` 四则运算，拒绝任意代码执行 | 不需要 |
| **对话式记账** | 「今天午餐花了 32 元」→ 预览 → 确认 → 落库（走触发器） | 可选 |
| **理财资料问答** | 上传条款/笔记 → 切分入库 → 检索问答（BM25 或向量） | 可选 |
| 情绪无关的稳健性 | 没有 Key 时自动降级为规则模式，全部接口照常可用 | — |

## 技术栈

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 后端 | FastAPI + Pydantic v2 | 单个 `app/` 包，24+ REST 接口，路径与原 Flask 版兼容 |
| 数据层 | psycopg2 / sqlite3 **双适配器** | 仓储层共用一段 SQL，仅方言原语分叉 |
| 数据库 | GaussDB（PostgreSQL 兼容）/ SQLite | 正式路径用视图、触发器、存储过程；SQLite 为离线演示 |
| 前端 | 原生 HTML/CSS/JS | 无构建步骤、无外网依赖，由 FastAPI 同源托管 |
| AI | OpenAI 兼容协议（可直连 DeepSeek） | 手写工具调用循环，工具全部走仓储层 |
| 检索 | 自研 BM25（中文二元切分）；可选 fastembed 向量 | 不装重依赖也能问答 |
| 测试 | pytest + pglast | 56 个用例；SQL 脚本用 PostgreSQL 官方解析器静态校验 |

## 目录结构

```
数据库课设/
├── app/                        # 统一后端（FastAPI）
│   ├── main.py                 # 应用工厂、异常映射、静态托管
│   ├── config.py deps.py models.py
│   ├── db/                     # 适配器 / 仓储 / SQLite 结构 / 演示数据
│   ├── services/               # 安全计算器、可负担性引擎
│   ├── ai/                     # 工具集、对话编排、知识库、提示词
│   └── routers/                # api.py（账本报表） ai.py（对话知识库）
├── frontend/                   # 前端：5 个视图（概览/明细/预算/资产负债/AI 顾问）
├── scripts/
│   ├── init_db.py              # 一键初始化（建结构 + 播种 + 打印余额）
│   └── smoke_api.py            # 对真实运行中的服务做端到端冒烟
├── tests/                      # pytest：数据层 / 接口 / 服务 / SQL 校验 / 前端契约
├── docs/
│   ├── sql/                    # 01–09 课程设计原始脚本，10 为增量升级脚本
│   ├── AI增强改造方案.md        # 动手前的设计与决策
│   └── AI增强改造报告.md        # 改造结果、验证证据与遗留项
├── web/                        # 原 Flask 实现（保留作参考，凭据已外置）
├── demo/                       # 原命令行演示程序
├── requirements*.txt           # 核心 / AI / RAG / 开发 四档依赖
└── .env.example                # 环境变量模板（.env 已被 gitignore）
```

## 快速开始

### 1. 离线演示（SQLite，零外部依赖）

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python.exe scripts\init_db.py --force     # 建库 + 写入演示数据
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

打开 <http://127.0.0.1:8000/> 使用前端页面，<http://127.0.0.1:8000/docs> 查看接口文档。
也可以直接运行 `.\run_backend.ps1`（自动初始化并启动）。

### 2. 正式路径（GaussDB / PostgreSQL）

```ini
# .env
DB_MODE=postgres
PG_HOST=<主机>          PG_PORT=15432
PG_DATABASE=llhdb       PG_USER=<用户名>
PG_PASSWORD=<密码>      PG_SCHEMA=<schema>
```

在 DBeaver 中按 `01 → 02 → 04 → 05 → 06 → 07 → 10 → 03` 顺序执行 `docs/sql` 下的脚本：

> **03 必须放在 06 之后**：账户余额由触发器维护，先建触发器再插数据，
> 否则需要手工重算余额（原脚本结尾那段重算会让只有支出的账户变成负数而违反约束，
> 详见 `tests/test_database.py::test_original_seed_recompute_would_violate_check_constraint`）。

本机已在 **PostgreSQL 18.6** 上完成真机验收。也可以让脚本一次性做完建角色、建库、执行脚本、播种与校验：

```powershell
.venv\Scripts\python.exe scripts\verify_postgres.py --reset
```

它会创建独立的 `finance_app` 角色与 `finance_db`（业务不使用超级用户），把应用凭据写回 `.env`；
`--reset` 先清空 public schema，因此可重复运行。首次运行需要 `.env` 里的 `PG_USER/PG_PASSWORD` 是超级用户。

### 3. 打开 AI 对话（可选）

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-ai.txt
```
在 `.env` 填入 `DEEPSEEK_API_KEY`（任何 OpenAI 兼容服务都可以，改 `CHAT_API_BASE` 与 `CHAT_MODEL`）。
想要向量检索再装 `requirements-rag.txt` 并把 `EMBEDDING_PROVIDER` 改成 `local`。

## 架构约定

```
routers  →  services  →  db（仓储 / 适配器）  →  数据库
              ↑                                  ↑
          ai/tools                    触发器 / 存储过程（余额唯一维护者）
```

三条硬约束（测试里有对应断言）：

1. **账户余额只能由数据库维护**。应用层不出现任何 `UPDATE account SET balance`，
   PostgreSQL 用触发器 `trg_transaction_balance`，SQLite 用同名等价触发器；
   `AccountUpdate` 模型结构上就没有 `balance` 字段。
2. **报表只读视图/函数**。`v_budget_status`、`v_category_expense_ratio`、`v_cash_flow_trend`、
   `v_net_worth`、`v_monthly_income_expense` 都被真实调用；PostgreSQL 下月度与类别报表
   直接调用 `fn_monthly_report` / `fn_category_spending`，转账调用 `sp_add_transfer`。
3. **月份过滤用可索引范围谓词**：`trans_date >= ? AND trans_date <= ?`，
   不再用 `TO_CHAR(trans_date,'YYYY-MM') = ?`（后者会让日期索引失效）。

### 双适配层差异

| 能力 | PostgreSQL / GaussDB | SQLite |
| --- | --- | --- |
| 余额维护 | `plpgsql` 触发器 + 余额预检 | 三个等价触发器（INSERT/UPDATE/DELETE） |
| 转账原子性 | `sp_add_transfer` 存储过程 | 同事务写两条记录（唯一在应用层实现的差异） |
| 月度/类别报表 | `fn_monthly_report` / `fn_category_spending` | 同结构视图 |
| 预算 upsert | `ON CONFLICT` | `ON CONFLICT` |
| 部分索引 | 支持 | 不支持（已省略） |
| 金额精度 | 精确 `NUMERIC` | NUMERIC 亲和性（浮点近似），读出一律量化到 2 位 |

## 数据库设计

9 张表 / 7 个视图 / 3 个触发器：

| 对象 | 说明 |
| --- | --- |
| `user_info` `account` `category` `transaction_record` `budget` `asset_liability` | 课程设计原有 6 张表 |
| `transaction_record.transfer_group_id` | 新增：同一笔转账的两条记录共享，替代"时间差 + 备注 LIKE"配对 |
| `ai_action_log` | 新增：AI 写操作审计（preview / applied / rejected / failed） |
| `knowledge_document` `knowledge_chunk` | 新增：知识库文档与切片（`embedding` 可空） |
| `05_create_views.sql` 的 7 个视图 | 全部补 `user_id` 维度（原版为全局聚合，多用户会串数据） |
| `06_create_triggers.sql` | 余额维护 + 余额预检 + 禁止改动交易归属用户 |

升级脚本 `docs/sql/10_upgrade_ai.sql` 可重复执行，不需要改动 01–09 原始脚本，
因此课程设计说明书里的原始表结构描述依然成立。

## 主要接口

| 分组 | 接口 |
| --- | --- |
| 账户 | `GET/POST /api/accounts`、`PUT/DELETE /api/accounts/{id}` |
| 类别 | `GET /api/categories?type=` |
| 交易 | `GET/POST /api/transactions`、`PUT/DELETE /api/transactions/{id}` |
| 转账 | `POST /api/transfers` |
| 报表 | `/api/overview`、`/api/net-worth`、`/api/monthly-trend`、`/api/daily-cashflow`、`/api/category-ratio`、`/api/monthly-report` |
| 预算 | `GET/POST /api/budgets`、`/api/budget-status`、`POST /api/budgets/copy` |
| 资产负债 | `GET/POST /api/asset-liability`、`PUT/DELETE /api/asset-liability/{id}` |
| AI | `POST /api/chat`、`POST /api/calculator`、`POST /api/affordability`、`GET /api/ai/actions`、`POST /api/ai/actions/{id}/reject` |
| 知识库 | `GET/POST /api/knowledge`、`DELETE /api/knowledge/{id}`、`POST /api/knowledge/search` |
| 运维 | `GET /api/health`、`GET /api/schema`、`GET /api/stats`、`DELETE /api/clear-data` |

## 环境变量

见 `.env.example`。常用项：`DB_MODE`、`SQLITE_PATH`、`PG_*`、`DEEPSEEK_API_KEY`、
`CHAT_API_BASE`、`CHAT_MODEL`、`EMBEDDING_PROVIDER`（`bm25` / `local`）、`DEFAULT_USER_ID`。

## 测试与验收

```powershell
# 离线路径（SQLite）
.venv\Scripts\python.exe -m pytest -q                                       # 63 个用例
.venv\Scripts\python.exe scripts\smoke_api.py --base http://127.0.0.1:8000  # 真实服务端到端冒烟（37 项）

# 正式路径（PostgreSQL / GaussDB）—— 同一套用例，不用改一行代码
$env:TEST_DB_MODE="postgres"; .venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts\verify_postgres.py --reset                 # 建库+执行脚本+真机验收（21 项）
```

覆盖：触发器不变量（插入/删除/改账户/改金额/改方向/透支拦截）、余额不可被应用层写入、
多用户隔离、24 个接口、规则模式 AI（预览→确认→审计）、BM25 检索、可负担性复算、
`docs/sql` 全部脚本的 PostgreSQL 语法校验、前端契约（导航与离线可用性）。

## 常见问题

**没有 GaussDB 实例能演示吗？** 能。把 `.env` 里的 `DB_MODE` 改成 `sqlite`（离线演示路径），
`scripts/init_db.py` 会自动建结构并播种；演示数据相对当前月份生成，因此「本月概览 / 预算执行」任何时候打开都有内容。

> 本机完成 PostgreSQL 验收后，`.env` 已切换为 `DB_MODE=postgres`（应用角色 `finance_app`，密码也在 `.env` 里）。
> 想回到零依赖演示，把 `DB_MODE` 改回 `sqlite` 即可——两条路径的数据互不影响，切换不需要改任何代码。

**没有 API Key 会报错吗？** 不会。记账、可负担性分析、计算器、知识库检索全部可用；
对话走规则模式并在回复里说明「未配置大模型」。接口 `/api/health` 的 `config.chat_ready` 会如实反映状态。

**端口 8000 被占用？** `uvicorn app.main:app --port 8010`，前端由同一服务托管，无需改前端地址。

**数据在哪？** SQLite 在 `data/finance.db`（已被 gitignore）；GaussDB 在你自己 schema 下。
`DELETE /api/clear-data` 只清当前用户的业务数据。

## 与课程设计原版的关系

| 原始交付物 | 处理方式 |
| --- | --- |
| `docs/sql/01–09` | 保留不动，新增 `10_upgrade_ai.sql` 作为增量升级 |
| `demo/finance_cli.py` | 保留（命令行演示） |
| `web/`（Flask，783 行） | 保留作参考实现；硬编码数据库密码已改为读环境变量 |
| `backend/app.py`（69 行 SQLite 演示） | 已退役，改为 3 行转发到 `app.main:app`，避免两套数据模型漂移 |
| `frontend/` | 重写为 5 视图应用（含 AI 顾问面板） |

> ⚠️ **安全提醒**：原 `web/app.py` 曾把数据库账号密码硬编码并推送到公开仓库，
> 该密码应视为已泄露，请轮换后写入本地 `.env`（`.env` 已被 gitignore）。
