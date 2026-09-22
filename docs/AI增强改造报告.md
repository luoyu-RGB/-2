# AI 增强改造报告

> 执行时间：本轮改造会话 · 目标目录：`C:\Users\dwt\Desktop\数据库课设`
> 改造方案见 `docs/AI增强改造方案.md`；SQL 增量升级见 `docs/sql/10_upgrade_ai.sql`。

---

## 一、改造前的三个关键问题（已实测确认）

| # | 问题 | 证据 | 影响 |
| --- | --- | --- | --- |
| 1 | **数据库凭据泄露** | `web/app.py` 原第 24–31 行硬编码 `222.27.161.245:15432 / llhdb / s1362 / 明文密码`，且该文件已推送到公开仓库 `github.com/luoyu-RGB/-2` | 任何人可直连你的课程数据库。**必须轮换密码** |
| 2 | **种子脚本跑不到底** | `docs/sql/03_insert_test_data.sql` 第 229–239 行的余额重算会让"现金""微信钱包"这类只有支出的账户余额变成负数，撞上 `account.balance >= 0` 的 CHECK 约束而整条语句回滚 | 一台干净库上无法完成初始化；已用测试 `test_original_seed_recompute_would_violate_check_constraint` 复现并锁死 |
| 3 | **课设卖点在演示里被绕过** | 最新的 `backend/app.py`（69 行 SQLite）没有外键、没有触发器，余额改由 Python 手算；表结构也与正式 schema 漂移 | 答辩演示与"我写了触发器/存储过程"自相矛盾 |

另有 7 处设计与实现问题（视图无 user_id、`TO_CHAR` 导致索引失效、转账靠时间差配对、存储过程不校验账户归属、触发器 UPDATE 分支不预检、`year_month` 无格式约束、无审计能力），全部在 `10_upgrade_ai.sql` 中修复。

## 二、新增/修改了什么

### 新增：统一后端 `app/`（FastAPI，替代 Flask + SQLite 两套并行）

```
app/
├── config.py            pydantic-settings，凭据与开关全部外置到 .env
├── deps.py              单例服务 + 当前用户（X-User-Id 请求头）
├── models.py            请求/响应模型
├── db/
│   ├── base.py          适配器抽象、领域异常、金额 Decimal 量化
│   ├── sqlite.py        SQLite 适配器（离线演示路径）
│   ├── postgres.py      GaussDB/PostgreSQL 适配器（正式路径）
│   ├── repository.py    全部领域 SQL：报表优先读视图
│   ├── sqlite_schema.sql 与 PG 逐列对齐的表/触发器/视图
│   └── seed.py          演示数据（相对当月生成、触发器维护余额）
├── services/            安全计算器 + 可负担性引擎
├── ai/                  大模型工具调用 + 知识库 + 规则降级
└── routers/             api.py（账本/报表）+ ai.py（对话/知识库）
```

### 新增/修改的其它文件

| 文件 | 说明 |
| --- | --- |
| `docs/sql/10_upgrade_ai.sql` | GaussDB 增量升级脚本（可重复执行），修复上述 7 项并新增审计表、知识库表 |
| `scripts/init_db.py` | 一键初始化：建结构 → 播种 → 打印由触发器维护的余额 |
| `tests/`（4 个文件） | pytest：数据层不变量、24 个接口、规则模式 AI、SQL 语法静态校验 |
| `requirements*.txt` | 拆成 核心 / AI / RAG / 开发 四档，无 AI 依赖也能跑 |
| `.env.example` | 环境变量模板（`.env` 已被 `.gitignore` 忽略） |
| `web/app.py` | 仅改 DB_CONFIG：硬编码明文凭据 → `os.getenv`；其余保持原样作为 Flask 参考实现 |
| `frontend/`（3 个文件） | 重写为 5 视图应用：概览 / 收支明细 / 预算规划 / 资产负债 / AI 顾问；无外网依赖、无构建步骤 |
| `README.md` | 整体重写：原文件描述的 `docs/01..06_*.md`、`diagrams/`、根 `sql/` 目录都不存在 |
| `run_backend.ps1` | 改为「初始化数据库 → 启动 `app.main:app`」 |
| `backend/app.py` | 退役为 3 行转发入口（原 69 行 SQLite 演示与正式 schema 漂移） |
| `tests/test_frontend.py` | 前端契约测试：导航与视图对应、接口路径真实存在、无外网资源、元素 id 交叉检查 |

