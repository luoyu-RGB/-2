"""PostgreSQL / GaussDB 适配器 —— 正式路径。

与原 Flask 版的关键差别：
1. 连接参数全部来自 ``.env``，源码里不再出现任何账号密码；
2. 转账调用存储过程 ``sp_add_transfer``（带账户归属校验与 transfer_group_id）；
3. 月度/类别报表直接调用 ``fn_monthly_report`` / ``fn_category_spending``，
   不再在应用层重写一遍聚合 SQL；
4. 启动时做结构自检，缺列/缺表会给出明确的升级脚本提示，而不是运行到一半报错。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .base import (
    ConflictError,
    ConnectionFailure,
    DatabaseAdapter,
    DatabaseError,
    InsufficientBalanceError,
    NotFoundError,
    PRIMARY_KEYS,
    SchemaError,
    Tx,
    normalize_rows,
)

REQUIRED_TABLES = (
    "user_info",
    "account",
    "category",
    "transaction_record",
    "budget",
    "asset_liability",
    "ai_action_log",
    "knowledge_document",
    "knowledge_chunk",
)
REQUIRED_VIEWS = (
    "v_transaction_detail",
    "v_account_balances",
    "v_monthly_income_expense",
    "v_category_expense_ratio",
    "v_budget_status",
    "v_cash_flow_trend",
    "v_net_worth",
)


def translate_error(exc: Exception) -> DatabaseError:
    """把 psycopg2 异常翻译成领域异常。"""
    if isinstance(exc, DatabaseError):
        return exc
    try:
        import psycopg2
        from psycopg2 import errors as pg_errors
    except ImportError:  # pragma: no cover - 未安装驱动时直接透传
        return DatabaseError(str(exc))

    if isinstance(exc, pg_errors.UniqueViolation):
        return ConflictError("记录已存在（违反唯一约束）")
    if isinstance(exc, pg_errors.ForeignKeyViolation):
        return ConflictError("关联数据不存在或仍被引用，无法完成操作")
    if isinstance(exc, pg_errors.CheckViolation):
        message = getattr(exc, "pgerror", "") or str(exc)
        if "余额" in message or "balance" in message:
            return InsufficientBalanceError("账户余额不足：该操作会导致账户余额为负数")
        return ConflictError(f"数据未通过完整性校验：{message.strip()}")
    if isinstance(exc, pg_errors.RaiseException):
        message = getattr(exc, "pgerror", "") or str(exc)
        if "余额不足" in message:
            return InsufficientBalanceError(message.strip())
        return DatabaseError(message.strip())
    if isinstance(exc, pg_errors.UndefinedTable):
        return SchemaError("数据库结构不完整：缺少表或视图，请先执行 docs/sql 下的建表与升级脚本")
    if isinstance(exc, pg_errors.UndefinedColumn):
        return SchemaError("数据库结构过旧：缺少字段，请执行 docs/sql/10_upgrade_ai.sql")
    if isinstance(exc, pg_errors.UndefinedFunction):
        return SchemaError("数据库结构过旧：缺少存储过程/函数，请执行 docs/sql/07_create_functions.sql")
    if isinstance(exc, psycopg2.OperationalError):
        return ConnectionFailure(f"无法连接 PostgreSQL/GaussDB：{exc}")
    if isinstance(exc, psycopg2.Error):
        return DatabaseError(str(exc).strip())
    return DatabaseError(str(exc))


class PostgresAdapter(DatabaseAdapter):
    mode = "postgres"

    # ---------------------------- 连接 ----------------------------
    def connect(self, *, autocommit: bool = True):
        try:
            import psycopg2
        except ImportError as exc:  # pragma: no cover
            raise ConnectionFailure(
                "未安装 psycopg2，请执行：pip install psycopg2-binary（或把 DB_MODE 改为 sqlite）"
            ) from exc

        try:
            connection = psycopg2.connect(**self.settings.pg_dsn())
        except psycopg2.Error as exc:
            raise translate_error(exc) from exc
        connection.autocommit = autocommit
        return connection

    def begin(self, connection) -> None:
        # psycopg2 在 autocommit=False 时由第一条语句隐式开启事务
        pass

    def commit(self, connection) -> None:
        connection.commit()

    def rollback(self, connection) -> None:
        try:
            connection.rollback()
        except Exception:  # pragma: no cover - 连接可能已失效
            pass

    def close(self, connection) -> None:
        connection.close()

    # ---------------------------- 读写 ----------------------------
    def _cursor(self, connection):
        from psycopg2.extras import RealDictCursor

        return connection.cursor(cursor_factory=RealDictCursor)

    def fetch(self, connection, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        cursor = self._cursor(connection)
        try:
            cursor.execute(sql, tuple(params))
            return normalize_rows([dict(row) for row in cursor.fetchall()])
        except Exception as exc:
            raise translate_error(exc) from exc
        finally:
            cursor.close()

    def execute_on(self, connection, sql: str, params: tuple | list = ()) -> int:
        cursor = connection.cursor()
        try:
            cursor.execute(sql, tuple(params))
            return int(cursor.rowcount)
        except Exception as exc:
            raise translate_error(exc) from exc
        finally:
            cursor.close()

    def insert(self, tx: Tx, table: str, values: dict[str, Any]) -> int:
        primary_key = PRIMARY_KEYS.get(table)
        if not primary_key:
            raise DatabaseError(f"未登记主键的表：{table}")
        columns = list(values)
        placeholders = ", ".join(["%s"] * len(columns))
        sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
            f"RETURNING {primary_key}"
        )
        cursor = tx.connection.cursor()
        try:
            cursor.execute(sql, [values[column] for column in columns])
            return int(cursor.fetchone()[0])
        except Exception as exc:
            raise translate_error(exc) from exc
        finally:
            cursor.close()

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
        """调用存储过程，账户归属校验、原子性与 transfer_group_id 都在库内完成。"""
        cursor = tx.connection.cursor()
        try:
            cursor.execute(
                "CALL sp_add_transfer(%s, %s, %s, %s, %s, %s, %s)",
                (user_id, from_account, to_account, amount, transfer_group, trans_date, remark),
            )
        except Exception as exc:
            raise translate_error(exc) from exc
        finally:
            cursor.close()

    # ---------------------------- 报表方言 ----------------------------
    def fetch_monthly_report(self, user_id: int, year_month: str) -> dict[str, Any] | None:
        """直接调用库内函数 fn_monthly_report(user_id, year_month)。"""
        rows = self.query("SELECT * FROM fn_monthly_report(%s, %s)", (user_id, year_month))
        return normalize_rows(rows)[0] if rows else None

    def fetch_category_spending(self, user_id: int, start: str, end: str) -> list[dict[str, Any]]:
        """直接调用库内函数 fn_category_spending(user_id, start, end)。"""
        return self.query("SELECT * FROM fn_category_spending(%s, %s, %s)", (user_id, start, end))

    # ---------------------------- 结构自检 ----------------------------
    def init_schema(self) -> dict[str, Any]:
        """正式路径不自动建表（结构由 docs/sql 脚本管理），只做自检。"""
        return self.describe()

    def describe(self) -> dict[str, Any]:
        info: dict[str, Any] = {"mode": self.mode}
        try:
            tables = {
                row["table_name"]
                for row in self.query(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_type = 'BASE TABLE'",
                    (self.settings.pg_schema,),
                )
            }
            views = {
                row["table_name"]
                for row in self.query(
                    "SELECT table_name FROM information_schema.views WHERE table_schema = %s",
                    (self.settings.pg_schema,),
                )
            }
            triggers = {
                row["trigger_name"]
                for row in self.query(
                    "SELECT DISTINCT trigger_name FROM information_schema.triggers "
                    "WHERE trigger_schema = %s",
                    (self.settings.pg_schema,),
                )
            }
            info.update(
                {
                    "tables": sorted(tables),
                    "views": sorted(views),
                    "triggers": sorted(triggers),
                    "missing_tables": sorted(set(REQUIRED_TABLES) - tables),
                    "missing_views": sorted(set(REQUIRED_VIEWS) - views),
                }
            )
            info["schema_ok"] = not info["missing_tables"] and not info["missing_views"]
            if info["schema_ok"] and not any("transaction_balance" in name for name in triggers):
                info["schema_ok"] = False
                info["hint"] = "缺少余额维护触发器，请执行 docs/sql/06_create_triggers.sql 与 10_upgrade_ai.sql"
            elif not info["schema_ok"]:
                info["hint"] = (
                    "请按 docs/sql 顺序执行 02 建表、04 索引、05 视图、06 触发器、07 函数，"
                    "并执行 10_upgrade_ai.sql 升级脚本（新增 transfer_group_id 与 ai_action_log）"
                )
        except DatabaseError as exc:
            info["schema_ok"] = False
            info["error"] = str(exc)
        return info

    def ping(self) -> bool:
        try:
            row = self.query_one("SELECT 1 AS ok")
        except DatabaseError:
            return False
        return bool(row and row.get("ok") == 1)


__all__ = ["PostgresAdapter", "REQUIRED_TABLES", "REQUIRED_VIEWS", "translate_error"]
