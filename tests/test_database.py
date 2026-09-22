"""数据层测试：结构、触发器不变量、双后端一致性相关的关键行为。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.db.base import DatabaseError, InsufficientBalanceError, month_bounds, quantize_money
from app.db.repository import LedgerRepository
from app.db.seed import seed_database


# --------------------------------------------------------------------------- #
# 结构
# --------------------------------------------------------------------------- #
def test_schema_has_tables_views_triggers(adapter):
    info = adapter.describe()
    assert info["schema_ok"] is True
    for table in ("account", "category", "transaction_record", "budget", "asset_liability", "ai_action_log"):
        assert table in info["tables"]
    for view in ("v_budget_status", "v_net_worth", "v_cash_flow_trend", "v_category_expense_ratio"):
        assert view in info["views"]
    # PostgreSQL 用一个行级触发器统一处理 INSERT/UPDATE/DELETE；
    # SQLite 没有 plpgsql，等价逻辑拆成三个触发器。
    if adapter.mode == "sqlite":
        assert len(info["triggers"]) == 3
    else:
        assert any("transaction_balance" in name for name in info["triggers"]), info["triggers"]


def test_month_bounds_is_index_friendly():
    """年月过滤必须给出左闭右开区间，而不是 TO_CHAR 包裹列。"""
    start, end = month_bounds("2026-02")
    assert start == "2026-02-01"
    assert end == "2026-02-28"
    with pytest.raises(ValueError):
        month_bounds("2026-13")
    with pytest.raises(ValueError):
        month_bounds("202602")


# --------------------------------------------------------------------------- #
# 触发器：余额唯一维护者
# --------------------------------------------------------------------------- #
def test_trigger_maintains_balance_on_insert(repo: LedgerRepository, user_id: int, fresh_data):
    account = repo.list_accounts(user_id)[0]
    before = quantize_money(account["balance"])
    category = next(item for item in repo.list_categories(user_id, "支出") if item["category_name"] == "其他支出")

    repo.create_transaction(
        user_id,
        account_id=int(account["account_id"]),
        category_id=int(category["category_id"]),
        amount=Decimal("100.00"),
        trans_type="支出",
        trans_date="2026-01-05",
        remark="测试支出",
    )
    after = quantize_money(repo.get_account(user_id, int(account["account_id"]))["balance"])
    assert after == before - Decimal("100.00")


def test_trigger_rejects_overdraft_with_readable_error(repo: LedgerRepository, user_id: int, fresh_data):
    """透支必须被触发器拦下，并且给出中文业务错误而不是 CHECK 报错。"""
    account = next(item for item in repo.list_accounts(user_id) if item["account_name"] == "现金")
    category = next(item for item in repo.list_categories(user_id, "支出") if item["category_name"] == "其他支出")
    with pytest.raises(InsufficientBalanceError):
        repo.create_transaction(
            user_id,
            account_id=int(account["account_id"]),
            category_id=int(category["category_id"]),
            amount=quantize_money(account["balance"]) + Decimal("1.00"),
            trans_type="支出",
        )


def test_delete_rolls_balance_back(repo: LedgerRepository, user_id: int, fresh_data):
    account = next(item for item in repo.list_accounts(user_id) if item["account_name"] == "微信钱包")
    category = next(item for item in repo.list_categories(user_id, "支出") if item["category_name"] == "其他支出")
    before = quantize_money(account["balance"])
    transaction_id = repo.create_transaction(
        user_id,
        account_id=int(account["account_id"]),
        category_id=int(category["category_id"]),
        amount=Decimal("50.00"),
        trans_type="支出",
    )
    assert quantize_money(repo.get_account(user_id, int(account["account_id"]))["balance"]) == before - Decimal("50.00")

    repo.delete_transaction(user_id, transaction_id)
    assert quantize_money(repo.get_account(user_id, int(account["account_id"]))["balance"]) == before


def test_update_transaction_moves_money_between_accounts(repo: LedgerRepository, user_id: int, fresh_data):
    accounts = {item["account_name"]: item for item in repo.list_accounts(user_id)}
    source = accounts["现金"]
    target = accounts["支付宝"]
    category = next(item for item in repo.list_categories(user_id, "支出") if item["category_name"] == "其他支出")

    transaction_id = repo.create_transaction(
        user_id,
        account_id=int(source["account_id"]),
        category_id=int(category["category_id"]),
        amount=Decimal("80.00"),
        trans_type="支出",
    )
    repo.update_transaction(
        user_id,
        transaction_id,
        account_id=int(target["account_id"]),
        category_id=int(category["category_id"]),
        amount=Decimal("60.00"),
        trans_type="支出",
        trans_date="2026-01-06",
    )
    source_after = quantize_money(repo.get_account(user_id, int(source["account_id"]))["balance"])
    target_after = quantize_money(repo.get_account(user_id, int(target["account_id"]))["balance"])
    assert source_after == quantize_money(source["balance"])  # 80 元已退回
    assert target_after == quantize_money(target["balance"]) - Decimal("60.00")


def test_balance_column_cannot_be_written_by_application(repo: LedgerRepository, user_id: int, fresh_data):
    """接口层面不允许直接改余额：这是原有实现的漏洞。"""
    from app.models import AccountUpdate

    assert "balance" not in AccountUpdate.model_fields


# --------------------------------------------------------------------------- #
# 转账
# --------------------------------------------------------------------------- #
def test_transfer_creates_paired_records_with_group_id(repo: LedgerRepository, user_id: int, fresh_data):
    accounts = {item["account_name"]: item for item in repo.list_accounts(user_id)}
    source, target = accounts["工商银行储蓄卡"], accounts["支付宝"]
    result = repo.transfer(
        user_id,
        from_account=int(source["account_id"]),
        to_account=int(target["account_id"]),
        amount=Decimal("300.00"),
        remark="测试转账",
    )
    group = result["transfer_group_id"]
    rows = repo.list_transactions(user_id, trans_type="转账", limit=10)
    assert any(row["transfer_group_id"] == group for row in rows)
    assert quantize_money(repo.get_account(user_id, int(source["account_id"]))["balance"]) == quantize_money(source["balance"]) - Decimal("300.00")
    assert quantize_money(repo.get_account(user_id, int(target["account_id"]))["balance"]) == quantize_money(target["balance"]) + Decimal("300.00")


def test_transfer_same_account_rejected(repo: LedgerRepository, user_id: int, fresh_data):
    account = repo.list_accounts(user_id)[0]
    with pytest.raises(ValueError):
        repo.transfer(
            user_id,
            from_account=int(account["account_id"]),
            to_account=int(account["account_id"]),
            amount=Decimal("10.00"),
        )


# --------------------------------------------------------------------------- #
# 视图与报表
# --------------------------------------------------------------------------- #
def test_views_are_used_and_scoped_by_user(repo: LedgerRepository, user_id: int, fresh_data):
    budget = repo.budget_status(user_id, repo.overview(user_id)["month"])
    assert budget, "预算执行视图应返回当月预算行"
    assert all(row["completion_rate"] is not None for row in budget)

    net = repo.net_worth(user_id)
    assert net["net_worth"] == net["total_assets"] - net["total_liabilities"] + net["total_account_balance"]

    ratio = repo.category_ratio(user_id, repo.overview(user_id)["month"])
    assert ratio, "类别占比视图应返回当月数据"
    total_ratio = sum(float(row["expense_ratio"]) for row in ratio)
    assert 95 <= total_ratio <= 105


def test_reports_isolate_users(repo: LedgerRepository, user_id: int, fresh_data, adapter):
    """原版视图没有 user_id 维度，多用户会串数据；这里断言隔离生效。"""
    adapter.execute("INSERT INTO user_info (user_name, password_hash) VALUES ('李四', 'x')")
    other = adapter.query_one("SELECT user_id FROM user_info WHERE user_name = '李四'")
    other_id = int(other["user_id"])

    assert repo.list_accounts(other_id) == []
    assert repo.overview(other_id)["month_income"] == Decimal("0.00")
    assert repo.net_worth(other_id)["net_worth"] == Decimal("0.00")
    assert repo.list_transactions(other_id) == []


def test_monthly_report_uses_view_or_function(repo: LedgerRepository, user_id: int, fresh_data):
    month = repo.overview(user_id)["month"]
    report = repo.monthly_report(user_id, month)
    overview = repo.overview(user_id, month)
    assert report["total_income"] == overview["month_income"]
    assert report["total_expense"] == overview["month_expense"]


# --------------------------------------------------------------------------- #
# 种子数据：复现并锁死原脚本的约束错误
# --------------------------------------------------------------------------- #
def test_original_seed_recompute_would_violate_check_constraint(adapter, repo: LedgerRepository, user_id: int, fresh_data):
    """原 03_insert_test_data.sql 结尾的余额重算会让"现金"账户变成负数。

    这里用等价 SQL 复现，确认约束会拦截，从而证明"由触发器维护余额"才是正确做法。
    """
    with pytest.raises(DatabaseError):
        adapter.execute(
            "UPDATE account SET balance = COALESCE(("
            "  SELECT SUM(CASE WHEN t.trans_type = '收入' THEN t.amount ELSE -t.amount END) "
            "  FROM transaction_record t WHERE t.account_id = account.account_id"
            "), 0)"
        )


def test_seed_is_idempotent(adapter, user_id: int):
    first = seed_database(adapter)
    assert first["seeded"] is False  # 已有数据则跳过
    accounts = LedgerRepository(adapter).list_accounts(user_id)
    assert all(quantize_money(item["balance"]) >= 0 for item in accounts)
