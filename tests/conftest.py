"""pytest 公共夹具。

要点：
* 必须在导入 app 之前设定环境变量，因为配置是进程级单例（``get_settings`` 带 lru_cache）；
* 默认使用独立的临时 SQLite 库，避免污染演示库；
* 设 ``TEST_DB_MODE=postgres`` 可让**同一套 63 个用例**跑在真实 PostgreSQL/GaussDB 上：:

      $env:TEST_DB_MODE="postgres"; .venv\\Scripts\\python.exe -m pytest -q

  此时 PG_* 连接参数从 .env 读取（见 scripts/verify_postgres.py）。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

MODE = os.environ.get("TEST_DB_MODE", "sqlite").strip().lower()

TEST_DIR = Path(tempfile.mkdtemp(prefix="finance-test-"))
if MODE in ("postgres", "postgresql", "pg"):
    os.environ["DB_MODE"] = "postgres"
else:
    os.environ["DB_MODE"] = "sqlite"
    os.environ["SQLITE_PATH"] = str(TEST_DIR / "test.db")
# 测试环境一律不配 Key：验证"没有大模型也能用"这条降级路径
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["EMBEDDING_PROVIDER"] = "bm25"
os.environ["AI_ENABLED"] = "true"

import pytest  # noqa: E402

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.db import build_adapter, reset_adapter  # noqa: E402
from app.db.repository import LedgerRepository  # noqa: E402
from app.db.seed import seed_database  # noqa: E402


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def adapter(settings):
    reset_adapter()
    instance = build_adapter(settings)
    schema = instance.init_schema()
    if not schema.get("schema_ok", False):
        pytest.exit(
            f"数据库结构不完整，无法测试：{schema.get('missing_tables')} {schema.get('missing_views')}",
            returncode=2,
        )
    seed_database(instance, force=True)
    return instance


@pytest.fixture()
def repo(adapter) -> LedgerRepository:
    return LedgerRepository(adapter)


@pytest.fixture()
def user_id(settings) -> int:
    return settings.default_user_id


@pytest.fixture()
def fresh_data(adapter):
    """需要确定性数据的用例：每个用例前重新播种。"""
    seed_database(adapter, force=True)
    return adapter


@pytest.fixture(scope="session")
def client(adapter):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
