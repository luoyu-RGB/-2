# 个人理财助手 — AI 增强改造方案

> 本文件是改造前的设计约定。**代码尚未改动**，你可以先审阅本文，逐条否决或调整。
> 定位：把原「数据库课程设计」升级为可用于求职简历的 **AI 个人理财助手**，保留全部原始课设交付物。

---

## 一、目标与范围（已与你确认）

| 决策项 | 结论 |
| --- | --- |
| 定位 | 求职简历项目（工程化 + 架构 + AI 落地能力都要能讲） |
| AI 能力 | ①对话式记账（写入走存储过程/触发器）②财务知识 RAG 问答 ③消费可负担性分析 + 安全 Decimal 计算 |
| 数据层 | 双适配层：**GaussDB/PostgreSQL 为正式路径**，**SQLite 为离线演示路径**，两者 schema/触发器/视图对齐 |
| 后端 | 统一到 **FastAPI**，迁移 Flask `web/` 的全部功能；`web/` 保留为参考，不删除 |
| 落地方式 | 直接改 `C:\Users\dwt\Desktop\数据库课设`（写入需你逐次授权） |
| 不做 | 自然语言查账（本次未选，留作后续扩展点） |

## 二、目标架构

```
数据库课设/
├── app/                          # 新的统一后端（FastAPI）
│   ├── main.py                   # 应用工厂 + 路由注册 + 托管 frontend 静态页
│   ├── config.py                 # pydantic-settings，读 .env（DB_MODE / API Key / 模型）
│   ├── db/
│   │   ├── base.py               # DatabaseAdapter 抽象接口（唯一数据入口）
│   │   ├── postgres.py           # psycopg2：走视图 v_* / 函数 fn_* / 存储过程 sp_*
│   │   ├── sqlite.py             # sqlite3：同接口，触发器/视图等价移植
│   │   ├── sqlite_schema.sql     # SQLite 版建表+触发器+视图（与 PG 对齐）
│   │   └── seed.py               # 演示数据（可重复执行、幂等）
│   ├── models.py                 # Pydantic 请求/响应模型
│   ├── services/
│   │   ├── ledger.py             # 账户/类别/交易/转账/预算/资产负债 业务
│   │   ├── report.py             # 报表：只读视图与函数，不重复写聚合 SQL
│   │   ├── calculator.py         # AST 白名单 + Decimal 安全计算（禁止 eval）
│   │   └── affordability.py      # 可负担性引擎（应急金月数/到手成本/分期）
│   ├── ai/
│   │   ├── agent.py              # create_agent 编排 + 工具注册 + 无 Key 降级
│   │   ├── tools.py              # 对 LLM 暴露的工具（只调 service，不拼 SQL）
│   │   ├── rag.py                # Chroma + 本地 BGE；不可用时降级 BM25
│   │   └── prompts/              # 系统提示词 / 记账抽取 / 记忆抽取
│   └── routers/                  # accounts, transactions, transfers, budgets,
│                                 # assets, reports, chat, knowledge
├── frontend/                     # 统一前端（补齐导航与视图，新增对话面板）
├── tests/                        # pytest：适配层一致性 / API / 计算器 / AI 工具
├── docs/
│   ├── sql/                      # 原始交付物（见第五节：修复清单）
│   └── AI增强改造方案.md          # 本文件
├── web/                          # 原 Flask 版，保留为参考实现
├── demo/                         # 原命令行演示，保留
├── requirements.txt              # 核心依赖（无 AI 也能跑）
├── requirements-ai.txt           # AI 依赖（langchain/chromadb/fastembed）
└── .env.example
```

分层约束（可写进简历/答辩）：

```
routers → services → db adapter → 数据库
   ↑                    ↑
  models           触发器/存储过程（余额唯一维护者）
   └── ai/tools ──────┘   AI 只能调用 service，不允许直接拼 SQL
```

## 三、关键设计决策

1. **余额只由数据库维护**：应用层不出现任何 `UPDATE account SET balance ...`。PostgreSQL 走既有触发器 `trg_transaction_balance`；SQLite 用等价触发器移植。测试中加断言 + 代码扫描校验这条约束。
2. **报表只读视图/函数**：新增报表一律查 `v_*` 视图或 `fn_*` 函数，杜绝"视图建了不用、应用层再写一遍聚合 SQL"（这是原项目最大的浪费点）。
3. **双适配层能力矩阵**（明确差异，不假装等价）：

   | 能力 | PostgreSQL | SQLite |
   | --- | --- | --- |
   | 触发器维护余额 | 原生 `plpgsql` | 移植为 SQLite 触发器 |
   | 转账原子性 | `sp_add_transfer` 存储过程 | 适配层单事务插入两条记录（唯一在应用层实现的差异，已在代码注释说明） |
   | 月度/类别报表 | `v_*` / `fn_monthly_report` | 同结构移植视图（`strftime` 替代 `TO_CHAR`） |
   | 部分索引 | 原生 | 普通索引（SQLite 无部分索引） |
   | 预算 upsert | `ON CONFLICT` | `ON CONFLICT`（SQLite 3.24+ 支持） |

