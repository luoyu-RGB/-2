"""数据访问层公共契约。

两个后端（PostgreSQL / SQLite）共享同一套领域方法签名，差异集中在各自的
方言原语里（``insert`` / ``call_transfer`` / 报表函数调用）。

统一约定：
* 索引友好的日期范围谓词（修复方案第五节第 2 条，不再用 ``TO_CHAR(trans_date)``）
* 金额在业务层一律是 ``Decimal``，读出来即量化到 2 位小数
* 领域异常统一为 ``DatabaseError`` 子类，路由层据此映射 HTTP 状态码
"""

from __future__ import annotations

import calendar
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterator

TWO_PLACES = Decimal("0.01")

# 需要按金额量化的列（两个后端返回类型不同：PG 是 Decimal，SQLite 是 float/int）
MONEY_COLUMNS: frozenset[str] = frozenset(
    {
        "balance",
        "amount",
        "budget_amount",
        "actual_expense",
        "expense_amount",
        "total_income",
        "total_expense",
        "net_balance",
        "daily_income",
        "daily_expense",
        "net_cashflow",
        "total_assets",
        "total_liabilities",
        "total_account_balance",
        "net_worth",
        "income",
        "expense",
        "assets",
        "liabilities",
        "account_balance",
        "daily_avg",
        "month_income",
        "month_expense",
        "today_income",
        "today_expense",
        "total_income_all",
        "total_expense_all",
    }
)

# 比率列按 2 位小数保留
RATE_COLUMNS: frozenset[str] = frozenset({"expense_ratio", "completion_rate"})

# 表 → 主键列，供 insert() 生成 RETURNING / lastrowid
PRIMARY_KEYS: dict[str, str] = {
    "user_info": "user_id",
    "account": "account_id",
    "category": "category_id",
    "transaction_record": "transaction_id",
    "budget": "budget_id",
    "asset_liability": "item_id",
    "ai_action_log": "log_id",
    "knowledge_document": "document_id",
    "knowledge_chunk": "chunk_id",
}

# 触发器唯一维护余额——应用层禁止直接写这一列
BALANCE_GUARD_COLUMN = "balance"


# --------------------------------------------------------------------------- #
# 领域异常
# --------------------------------------------------------------------------- #
class DatabaseError(RuntimeError):
    """数据层异常基类。"""


class ConnectionFailure(DatabaseError):
    """无法连接数据库（配置错误、服务未启动、网络不可达）。"""


class SchemaError(DatabaseError):
    """库结构不符合预期（例如尚未执行升级脚本）。"""


class NotFoundError(DatabaseError):
    """目标记录不存在。"""


class InsufficientBalanceError(DatabaseError):
    """余额不足（由触发器或适配层前置校验抛出）。"""


class ConflictError(DatabaseError):
    """违反唯一约束 / 外键约束。"""


# --------------------------------------------------------------------------- #
# 数值与日期工具
# --------------------------------------------------------------------------- #
def to_decimal(value: Any) -> Decimal:
    """把任意数值安全地转成 Decimal，避免二进制浮点误差进入金额计算。"""
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    if isinstance(value, bool):
        raise ValueError("布尔值不能作为金额")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:  # pragma: no cover - 防御分支
        raise ValueError(f"无法转换为金额：{value!r}") from exc


def quantize_money(value: Any) -> Decimal:
    """金额统一四舍五入到 2 位小数（银行家场景使用 HALF_UP，与账目习惯一致）。"""
    return to_decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def current_month() -> str:
    return date.today().isoformat()[:7]


def month_bounds(year_month: str) -> tuple[str, str]:
    """把 ``YYYY-MM`` 转成左闭右开的日期区间。

    这样 ``trans_date >= %s AND trans_date < %s`` 能命中 ``idx_transaction_date``，
    而原来的 ``TO_CHAR(trans_date,'YYYY-MM') = %s`` 会让索引失效。
    """
    text = (year_month or "").strip()
    parts = text.split("-")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"年月格式应为 YYYY-MM，当前为 {year_month!r}")
    year, month = int(parts[0]), int(parts[1])
    if not 1 <= month <= 12:
        raise ValueError(f"月份越界：{year_month!r}")
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"


def is_valid_year_month(year_month: str) -> bool:
    try:
        month_bounds(year_month)
    except ValueError:
        return False
    return True


def day_shift(iso_date: str, days: int) -> str:
    """把 ``YYYY-MM-DD`` 平移若干天，用于"近 N 天"这类可索引范围查询。"""
    from datetime import timedelta

    base = date.fromisoformat(iso_date)
    return (base + timedelta(days=days)).isoformat()


def normalize_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """统一两个后端的数值类型：金额/比率 → Decimal(2)。"""
    if row is None:
        return None
    normalized: dict[str, Any] = {}
    for key, value in row.items():
        if value is None:
            normalized[key] = None
        elif key in MONEY_COLUMNS or key in RATE_COLUMNS:
            normalized[key] = quantize_money(value)
        else:
            normalized[key] = value
    return normalized


def normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_row(row) or {} for row in rows]


def json_safe(value: Any) -> Any:
    """递归把 Decimal 转成 float，供 FastAPI 之外的地方（如写审计 JSON）使用。"""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


# --------------------------------------------------------------------------- #
# 事务执行器
# --------------------------------------------------------------------------- #
class Tx:
    """一次写事务内的执行器。所有写操作都必须经由它，保证原子性。"""

    def __init__(self, adapter: "DatabaseAdapter", connection: Any) -> None:
        self.adapter = adapter
        self.connection = connection

    def query(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        return self.adapter.fetch(self.connection, sql, params)

    def query_one(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple | list = ()) -> int:
        return self.adapter.execute_on(self.connection, sql, params)

    def insert(self, table: str, values: dict[str, Any]) -> int:
        """插入一行并返回主键，由各后端用自己的方言实现。"""
        return self.adapter.insert(self, table, values)


# --------------------------------------------------------------------------- #
# 适配器抽象
# --------------------------------------------------------------------------- #
class DatabaseAdapter(ABC):
    """数据库适配器：读走连接池式短连接，写走显式事务。"""

    mode: str = "unknown"

    def __init__(self, settings) -> None:  # noqa: ANN001 - 避免循环导入
        self.settings = settings

    # ---------------------------- 生命周期 ----------------------------
    @abstractmethod
    def connect(self, *, autocommit: bool = True) -> Any:
        """建立一个新连接。调用方负责关闭。"""

    @abstractmethod
    def fetch(self, connection: Any, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        """在给定连接上执行查询，返回字典列表。"""

    @abstractmethod
    def execute_on(self, connection: Any, sql: str, params: tuple | list = ()) -> int:
        """在给定连接上执行写语句，返回受影响行数。"""

    @abstractmethod
    def insert(self, tx: Tx, table: str, values: dict[str, Any]) -> int:
        """插入一行并返回主键。"""

    @abstractmethod
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
        """执行转账。

        PostgreSQL：调用存储过程 ``sp_add_transfer``（一次性写入两条记录）。
        SQLite：同一事务内写两条记录（SQLite 无存储过程，这是唯一的实现差异）。
        两者都由触发器负责余额变更。
        """

    @abstractmethod
    def init_schema(self) -> dict[str, Any]:
        """确保库结构可用，返回自检信息。"""

    @abstractmethod
    def ping(self) -> bool:
        """连通性探测。"""

    @abstractmethod
    def fetch_monthly_report(self, user_id: int, year_month: str) -> dict[str, Any] | None:
        """月度收支汇总：PG 走 ``fn_monthly_report``，SQLite 走同名视图。"""

    @abstractmethod
    def fetch_category_spending(self, user_id: int, start: str, end: str) -> list[dict[str, Any]]:
        """区间类别支出：PG 走 ``fn_category_spending``，SQLite 走等价聚合。"""

    # ---------------------------- 通用读写 ----------------------------
    def query(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        connection = self.connect()
        try:
            return self.fetch(connection, sql, params)
        finally:
            self.close(connection)

    def query_one(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple | list = ()) -> int:
        connection = self.connect()
        try:
            self.begin(connection)
            affected = self.execute_on(connection, sql, params)
            self.commit(connection)
            return affected
        except Exception:
            self.rollback(connection)
            raise
        finally:
            self.close(connection)

    @contextmanager
    def write_transaction(self) -> Iterator[Tx]:
        """显式写事务：进入时开启，正常退出提交，异常回滚。"""
        connection = self.connect(autocommit=False)
        self.begin(connection)
        tx = Tx(self, connection)
        try:
            yield tx
            self.commit(connection)
        except Exception:
            self.rollback(connection)
            raise
        finally:
            self.close(connection)

    # ---------------------------- 事务控制 ----------------------------
    def begin(self, connection: Any) -> None:  # pragma: no cover - 由子类覆盖
        pass

    def commit(self, connection: Any) -> None:  # pragma: no cover - 由子类覆盖
        pass

    def rollback(self, connection: Any) -> None:  # pragma: no cover - 由子类覆盖
        pass

    def close(self, connection: Any) -> None:  # pragma: no cover - 由子类覆盖
        pass

    # ---------------------------- 元信息 ----------------------------
    def describe(self) -> dict[str, Any]:
        return {"mode": self.mode}


__all__ = [
    "BALANCE_GUARD_COLUMN",
    "ConflictError",
    "ConnectionFailure",
    "DatabaseAdapter",
    "DatabaseError",
    "InsufficientBalanceError",
    "MONEY_COLUMNS",
    "NotFoundError",
    "PRIMARY_KEYS",
    "RATE_COLUMNS",
    "SchemaError",
    "TWO_PLACES",
    "Tx",
    "current_month",
    "day_shift",
    "is_valid_year_month",
    "json_safe",
    "month_bounds",
    "normalize_row",
    "normalize_rows",
    "quantize_money",
    "to_decimal",
]
