"""账本与报表路由。

接口路径与原 Flask 版保持一致（``/api/*``），因此原有的两个前端页面无需改动
即可迁移过来；新增的接口都带注释标出。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from ..db.repository import LedgerRepository
from ..deps import current_user_id, get_database, get_repository
from ..models import (
    AccountCreate,
    AccountUpdate,
    AssetLiabilityIn,
    BudgetSet,
    TransactionCreate,
    TransactionUpdate,
    TransferCreate,
)

router = APIRouter(prefix="/api", tags=["账本"])


# --------------------------------------------------------------------------- #
# 账户
# --------------------------------------------------------------------------- #
@router.get("/accounts", summary="账户列表（读视图 v_account_balances）")
def list_accounts(user_id: int = Depends(current_user_id), repo: LedgerRepository = Depends(get_repository)):
    return repo.list_accounts(user_id)


@router.post("/accounts", status_code=201, summary="新建账户")
def create_account(
    payload: AccountCreate,
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    account_id = repo.create_account(user_id, payload.account_name, payload.account_type, payload.balance)
    return {"success": True, "account_id": account_id}


@router.put("/accounts/{account_id}", summary="修改账户名称/类型（余额只能由交易驱动）")
def update_account(
    account_id: int,
    payload: AccountUpdate,
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    repo.update_account(user_id, account_id, payload.account_name, payload.account_type)
    return {"success": True}


@router.delete("/accounts/{account_id}", summary="删除账户及其交易（余额由 DELETE 触发器回滚）")
def delete_account(
    account_id: int,
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    return {"success": True, **repo.delete_account(user_id, account_id)}


# --------------------------------------------------------------------------- #
# 类别
# --------------------------------------------------------------------------- #
@router.get("/categories", summary="类别列表（支持父子层级）")
def list_categories(
    type: str | None = Query(default=None, description="收入 / 支出"),
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    return repo.list_categories(user_id, type)


# --------------------------------------------------------------------------- #
# 交易
# --------------------------------------------------------------------------- #
@router.get("/transactions", summary="交易查询（月份用可索引范围谓词）")
def list_transactions(
    month: str | None = None,
    trans_type: str | None = None,
    account_id: int | None = None,
    category_id: int | None = None,
    keyword: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    return repo.list_transactions(
        user_id,
        month=month,
        trans_type=trans_type,
        account_id=account_id,
        category_id=category_id,
        keyword=keyword,
        limit=limit,
        offset=offset,
    )


@router.post("/transactions", status_code=201, summary="新增交易（余额由触发器维护）")
def create_transaction(
    payload: TransactionCreate,
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    transaction_id = repo.create_transaction(
        user_id,
        account_id=payload.account_id,
        category_id=payload.category_id,
        amount=payload.amount,
        trans_type=payload.trans_type,
        trans_date=payload.trans_date,
        remark=payload.remark,
    )
    return {"success": True, "transaction_id": transaction_id}


@router.put("/transactions/{transaction_id}", summary="修改交易")
def update_transaction(
    transaction_id: int,
    payload: TransactionUpdate,
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    repo.update_transaction(
        user_id,
        transaction_id,
        account_id=payload.account_id,
        category_id=payload.category_id,
        amount=payload.amount,
        trans_type=payload.trans_type,
        trans_date=payload.trans_date,
        remark=payload.remark,
    )
    return {"success": True}


@router.delete("/transactions/{transaction_id}", summary="删除交易")
def delete_transaction(
    transaction_id: int,
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    repo.delete_transaction(user_id, transaction_id)
    return {"success": True}


# --------------------------------------------------------------------------- #
# 转账
# --------------------------------------------------------------------------- #
@router.post("/transfers", status_code=201, summary="转账（PostgreSQL 走 sp_add_transfer 存储过程）")
def create_transfer(
    payload: TransferCreate,
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    result = repo.transfer(
        user_id,
        from_account=payload.from_account,
        to_account=payload.to_account,
        amount=payload.amount,
        trans_date=payload.trans_date,
        remark=payload.remark,
    )
    return {"success": True, **result}


# --------------------------------------------------------------------------- #
# 概览与报表
# --------------------------------------------------------------------------- #
@router.get("/overview", summary="总览（净资产读视图 v_net_worth）")
def overview(
    month: str | None = None,
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    return repo.overview(user_id, month)


@router.get("/net-worth", summary="净资产（读视图 v_net_worth）")
def net_worth(user_id: int = Depends(current_user_id), repo: LedgerRepository = Depends(get_repository)):
    data = repo.net_worth(user_id)
    return {
        "assets": data.get("total_assets"),
        "liabilities": data.get("total_liabilities"),
        "account_balance": data.get("total_account_balance"),
        "net_worth": data.get("net_worth"),
    }


@router.get("/monthly-trend", summary="月度收支趋势（读视图 v_monthly_income_expense）")
def monthly_trend(user_id: int = Depends(current_user_id), repo: LedgerRepository = Depends(get_repository)):
    return [
        {
            "ym": row["year_month"],
            "income": row["total_income"],
            "expense": row["total_expense"],
            "net_balance": row["net_balance"],
        }
        for row in repo.monthly_trend(user_id)
    ]


@router.get("/daily-cashflow", summary="近 N 天日现金流（读视图 v_cash_flow_trend）")
def daily_cashflow(
    days: int = Query(default=30, ge=1, le=365),
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    return repo.daily_cashflow(user_id, days)


@router.get("/category-ratio", summary="类别支出占比（读视图 v_category_expense_ratio）")
def category_ratio(
    month: str | None = None,
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    return repo.category_ratio(user_id, month)


@router.get("/monthly-report", summary="月度收支汇总（PostgreSQL 走 fn_monthly_report）")
def monthly_report(
    month: str | None = None,
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    return repo.monthly_report(user_id, month or date.today().isoformat()[:7])


# --------------------------------------------------------------------------- #
# 预算
# --------------------------------------------------------------------------- #
@router.get("/budgets", summary="预算列表")
def list_budgets(
    month: str | None = None,
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    return repo.list_budgets(user_id, month or date.today().isoformat()[:7])


@router.post("/budgets", summary="设置预算（upsert）")
def set_budget(
    payload: BudgetSet,
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    repo.upsert_budget(user_id, payload.category_id, payload.year_month, payload.amount)
    return {"success": True}


@router.get("/budget-status", summary="预算执行情况（读视图 v_budget_status）")
def budget_status(
    month: str | None = None,
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    return repo.budget_status(user_id, month or date.today().isoformat()[:7])


@router.post("/budgets/copy", summary="复制预算到目标月份（PostgreSQL 走 sp_copy_budget）")
def copy_budget(
    source_month: str = Body(..., embed=True),
    target_month: str = Body(..., embed=True),
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    count = repo.copy_budget(user_id, source_month, target_month)
    return {"success": True, "copied": count}


# --------------------------------------------------------------------------- #
# 资产负债
# --------------------------------------------------------------------------- #
@router.get("/asset-liability", summary="资产负债列表")
def list_asset_liability(user_id: int = Depends(current_user_id), repo: LedgerRepository = Depends(get_repository)):
    return repo.list_asset_liability(user_id)


@router.post("/asset-liability", status_code=201, summary="新增资产/负债")
def create_asset_liability(
    payload: AssetLiabilityIn,
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    item_id = repo.create_asset_liability(
        user_id,
        item_name=payload.item_name,
        item_type=payload.item_type,
        amount=payload.amount,
        acquire_date=payload.acquire_date,
        remark=payload.remark,
    )
    return {"success": True, "item_id": item_id}


@router.put("/asset-liability/{item_id}", summary="修改资产/负债")
def update_asset_liability(
    item_id: int,
    payload: AssetLiabilityIn,
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    repo.update_asset_liability(
        user_id,
        item_id,
        item_name=payload.item_name,
        item_type=payload.item_type,
        amount=payload.amount,
        acquire_date=payload.acquire_date,
        remark=payload.remark,
    )
    return {"success": True}


@router.delete("/asset-liability/{item_id}", summary="删除资产/负债")
def delete_asset_liability(
    item_id: int,
    user_id: int = Depends(current_user_id),
    repo: LedgerRepository = Depends(get_repository),
):
    repo.delete_asset_liability(user_id, item_id)
    return {"success": True}


# --------------------------------------------------------------------------- #
# 维护
# --------------------------------------------------------------------------- #
@router.delete("/clear-data", summary="清空当前用户业务数据")
@router.delete("/clear_data", include_in_schema=False)
def clear_data(user_id: int = Depends(current_user_id), repo: LedgerRepository = Depends(get_repository)):
    return {"success": True, "cleared": repo.clear_data(user_id)}


@router.get("/stats", summary="数据统计（用于首页概览卡片）")
def stats(user_id: int = Depends(current_user_id), repo: LedgerRepository = Depends(get_repository)):
    return repo.stats(user_id)


@router.get("/schema", summary="数据库结构自检（表/视图/触发器）")
def schema(database: Any = Depends(get_database)):
    return database.describe()
