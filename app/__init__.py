"""个人理财助手 — 统一后端包。

分层约定（详见 docs/AI增强改造方案.md）：

    routers  →  services  →  db（适配层 / 仓储）  →  数据库
                  ↑                                 ↑
              ai/tools                        触发器 / 存储过程（余额唯一维护者）

约束：应用层任何位置都不允许出现 ``UPDATE account SET balance``，
账户余额只能由数据库触发器（PostgreSQL ``trg_transaction_balance`` /
SQLite 同名触发器）维护。
"""

__all__ = ["__version__"]

__version__ = "2.0.0"