4. **AI 工具边界**：`add_transaction` / `transfer` / `set_budget` / `query_report` / `calc` / `analyze_affordability` / `search_knowledge`。写类工具先返回 **dry-run 预览**，用户确认后才落库，并写入审计表。
5. **无 Key 也能演示**：`DEEPSEEK_API_KEY` 缺失时，计算器与可负担性分析按规则直接可用，RAG 返回明确提示而非报错；AI 依赖未安装时同样降级。答辩现场不怕断网。
6. **前端由 FastAPI 托管**：去掉 `allow_origins=['*']` 与硬编码 `127.0.0.1:8000`，同源访问。
7. **可回滚**：git 工作区当前干净（`093d3c4`），改造按阶段推进，每阶段一个可验证状态。

## 四、AI 能力实现要点

| 能力 | 实现 | 无 Key 降级 |
| --- | --- | --- |
| 对话式记账 | LLM 工具调用 → `ledger` → 存储过程/触发器；含确认流 | 规则解析"今天午餐 32"式简单句 |
| 财务知识 RAG | Chroma 持久化 + `user_id` 元数据隔离；本地 BGE 优先，DashScope 可选 | 关键词/BM25 检索 |
| 可负担性分析 | 读取真实账本 → 应急金月数、到手总成本、分期总成本 | **完整可用**（纯计算，不需要模型） |
| 安全计算 | `ast` 白名单四则运算 + `Decimal`，长度与除零保护 | 完整可用 |

## 五、SQL 层修复清单（原项目实测问题）

| # | 问题 | 位置 | 修复 |
| --- | --- | --- | --- |
| 1 | 视图无 `user_id` 维度，多用户串数据 | 05 全部视图 | 视图输出 `user_id` 列，查询侧强制过滤 |
| 2 | 函数包列导致索引失效：`TO_CHAR(trans_date,'YYYY-MM') = ?` | 05 / 07 / 08 | 改可索引范围谓词 `trans_date >= ? AND trans_date < ?`；文档保留表达式索引作为对照 |
| 3 | 转账靠"时间差 <10 秒 + 备注 LIKE"自连接配对，脆弱 | 08 Q10、07 `sp_add_transfer` | `transaction_record` 增 `transfer_group_id`，存储过程写入，Q10 直接等值连接 |
| 4 | `sp_add_transfer` 未校验账户归属与同账户转账 | 07 | 增加 `p_from_account <> p_to_account` 与 `user_id` 归属校验 |
| 5 | 触发器 UPDATE 分支未校验新值余额 | 06 | UPDATE 前对目标账户做余额预检，并禁止改 `user_id` |
| 6 | `budget.year_month` 无格式约束、无法范围查询 | 02 | 加 `CHECK (year_month ~ '^\d{4}-(0[1-9]\|1[0-2])$')` |
| 7 | 无审计能力（触发器里 `system_log` 被注释掉） | 06 | 新增 `ai_action_log` 审计表，记录 AI 写操作 |

> 处理方式：**直接修改 `docs/sql/` 原始脚本并同步 `09_all_in_one.sql`**，git 历史即旧版快照。若你希望课设原稿一字不动，改为新增 `10_upgrade.sql` 迁移脚本——请在审阅时告诉我。

## 六、分阶段计划与验收标准

| 阶段 | 内容 | 验收标准 |
| --- | --- | --- |
| P0 | 方案文档、venv 与依赖、git 基线 | 依赖装好；`pytest` 可运行 |
| P1 | 双适配层 + SQLite 全量 DDL/触发器/视图 + PG SQL 修复 | SQLite 端到端跑通；`docs/sql/*.sql` 全部通过 PostgreSQL 语法解析；适配层一致性测试通过 |
| P2 | FastAPI 统一后端，迁移 Flask 全部 20+ 接口 | 接口清单与 Flask 版逐条对齐；报表走视图/函数；前端同源可访问 |
| P3 | 三项 AI 能力 + 审计 + 降级 | 无 Key 时功能不报错；有 Key 时对话式记账落库正确且余额由触发器变更 |
| P4 | 前端补齐（导航路由、交易/预算/资产负债/报表、对话面板） | 四个导航可用；记账、转账、预算、报表页面可操作 |
| P5 | 工程化与交付 | 根 `requirements.txt` + `.env.example` + README 重写 + 改造报告 + 简历要点 |

## 七、需要你决定或提供的事项

1. **PostgreSQL 实例**：本机 5432 未监听、无 `psql`。可选：(a) 我安装本地 PostgreSQL 做真机验证；(b) 你提供远程 GaussDB 连接信息；(c) 接受"PG 路径只做语法静态校验 + SQLite 做行为验证"。默认走 (c)。
2. **API Key**：DeepSeek 官方还是校内网关？我会先写成 `.env` 配置 + 无 Key 降级，Key 你随时填。
3. **git 提交**：允许我新建分支 `feat/ai-assistant` 并按阶段提交吗？还是只留工作区改动由你自己提交。
4. **课设文档同步**：`数据库设计说明书.docx` / `答辩PPT.pptx` 是否需要我生成一份"变更说明"供你贴入？

## 八、风险与对策

| 风险 | 对策 |
| --- | --- |
| Python 3.14 无部分 AI 依赖轮子 | 统一用项目内 3.12 venv；AI 依赖全部惰性导入 |
| chromadb/fastembed 体积大、首次下载模型 | 独立 `requirements-ai.txt`；RAG 可降级 BM25，不影响主流程 |
| PG 无法本地实测 | SQL 语法静态校验 + SQLite 行为验证双重保障；等你提供实例后补一次真机验收 |
| 改动破坏课设交付物 | 原始 docx/pptx/demo/web 一律不删；SQL 脚本改动列入本文第五节，可逐条回退 |