## 三、验证结果

```
pytest：   63 passed × 2（同一套用例分别跑在 SQLite 与真实 PostgreSQL 18.6 上）
真实服务端到端冒烟（scripts/smoke_api.py）：37 项全部通过 × 2（SQLite 后端 / PostgreSQL 后端）
PostgreSQL 真机验收（scripts/verify_postgres.py --reset）：21 项全部通过
```

端到端冒烟是在**真实启动的 uvicorn 服务**上发 HTTP 请求（不是进程内测试客户端），
覆盖：健康检查 → 账本报表 → 安全计算 → 可负担性 → 记账确认 → 知识库检索。

```
1) 健康检查与结构自检   服务存活 / 结构自检通过 — 9 表 / 7 视图 / 3 触发器 / 55 笔交易
2) 账本与报表          账户、总览、净资产、预算执行、类别占比、月度趋势 全部可用
3) 计算与可负担性      计算器 2050.00 精确；拒绝代码执行；月必要支出 4126.33 元/月
4) 对话式记账          识别意图 → 生成预览 → 确认落库（55→56 笔）→ 审计状态 applied
5) 自然语言购买咨询    走系统计算而非泛泛而谈，结论「可以购买」
6) 知识库              入库 1 个片段，检索命中 mode=bm25
7) 取消待确认操作      取消后「确认」不再落库（56→56），审计状态 rejected
8) 前端资源            首页 HTML + 5 个视图 + /app.js + /style.css 全部可访问
```

### PostgreSQL 18.6 真机验收（本机实例，非模拟）

```
1) 独立应用角色        finance_app + finance_db（业务不使用超级用户）
2) docs/sql 脚本       02 / 04 / 05 / 06 / 07 / 10 全部在真实 PostgreSQL 上执行成功
3) 适配层播种          9 表 / 7 视图 / 触发器 trg_transaction_balance / 55 笔交易
                      余额由触发器算出：现金=1616.00、储蓄卡=86101.00、支付宝=11598.00
                      —— 与 SQLite 路径完全一致（跨后端一致性）
4) 专属对象            fn_monthly_report ✓  fn_category_spending ✓
                      sp_add_transfer ✓（余额精确变动 123.45）sp_copy_budget ✓
                      transfer_group_id 已写入 ✓  视图按 user_id 隔离 ✓
                      透支被触发器拦下并返回中文提示 ✓
5) EXPLAIN（20000 行） 范围谓词 → Bitmap Heap Scan（走索引）cost 185.41
                      TO_CHAR 包裹列 → Seq Scan（不走索引）cost 528.25
```

同时用真机暴露并修掉了三个只有 PostgreSQL 才会出现的问题：

1. **`DROP/CREATE SCHEMA` 不能写限定名**，`public.verify_scratch` 会被当成跨库引用；
2. **SERIAL 序列不会被显式 id 插入推进** —— 种子里固定插入 `user_id = 1` 后，
   下一次自动插入仍会拿到 1 而主键冲突（SQLite 的 AUTOINCREMENT 会自动记住最大值），
   已在 `seed.py` 里补 `setval(pg_get_serial_sequence(...))`；
3. **`information_schema.tables` 把视图也算作表**，结构自检需要加 `table_type = 'BASE TABLE'`。

| 验证项 | 结果 |
| --- | --- |
| SQLite 全链路（建表→触发器→视图→播种→查询） | 通过：9 表 / 7 视图 / 3 触发器，余额全部由触发器算出且非负 |
| 触发器不变量（插入/删除/改账户/改金额/改方向/透支拦截） | 通过，透支返回中文业务错误而非约束报错 |
| 余额不可被应用层写入 | 通过：`AccountUpdate` 结构上就没有 `balance` 字段 |
| 24 个原 Flask 接口 | 路径完全兼容，冒烟全部通过 |
| 多用户隔离 | 通过：视图补了 `user_id`，另一用户查不到任何数据 |
| 可负担性分析（无 API Key） | 通过：到手总成本、应急金月数、需攒钱月数均可复算 |
| 规则记账 → 预览 → 确认 → 审计 | 通过：预览阶段不动账本，确认后落库且触发器更新余额 |
| 知识库（BM25 兜底） | 通过：中文二元切分 + BM25，检索"提前还款违约金"命中条款原文 |
| `docs/sql/*.sql` 全部 10 个脚本 | 通过 pglast（PostgreSQL 官方解析器）语法校验 |

