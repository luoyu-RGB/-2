# 个人理财管理系统 — 数据库课程设计

## 项目信息

- **课程**：数据库系统原理
- **题目**：个人理财管理系统
- **DBMS**：GaussDB（PostgreSQL 兼容）
- **客户端工具**：DBeaver
- **开发语言**：SQL + Python 3.x（演示程序）

## 项目结构

```
数据库课设/
├── README.md                                  # 项目总说明
├── 个人理财管理系统需求文档.md                  # 原始需求文档
│
├── docs/                                      # 设计文档
│   ├── 01_需求分析报告.md                       # 第1阶段：需求分析
│   ├── 02_概念结构设计报告.md                    # 第2阶段：E-R图设计
│   ├── 03_逻辑结构设计报告.md                    # 第3阶段：关系模式设计
│   ├── 04_物理结构设计报告.md                    # 第4阶段：物理设计
│   ├── 05_数据库实施报告.md                      # 第5阶段：实施记录
│   ├── 06_运行与维护报告.md                      # 第6阶段：运维方案
│   └── 附录_数据库设计说明书_完整版.md             # 汇总版（可直接提交）
│
├── sql/                                       # SQL 脚本（按编号顺序执行）
│   ├── 01_drop_tables.sql                      # 删除表（调试用）
│   ├── 02_create_tables.sql                    # CREATE TABLE 建表
│   ├── 03_insert_test_data.sql                 # INSERT 测试数据
│   ├── 04_create_indexes.sql                   # 索引
│   ├── 05_create_views.sql                     # 视图
│   ├── 06_create_triggers.sql                  # 触发器
│   ├── 07_create_functions.sql                 # 存储过程/函数
│   ├── 08_statistical_queries.sql              # 统计查询
│   └── 09_all_in_one.sql                       # 全量汇总（一键执行）
│
├── diagrams/                                   # E-R 图（Mermaid 格式）
│   ├── 01_用户_账户_局部ER图.md
│   ├── 02_交易_类别_局部ER图.md
│   ├── 03_预算_局部ER图.md
│   ├── 04_资产负债_局部ER图.md
│   └── 05_全局集成ER图.md
│
└── demo/                                       # Python 命令行演示
    ├── finance_cli.py
    ├── requirements.txt
    └── 演示说明.md
```

## 快速开始

### 1. 连接数据库

使用 DBeaver 连接 GaussDB：

- 主机：`<your-gaussdb-host>`
- 端口：`5432`（默认）
- 数据库：`finance_db`
- 用户名/密码：`<your-credentials>`

### 2. 执行 SQL 脚本

在 DBeaver 中按顺序执行 `sql/` 目录下的脚本：

| 顺序 | 文件 | 说明 |
|------|------|------|
| 1 | `01_drop_tables.sql` | （可选）清理旧表 |
| 2 | `02_create_tables.sql` | 创建6张数据表 |
| 3 | `03_insert_test_data.sql` | 插入测试数据 |
| 4 | `04_create_indexes.sql` | 创建索引 |
| 5 | `05_create_views.sql` | 创建视图 |
| 6 | `06_create_triggers.sql` | 创建触发器 |
| 7 | `07_create_functions.sql` | 创建存储过程 |
| 8 | `08_statistical_queries.sql` | 执行统计查询 |

> 或直接执行 `09_all_in_one.sql` 一键完成全部操作。

### 3. 运行 Python 演示程序（可选）

```bash
cd demo/
pip install -r requirements.txt
python finance_cli.py
```

## 数据库表概览

| 表名 | 说明 | 主要字段 |
|------|------|----------|
| `user_info` | 用户信息 | user_id, user_name, password_hash |
| `account` | 账户 | account_id, account_name, account_type, balance |
| `category` | 收支类别 | category_id, category_name, category_type, parent_id |
| `transaction_record` | 交易记录 | transaction_id, amount, trans_type, trans_date |
| `budget` | 预算 | budget_id, category_id, year_month, budget_amount |
| `asset_liability` | 资产负债 | item_id, item_name, item_type, amount |

## 文档阅读顺序

1. [需求分析报告](docs/01_需求分析报告.md) — 了解系统要做什么
2. [概念结构设计报告](docs/02_概念结构设计报告.md) — 理解实体和关系
3. [逻辑结构设计报告](docs/03_逻辑结构设计报告.md) — 关系模式和范式
4. [物理结构设计报告](docs/04_物理结构设计报告.md) — 索引和存储
5. [数据库实施报告](docs/05_数据库实施报告.md) — 建表和数据
6. [运行与维护报告](docs/06_运行与维护报告.md) — 备份和优化
7. [完整设计说明书](docs/附录_数据库设计说明书_完整版.md) — 汇总版
