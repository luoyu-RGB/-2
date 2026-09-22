"""FastAPI 依赖注入：单例服务 + 当前用户。

与改造前的区别：原 Flask 版每个请求都 ``psycopg2.connect()`` 并且把 user_id
硬编码为 1；这里适配器是进程单例，用户身份通过请求头传入（默认配置用户）。
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Header

from .ai.agent import FinanceAssistant
from .ai.knowledge import KnowledgeBase
from .config import Settings, get_settings
from .db import DatabaseAdapter, get_adapter
from .db.repository import LedgerRepository
from .services.affordability import AffordabilityService


def current_user_id(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    settings: Settings = Depends(get_settings),
) -> int:
    """取当前用户 ID。

    课设场景下没有登录体系，因此允许前端指定；一旦接入登录，
    把这里换成从 token 解析即可，其余代码不需要改动。
    """
    resolved = settings
    if x_user_id and x_user_id.strip().isdigit():
        return int(x_user_id.strip())
    return resolved.default_user_id


@lru_cache(maxsize=1)
def get_repository() -> LedgerRepository:
    return LedgerRepository(get_adapter())


@lru_cache(maxsize=1)
def get_affordability_service() -> AffordabilityService:
    return AffordabilityService(get_repository())


@lru_cache(maxsize=1)
def get_knowledge_base() -> KnowledgeBase:
    return KnowledgeBase(get_adapter(), get_settings())


@lru_cache(maxsize=1)
def get_assistant() -> FinanceAssistant:
    return FinanceAssistant(
        get_repository(),
        get_settings(),
        knowledge=get_knowledge_base(),
    )


def get_database() -> DatabaseAdapter:
    return get_adapter()


__all__ = [
    "current_user_id",
    "get_affordability_service",
    "get_assistant",
    "get_database",
    "get_knowledge_base",
    "get_repository",
]