### 实测中发现并修复的两个问题

1. **月必要支出算成 59.75 元**（应为数千元）：交易记在子类别上（房租/早餐/地铁），
   而"必要类目"匹配的是父类别（居住/餐饮/交通），两者对不上。
   已改为按父类别归并（`COALESCE(parent.category_name, child.category_name)`），修复后为 4126.33 元/月。
2. **口径混入未过完的当月**：月均值改为只统计"已完整结束的月份"，避免半个月数据拉低基准。


## 四、如何运行

```powershell
# 1) 依赖（核心，不需要任何 AI Key）
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt   # 跑测试用

# 2) 初始化离线演示库（SQLite）
.venv\Scripts\python.exe scripts\init_db.py --force

# 3) 启动服务（前端页面与 /docs 都在这个端口）
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000

# 4) 跑测试
.venv\Scripts\python.exe -m pytest -q
```

切换到正式路径（GaussDB）：

```ini
# .env
DB_MODE=postgres
PG_HOST=<你的主机>
PG_PORT=15432
PG_DATABASE=llhdb
PG_USER=<你的用户名>
PG_PASSWORD=<轮换后的新密码>
PG_SCHEMA=<你的 schema>
```

然后在 DBeaver 里按 `01 → 02 → 04 → 05 → 06 → 07 → 10 → 03` 的顺序执行脚本
（03 放在触发器之后，余额才会被自动维护；`03` 结尾那段手工重算应删除）。

开启 AI 对话：在 `.env` 填入 `DEEPSEEK_API_KEY`，并安装 `requirements-ai.txt`。

## 五、行为差异（迁移时需要注意）

1. `PUT /api/accounts/{id}` **不再接受修改余额**（原来可以，等于绕开触发器）。前端若传了 `balance` 会被忽略。
2. `DELETE /api/accounts/{id}` 会返回 `orphan_transfer_groups`，提示哪些转账只剩单边。
3. "转账"不再靠备注里的"转出/转入"识别，而是用 `transfer_group_id`；`trans_type=转账` 的过滤仍兼容。
4. 月份过滤一律用 `trans_date >= ? AND trans_date <= ?`，请不要再拼 `TO_CHAR(trans_date,'YYYY-MM') = ?`。
5. 原 `backend/app.py`（69 行 SQLite 演示）已被 `app/` 取代；建议删除或改为 3 行转发，避免两套数据模型继续漂移。

## 六、遗留项

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 前端补全 | ✅ 已完成 | `frontend/` 五个视图全部可用（原先四个导航是死链），并新增 AI 顾问面板与审计列表 |
| README 重写 | ✅ 已完成 | 已改为真实目录结构 + 架构约定 + 双适配层差异表 + 常见问题 |
| 启动脚本 | ✅ 已完成 | `run_backend.ps1` 自动初始化并启动统一后端 |
| **密码轮换** | ⚠️ 待你处理 | 公开仓库里的旧数据库密码必须改；改完填进本地 `.env`（已被 gitignore） |
| GaussDB 真机验收 | ✅ 已完成 | 本机 PostgreSQL 18.6 上跑通：6 个脚本执行成功、播种子、函数/存储过程/视图/触发器全部验证、EXPLAIN 证明索引改造有效；`.env` 已切到 `DB_MODE=postgres`（改回 `sqlite` 即可恢复离线演示） |
| 课设文档同步 | ⏳ 可选 | `数据库设计说明书.docx` 可追加"AI 增强与结构升级"一节（表 6→9 张、视图补 user_id、新增审计与知识库表） |

## 七、演示时的建议话术

1. **先讲约束**：账户余额只由触发器维护，应用层连 `UPDATE account SET balance` 都不允许——
   测试里有一条断言专门盯这件事（`test_balance_column_cannot_be_written_by_application`）。
2. **再讲复用**：5 个视图与 3 个存储过程/函数都被真实调用，PostgreSQL 路径下月度报表走
   `fn_monthly_report`、类别占比走 `fn_category_spending`、转账走 `sp_add_transfer`。
3. **然后演示 AI 的三段式**：自然语言 → 预览 → 确认 → 审计留痕；
   并强调"没有 API Key 时规则模式照样能记账和做可负担性分析"。
4. **最后讲可解释性**：可负担性的每一步（到手总成本、月必要支出、应急金目标、需攒钱月数）
   都能在接口返回的 `calculation_steps` 里逐条复算，数字不来自模型。
