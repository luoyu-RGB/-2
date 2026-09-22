"""已退役：旧的 69 行 SQLite 演示后端。

统一后端已经迁移到项目根目录的 ``app/`` 包（FastAPI + 双数据库适配层），
原先这个文件和 ``app/`` 各维护一套数据模型，导致 schema 漂移
（旧版没有外键、没有触发器，余额由 Python 手算）。现在只保留一个转发入口，
让旧的启动命令继续可用：

    python -m uvicorn backend.app:app --reload --port 8000   # 仍然能跑

推荐改用：

    python -m uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

from app.main import app  # noqa: F401  （对外暴露同名 ASGI 应用）

__all__ = ["app"]
