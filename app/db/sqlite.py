"""SQLite 适配器 —— 离线演示路径。

设计取舍：
* 触发器、视图与 PostgreSQL 版同名同结构（见 ``sqlite_schema.sql``），
  因此仓储层的 SQL 在两个后端上几乎完全一致；
* SQLite 没有存储过程，转账是唯一在应用层实现的差异（同一事务写两条记录），
  余额仍然只由触发器变更；
* 金额在 SQLite 里最终以 NUMERIC 亲和性存储（可能是浮点近似），
  因此读出来统一量化到 2 位小数；精确账务请使用 PostgreSQL 路径。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from .base import (
    ConflictError,
    DatabaseAdapter,
    DatabaseError,
    InsufficientBalanceError,
    NotFoundError,
    PRIMARY_KEYS,
    Tx,
    normalize_rows,
)

SCHEMA_FILE = Path(__file__).with_name("sqlite_schema.sql")


def translate_error(exc: Exception) -> DatabaseError:
    """把 sqlite3 异常翻译成领域异常，避免驱动细节泄漏到业务层。"""
    if isinstance(exc, DatabaseError):
        return exc
    if isinstance(exc, sqlite3.IntegrityError):
        message = str(exc)
        if "余额不足" in message or "余额不能为负数" in message:
            return InsufficientBalanceError("账户余额不足：该操作会导致账户余额为负数")
        if "FOREIGN KEY" in message.upper():
            return ConflictError("关联数据不存在或仍被引用，无法完成操作")
        if "UNIQUE" in message.upper():
            return ConflictError("记录已存在（违反唯一约束）")
        if "CHECK" in message.upper():
            return ConflictError(f"数据未通过完整性校验：{message}")
        return ConflictError(message)
    if isinstance(exc, sqlite3.OperationalError):
        return DatabaseError(f"SQLite 执行失败：{exc}")
    if isinstance(exc, sqlite3.Error):
        return DatabaseError(f"SQLite 错误：{exc}")
    return DatabaseError(str(exc))


class SqliteAdapter(DatabaseAdapter):
    mode = "sqlite"

    def __init__(self, settings) -> None:  # noqa: ANN001
        super().__init__(settings)
        self.path: Path = settings.sqlite_file
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 允许直接把 Decimal 作为参数传入（存为字符串，由 NUMERIC 亲和性转换）
        sqlite3.register_adapter(Decimal, lambda value: str(value))

    # ---------------------------- 连接 ----------------------------
    def connect(self, *, autocommit: bool = True) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=15.0,
                isolation_level=None,  # 自己控制事务边界
                check_same_thread=False,
            )
        except sqlite3.Error as exc:  # pragma: no cover - 防御分支
            raise translate_error(exc) from exc
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def begin(self, connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")

    def commit(self, connection: sqlite3.Connection) -> None:
        connection.execute("COMMIT")

    def rollback(self, connection: sqlite3.Connection) -> None:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:  # 事务可能已因 RAISE(ABORT) 自动结束
            pass

    def close(self, connection: sqlite3.Connection) -> None:
        connection.close()

    # ---------------------------- 读写 ----------------------------
    def fetch(self, connection, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        try:
            cursor = connection.execute(sql, tuple(params))
            return normalize_rows([dict(row) for row in cursor.fetchall()])
        except sqlite3.Error as exc:
            raise translate_error(exc) from exc

    def execute_on(self, connection, sql: str, params: tuple | list = ()) -> int:
        try:
            cursor = connection.execute(sql, tuple(params))
            return int(cursor.rowcount)
        except sqlite3.Error as exc:
            raise translate_error(exc) from exc

    def insert(self, tx: Tx, table: str, values: dict[str, Any]) -> int:
        if table not in PRIMARY_KEYS:
            raise DatabaseError(f"未登记主键的表：{table}")
        columns = list(values)
        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
        try:
            cursor = tx.connection.execute(sql, [values[column] for column in columns])
        except sqlite3.Error as exc:
            raise translate_error(exc) from exc
        return int(cursor.lastrowid or 0)

    def call_transfer(
        self,
        tx: Tx,
        *,
        user_id: int,
        from_account: int,
        to_account: int,
        amount: Decimal,
        transfer_group: str,
        trans_date: str,
        remark: str,
    ) -> None:
        """SQLite 无存储过程：在同一事务内写两条记录，余额由触发器维护。"""
        expense_category = tx.query_one(
            "SELECT category_id FROM category WHERE user_id = ? AND category_name = '其他支出' "
            "AND category_type = '支出' LIMIT 1",
            (user_id,),
        )
        income_category = tx.query_one(
            "SELECT category_id FROM category WHERE user_id = ? AND category_name = '其他收入' "
            "AND category_type = '收入' LIMIT 1",
            (user_id,),
        )
        if not expense_category or not income_category:
            raise NotFoundError("缺少默认转账类别（其他支出 / 其他收入），请先初始化类别数据")

        base_remark = remark.strip() if remark and remark.strip() else "账户间转账"
        # 先记支出，触发器会在此刻校验余额是否充足
        tx.insert(
            "transaction_record",
            {
                "user_id": user_id,
                "account_id": from_account,
                "category_id": expense_category["category_id"],
                "amount": amount,
                "trans_type": "支出",
                "trans_date": trans_date,
                "remark": f"{base_remark}（转出）",
                "transfer_group_id": transfer_group,
            },
        )
        tx.insert(
            "transaction_record",
            {
                "user_id": user_id,
                "account_id": to_account,
                "category_id": income_category["category_id"],
                "amount": amount,
                "trans_type": "收入",
                "trans_date": trans_date,
                "remark": f"{base_remark}（转入）",
                "transfer_group_id": transfer_group,
            },
        )

    # ---------------------------- 报表方言 ----------------------------
    def fetch_monthly_report(self, user_id: int, year_month: str) -> dict[str, Any] | None:
        # PostgreSQL 走 fn_monthly_report()；SQLite 没有函数，改查同名视图
        return self.query_one(
            "SELECT total_income, total_expense, net_balance FROM v_monthly_income_expense "
            "WHERE user_id = ? AND year_month = ?",
            (user_id, year_month),
        )

    def fetch_category_spending(self, user_id: int, start: str, end: str) -> list[dict[str, Any]]:
        return self.query(
            """
            SELECT c.category_name,
                   SUM(t.amount) AS expense_amount,
                   ROUND(SUM(t.amount) * 100.0 / NULLIF((
                       SELECT SUM(amount) FROM transaction_record
                        WHERE user_id = ? AND trans_type = '支出'
                          AND trans_date >= ? AND trans_date <= ?
                   ), 0), 2) AS expense_ratio
              FROM transaction_record t
              JOIN category c ON t.category_id = c.category_id
             WHERE t.user_id = ? AND t.trans_type = '支出'
               AND t.trans_date >= ? AND t.trans_date <= ?
             GROUP BY c.category_name
             ORDER BY expense_amount DESC
            """,
            (user_id, start, end, user_id, start, end),
        )

    # ---------------------------- 结构初始化 ----------------------------
    def init_schema(self) -> dict[str, Any]:
        """建表 + 触发器 + 视图（幂等），返回自检信息。"""
        ddl = SCHEMA_FILE.read_text(encoding="utf-8")
        connection = self.connect()
        try:
            connection.executescript(ddl)
        except sqlite3.Error as exc:
            raise translate_error(exc) from exc
        finally:
            connection.close()
        return self.describe()

    def ping(self) -> bool:
        try:
            row = self.query_one("SELECT 1 AS ok")
        except DatabaseError:
            return False
        return bool(row and row.get("ok") == 1)

    def describe(self) -> dict[str, Any]:
        tables = [
            row["name"]
            for row in self.query(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        views = [
            row["name"]
            for row in self.query("SELECT name FROM sqlite_master WHERE type = 'view' ORDER BY name")
        ]
        triggers = [
            row["name"]
            for row in self.query("SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name")
        ]
        return {
            "mode": self.mode,
            "path": str(self.path),
            "tables": tables,
            "views": views,
            "triggers": triggers,
            "schema_ok": "transaction_record" in tables and "v_net_worth" in views,
        }


__all__ = ["SqliteAdapter", "translate_error"]
