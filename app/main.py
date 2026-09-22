"""FastAPI 应用入口。

启动：
    .venv\\Scripts\\python.exe -m uvicorn app.main:app --reload --port 8000

设计要点：
* 启动时按配置初始化数据库结构（SQLite 自动建表/触发器/视图并播种，
  PostgreSQL 只做结构自检，结构由 docs/sql 脚本管理）；
* 领域异常统一映射为 HTTP 状态码，前端拿到的是可读中文提示而不是堆栈；
* 前端静态页由本服务托管，因此不再需要 ``allow_origins=['*']``。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import PROJECT_ROOT, get_settings
from .db import (
    ConflictError,
    ConnectionFailure,
    DatabaseError,
    InsufficientBalanceError,
    NotFoundError,
    SchemaError,
    get_adapter,
)
from .db.repository import LedgerRepository
from .db.seed import seed_database
from .routers import ai as ai_router
from .routers import api as api_router

logger = logging.getLogger("finance")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

FRONTEND_DIR = PROJECT_ROOT / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    adapter = get_adapter(settings)
    schema = adapter.init_schema()
    logger.info("数据库模式=%s 结构自检=%s", adapter.mode, schema.get("schema_ok"))
    if adapter.mode == "sqlite":
        result = seed_database(adapter)
        logger.info("演示数据：%s", result)
    elif not schema.get("schema_ok"):
        logger.warning("PostgreSQL 结构不完整：%s", schema.get("hint") or schema.get("missing_tables"))

    assistant = None
    if settings.chat_ready:
        from .deps import get_assistant

        assistant = get_assistant()
        logger.info("AI 对话已启用：模型=%s", settings.chat_model)
    else:
        logger.info("未配置 DEEPSEEK_API_KEY：AI 能力降级为规则模式（记账与可负担性分析仍可用）")
    yield


app = FastAPI(
    title="个人理财助手 API",
    version="2.0.0",
    description="数据库课设升级版：双数据库适配 + 可解释的 AI 理财助手",
    lifespan=lifespan,
)

# 前端由本服务托管，同源访问；保留 CORS 仅用于本地调试其它端口
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:5500", "null"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router.router)
app.include_router(ai_router.router)


def _status_for(exc: Exception) -> int:
    if isinstance(exc, NotFoundError):
        return 404
    if isinstance(exc, InsufficientBalanceError):
        return 400
    if isinstance(exc, ConflictError):
        return 409
    if isinstance(exc, (SchemaError, ConnectionFailure)):
        return 503
    return 500


@app.exception_handler(DatabaseError)
async def database_error_handler(request: Request, exc: DatabaseError) -> JSONResponse:
    status = _status_for(exc)
    if status >= 500:
        logger.exception("数据库错误：%s", exc)
    return JSONResponse(status_code=status, content={"error": str(exc), "type": exc.__class__.__name__})


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.get("/", include_in_schema=False)
def index():
    """返回前端页面；没有构建产物时给一份可读的说明。"""
    page = FRONTEND_DIR / "index.html"
    if page.exists():
        return FileResponse(page)
    return {
        "message": "个人理财助手 API 已启动",
        "docs": "/docs",
        "frontend": "未找到 frontend/index.html",
    }


@app.get("/api/health", summary="健康检查（含数据库与 AI 状态）")
def health():
    settings = get_settings()
    adapter = get_adapter()
    repository = LedgerRepository(adapter)
    try:
        stats = repository.stats(settings.default_user_id)
    except DatabaseError as exc:
        stats = {"error": str(exc)}
    return {
        "status": "ok",
        "version": settings.version,
        "config": settings.describe(),
        "database": adapter.describe(),
        "data": stats,
    }


# 前端挂在根路径（html=True 时 "/" 也返回 index.html），
# 这样页面里的 ./style.css、./app.js 相对链接才能解析；
# 所有 /api/* 与 /docs 路由在它之前注册，因此优先级更高。
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
