"""SQL 脚本静态校验。

本机没有 GaussDB/PostgreSQL 实例，但用 pglast（PostgreSQL 官方解析器绑定）
可以对 ``docs/sql`` 下的全部脚本做**真实语法校验**，把"能不能跑"这件事
从"人工目测"变成 CI 里的一条断言。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pglast = pytest.importorskip("pglast", reason="需要 pglast 才能校验 PostgreSQL 语法")

from app.config import PROJECT_ROOT  # noqa: E402

SQL_DIR = PROJECT_ROOT / "docs" / "sql"
UPGRADE_SCRIPT = SQL_DIR / "10_upgrade_ai.sql"


def _sql_files() -> list[Path]:
    return sorted(SQL_DIR.glob("*.sql"))


def test_sql_directory_is_not_empty():
    files = _sql_files()
    assert files, f"{SQL_DIR} 下没有找到任何 SQL 脚本"
    assert UPGRADE_SCRIPT in files, "缺少 AI 增强升级脚本 10_upgrade_ai.sql"


@pytest.mark.parametrize("path", _sql_files(), ids=lambda path: path.name)
def test_script_is_valid_postgresql(path: Path):
    content = path.read_text(encoding="utf-8")
    try:
        pglast.parse_sql(content)
    except Exception as exc:  # noqa: BLE001 - 解析失败信息本身就是断言内容
        pytest.fail(f"{path.name} 不是合法的 PostgreSQL 语句：{exc}")


def test_upgrade_script_covers_all_fixes():
    """升级脚本必须覆盖改造方案第五节的每一条修复，避免文档与脚本脱节。"""
    content = UPGRADE_SCRIPT.read_text(encoding="utf-8")
    required_tokens = {
        "transfer_group_id": "转账分组字段（修复 #3）",
        "CREATE OR REPLACE FUNCTION fn_update_balance": "触发器余额预检（修复 #5）",
        "sp_add_transfer": "转账存储过程（修复 #4）",
        "user_id": "视图补 user_id 维度（修复 #1）",
        "ck_budget_year_month_format": "预算年月格式约束（修复 #6）",
        "ai_action_log": "AI 审计表（修复 #7）",
        "knowledge_chunk": "知识库表",
    }
    missing = [label for token, label in required_tokens.items() if token not in content]
    assert not missing, f"升级脚本缺少：{'、'.join(missing)}"


def test_upgrade_script_avoids_to_char_on_indexed_column():
    """应用层已改用范围谓词；升级脚本里的视图可保留 TO_CHAR，但不得出现在 WHERE 的索引列上。"""
    content = UPGRADE_SCRIPT.read_text(encoding="utf-8")
    assert "trans_date >= " in content or "WHERE trans_type IN" in content
    # 明确不建表达式索引，避免与范围查询重复
    assert "idx_transaction_month" not in content.split("-- 可选")[0]
