"""数据层入口：按配置装配适配器，并对上层暴露统一接口。"""

from __future__ import annotations

from .base import (
    ConflictError,
    ConnectionFailure,
    DatabaseAdapter,
    DatabaseError,
    InsufficientBalanceError,
    NotFoundError,
    SchemaError,
    Tx,
    current_month,
    day_shift,
    is_valid_year_month,
    json_safe,
    month_bounds,
    quantize_money,
    to_decimal,
)
from .postgres import PostgresAdapter
from .sqlite import SqliteAdapter

__all__ = [
    "ConflictError",
    "ConnectionFailure",
    "DatabaseAdapter",
    "DatabaseError",
    "InsufficientBalanceError",
    "NotFoundError",
    "PostgresAdapter",
    "SchemaError",
    "SqliteAdapter",
    "Tx",
    "build_adapter",
    "current_month",
    "day_shift",
    "get_adapter",
    "is_valid_year_month",
    "json_safe",
    "month_bounds",
    "quantize_money",
    "reset_adapter",
    "to_decimal",
]

_adapter: DatabaseAdapter | None = None


def build_adapter(settings=None) -> DatabaseAdapter:  # noqa: ANN001
    """按配置新建适配器（测试用；应用内请使用 get_adapter 复用单例）。"""
    from ..config import get_settings

    resolved = settings or get_settings()
    if resolved.db_mode == "sqlite":
        return SqliteAdapter(resolved)
    return PostgresAdapter(resolved)


def get_adapter(settings=None) -> DatabaseAdapter:  # noqa: ANN001
    """进程内单例。原来每个请求都重建连接与客户端，这里只建一次。"""
    global _adapter
    if _adapter is None:
        _adapter = build_adapter(settings)
    return _adapter


def reset_adapter() -> None:
    """测试或切换配置后重置单例。"""
    global _adapter
    _adapter = None
