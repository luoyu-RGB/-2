"""对模型暴露的工具集。

约定：
* 查询/计算类工具是**只读**的，直接返回结果；
* 写入类工具（``add_transaction`` / ``transfer`` / ``set_budget``）**只生成预览**，
  真正落库要等用户在下一轮点"确认"。这既防止模型误操作，也让每次写入都有审计。
* 工具的 SQL 全部走仓储层与存储过程，模型完全接触不到数据库。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from ..config import Settings
from ..db.base import (
    DatabaseError,
    InsufficientBalanceError,
    NotFoundError,
    current_month,
    month_bounds,
    quantize_money,
)
from ..db.repository import LedgerRepository
from ..services.affordability import AffordabilityInput, AffordabilityService
from ..services.calculator import CalculatorError, calculate
from .knowledge import KnowledgeBase

WRITE_TOOLS = {"add_transaction", "transfer", "set_budget"}


@dataclass
class ToolContext:
    user_id: int
    repository: LedgerRepository
    affordability: AffordabilityService
    knowledge: KnowledgeBase
    settings: Settings


def _money(value: Any) -> Decimal:
    try:
        return quantize_money(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"金额格式不正确：{value!r}") from exc


def _resolve_account(context: ToolContext, name: str | None) -> dict[str, Any]:
    accounts = context.repository.list_accounts(context.user_id)
    if not accounts:
        raise NotFoundError("还没有任何账户，请先创建账户")
    if not name:
        return accounts[0]
    text = str(name).strip()
    for account in accounts:
        if account["account_name"] == text:
            return account
    for account in accounts:
        if text in account["account_name"] or account["account_name"] in text:
            return account
    raise NotFoundError(f"找不到账户「{name}」，现有账户：{'、'.join(a['account_name'] for a in accounts)}")


def _resolve_category(context: ToolContext, name: str | None, category_type: str, *, create: bool = True) -> dict[str, Any]:
    categories = context.repository.list_categories(context.user_id, category_type)
    text = (name or "").strip() or ("其他支出" if category_type == "支出" else "其他收入")
    for category in categories:
        if category["category_name"] == text:
            return category
    for category in categories:
        if text in category["category_name"] or category["category_name"] in text:
            return category
    if not create:
        raise NotFoundError(f"找不到{category_type}类别「{name}」")
    # 让 AI 能记下"宠物""健身"这类新类目；类别类型由交易类型决定
    category_id = context.repository.ensure_category(context.user_id, text, category_type)
    return {"category_id": category_id, "category_name": text, "category_type": category_type, "parent_id": None}


# --------------------------------------------------------------------------- #
# 只读工具
# --------------------------------------------------------------------------- #
def tool_current_time(context: ToolContext, **_: Any) -> dict[str, Any]:
    now = datetime.now().astimezone()
    return {"datetime": now.isoformat(timespec="seconds"), "date": now.date().isoformat(), "month": current_month()}


def tool_query_overview(context: ToolContext, month: str | None = None, **_: Any) -> dict[str, Any]:
    return context.repository.overview(context.user_id, month)


def tool_query_accounts(context: ToolContext, **_: Any) -> dict[str, Any]:
    return {"accounts": context.repository.list_accounts(context.user_id)}


def tool_query_monthly_report(context: ToolContext, month: str | None = None, **_: Any) -> dict[str, Any]:
    return context.repository.monthly_report(context.user_id, month or current_month())


def tool_query_category_spending(context: ToolContext, month: str | None = None, **_: Any) -> dict[str, Any]:
    label = month or current_month()
    start, end = month_bounds(label)
    return {
        "month": label,
        "items": context.repository.category_spending(context.user_id, start, end),
    }


def tool_query_budget_status(context: ToolContext, month: str | None = None, **_: Any) -> dict[str, Any]:
    return {"month": month or current_month(), "items": context.repository.budget_status(context.user_id, month or current_month())}


def tool_list_recent_transactions(
    context: ToolContext, limit: int = 10, month: str | None = None, **_: Any
) -> dict[str, Any]:
    rows = context.repository.list_transactions(context.user_id, month=month, limit=int(limit or 10))
    return {"count": len(rows), "items": rows}


def tool_calculate(context: ToolContext, expression: str, **_: Any) -> dict[str, Any]:
    try:
        return calculate(expression).as_dict()
    except CalculatorError as exc:
        return {"error": str(exc)}


def tool_analyze_affordability(context: ToolContext, **arguments: Any) -> dict[str, Any]:
    payload = AffordabilityInput(
        item_name=str(arguments.get("item_name") or "目标商品")[:100],
        price=_money(arguments.get("price") or 0),
        tax=_money(arguments.get("tax") or 0),
        shipping=_money(arguments.get("shipping") or 0),
        accessories=_money(arguments.get("accessories") or 0),
        subscription=_money(arguments.get("subscription") or 0),
        installment_fee=_money(arguments.get("installment_fee") or 0),
        discount=_money(arguments.get("discount") or 0),
        emergency_months_target=int(arguments.get("emergency_months_target") or 6),
        months=int(arguments.get("months") or 3),
    )
    if payload.price <= 0:
        return {"error": "需要提供商品价格（price）才能分析"}
    return context.affordability.analyze(context.user_id, payload).as_dict()


def tool_search_knowledge(context: ToolContext, question: str, top_k: int = 5, **_: Any) -> dict[str, Any]:
    chunks = context.knowledge.search(context.user_id, question, top_k=int(top_k or 5))
    return {
        "count": len(chunks),
        "mode": chunks[0].mode if chunks else "none",
        "items": [chunk.as_dict() for chunk in chunks],
    }


# --------------------------------------------------------------------------- #
# 写入工具（两段式：先预览）
# --------------------------------------------------------------------------- #
def tool_add_transaction(context: ToolContext, **arguments: Any) -> dict[str, Any]:
    trans_type = str(arguments.get("trans_type") or "支出").strip()
    if trans_type not in ("收入", "支出"):
        return {"error": "交易类型必须是收入或支出"}
    amount = _money(arguments.get("amount"))
    if amount <= 0:
        return {"error": "金额必须大于 0"}
    account = _resolve_account(context, arguments.get("account"))
    category = _resolve_category(context, arguments.get("category"), trans_type)
    trans_date = str(arguments.get("trans_date") or date.today().isoformat())[:10]
    remark = str(arguments.get("remark") or "")[:200]
    payload = {
        "account_id": int(account["account_id"]),
        "account_name": account["account_name"],
        "category_id": int(category["category_id"]),
        "category_name": category["category_name"],
        "amount": str(amount),
        "trans_type": trans_type,
        "trans_date": trans_date,
        "remark": remark,
    }
    return {
        "status": "preview",
        "action": "add_transaction",
        "payload": payload,
        "summary": f"{trans_date} 在「{account['account_name']}」记一笔{trans_type} {amount} 元（{category['category_name']}）",
        "confirm_required": True,
    }


def tool_transfer(context: ToolContext, **arguments: Any) -> dict[str, Any]:
    amount = _money(arguments.get("amount"))
    if amount <= 0:
        return {"error": "转账金额必须大于 0"}
    source = _resolve_account(context, arguments.get("from_account"))
    target = _resolve_account(context, arguments.get("to_account"))
    if source["account_id"] == target["account_id"]:
        return {"error": "转出与转入账户不能相同"}
    if quantize_money(source["balance"]) < amount:
        return {
            "error": f"账户「{source['account_name']}」余额 {source['balance']} 元，不足以转账 {amount} 元"
        }
    payload = {
        "from_account": int(source["account_id"]),
        "from_account_name": source["account_name"],
        "to_account": int(target["account_id"]),
        "to_account_name": target["account_name"],
        "amount": str(amount),
        "trans_date": str(arguments.get("trans_date") or date.today().isoformat())[:10],
        "remark": str(arguments.get("remark") or "")[:200],
    }
    return {
        "status": "preview",
        "action": "transfer",
        "payload": payload,
        "summary": f"从「{source['account_name']}」向「{target['account_name']}」转账 {amount} 元",
        "confirm_required": True,
    }


def tool_set_budget(context: ToolContext, **arguments: Any) -> dict[str, Any]:
    amount = _money(arguments.get("amount"))
    if amount <= 0:
        return {"error": "预算金额必须大于 0"}
    month = str(arguments.get("year_month") or current_month())[:7]
    try:
        month_bounds(month)
    except ValueError as exc:
        return {"error": str(exc)}
    category = _resolve_category(context, arguments.get("category"), "支出")
    payload = {
        "category_id": int(category["category_id"]),
        "category_name": category["category_name"],
        "year_month": month,
        "amount": str(amount),
    }
    return {
        "status": "preview",
        "action": "set_budget",
        "payload": payload,
        "summary": f"把 {month} 的「{category['category_name']}」预算设为 {amount} 元",
        "confirm_required": True,
    }


# --------------------------------------------------------------------------- #
# 注册表与 schema
# --------------------------------------------------------------------------- #
REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {
    "current_time": tool_current_time,
    "query_overview": tool_query_overview,
    "query_accounts": tool_query_accounts,
    "query_monthly_report": tool_query_monthly_report,
    "query_category_spending": tool_query_category_spending,
    "query_budget_status": tool_query_budget_status,
    "list_recent_transactions": tool_list_recent_transactions,
    "calculate": tool_calculate,
    "analyze_affordability": tool_analyze_affordability,
    "search_knowledge": tool_search_knowledge,
    "add_transaction": tool_add_transaction,
    "transfer": tool_transfer,
    "set_budget": tool_set_budget,
}

_MONEY_SCHEMA = {"type": "number", "description": "金额（元），只填数字"}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "current_time",
            "description": "获取当前日期时间。涉及今天、本月、最近、有效期时必须先调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_overview",
            "description": "查询本月收入、支出、结余、净资产、日均支出等总览数据。",
            "parameters": {
                "type": "object",
                "properties": {"month": {"type": "string", "description": "YYYY-MM，缺省为本月"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_accounts",
            "description": "查询所有账户及其余额。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_monthly_report",
            "description": "查询指定月份的收支汇总（走数据库函数 fn_monthly_report）。",
            "parameters": {"type": "object", "properties": {"month": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_category_spending",
            "description": "查询指定月份各类别支出金额与占比（走数据库函数 fn_category_spending）。",
            "parameters": {"type": "object", "properties": {"month": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_budget_status",
            "description": "查询指定月份各预算类别的预算额、实际支出、完成率与状态（走视图 v_budget_status）。",
            "parameters": {"type": "object", "properties": {"month": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_recent_transactions",
            "description": "列出最近的交易明细。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "条数，默认 10"},
                    "month": {"type": "string", "description": "YYYY-MM，可选"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "精确四则运算。expression 只能含数字、括号与 + - * /，百分比写成 5% 形式。",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_affordability",
            "description": (
                "判断某笔消费是否负担得起。基于用户真实账本计算到手总成本、应急金月数、"
                "需攒钱月数并给出结论。任何'能不能买/分期合适吗'的问题都必须调用它。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item_name": {"type": "string"},
                    "price": _MONEY_SCHEMA,
                    "tax": _MONEY_SCHEMA,
                    "shipping": _MONEY_SCHEMA,
                    "accessories": _MONEY_SCHEMA,
                    "subscription": _MONEY_SCHEMA,
                    "installment_fee": _MONEY_SCHEMA,
                    "discount": _MONEY_SCHEMA,
                    "emergency_months_target": {"type": "integer", "description": "应急金目标月数，默认 6"},
                },
                "required": ["price"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "在用户上传的理财资料、贷款条款、产品说明里检索相关内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "top_k": {"type": "integer"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_transaction",
            "description": "记一笔收入或支出。只会生成待确认预览，用户确认后才落库。",
            "parameters": {
                "type": "object",
                "properties": {
                    "trans_type": {"type": "string", "enum": ["收入", "支出"]},
                    "amount": _MONEY_SCHEMA,
                    "category": {"type": "string", "description": "类别名，如 餐饮/交通/工资收入"},
                    "account": {"type": "string", "description": "账户名，缺省用第一个账户"},
                    "trans_date": {"type": "string", "description": "YYYY-MM-DD，缺省今天"},
                    "remark": {"type": "string"},
                },
                "required": ["trans_type", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transfer",
            "description": "账户间转账（会走存储过程/双记录事务）。只生成待确认预览。",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_account": {"type": "string"},
                    "to_account": {"type": "string"},
                    "amount": _MONEY_SCHEMA,
                    "trans_date": {"type": "string"},
                    "remark": {"type": "string"},
                },
                "required": ["from_account", "to_account", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_budget",
            "description": "设置某月某类别预算。只生成待确认预览。",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "year_month": {"type": "string", "description": "YYYY-MM，缺省本月"},
                    "amount": _MONEY_SCHEMA,
                },
                "required": ["category", "amount"],
            },
        },
    },
]


def execute_tool(name: str, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    """执行工具并把异常收敛成 ``{"error": ...}``，保证对话不会因单个工具失败而中断。"""
    handler = REGISTRY.get(name)
    if handler is None:
        return {"error": f"未知工具：{name}"}
    try:
        return handler(context, **arguments)
    except (DatabaseError, ValueError, NotFoundError, InsufficientBalanceError) as exc:
        return {"error": str(exc)}
    except TypeError as exc:
        return {"error": f"工具参数不正确：{exc}"}


def apply_write_action(context: ToolContext, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """把已确认的预览真正落库。余额变更全部交给触发器/存储过程。"""
    if action == "add_transaction":
        transaction_id = context.repository.create_transaction(
            context.user_id,
            account_id=int(payload["account_id"]),
            category_id=int(payload["category_id"]),
            amount=Decimal(str(payload["amount"])),
            trans_type=payload["trans_type"],
            trans_date=payload["trans_date"],
            remark=payload.get("remark", ""),
        )
        return {"transaction_id": transaction_id}

    if action == "transfer":
        result = context.repository.transfer(
            context.user_id,
            from_account=int(payload["from_account"]),
            to_account=int(payload["to_account"]),
            amount=Decimal(str(payload["amount"])),
            trans_date=payload["trans_date"],
            remark=payload.get("remark", ""),
        )
        return result

    if action == "set_budget":
        context.repository.upsert_budget(
            context.user_id,
            int(payload["category_id"]),
            payload["year_month"],
            Decimal(str(payload["amount"])),
        )
        return {"budget": payload}

    raise ValueError(f"不支持的动作：{action}")


def payload_to_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


__all__ = [
    "REGISTRY",
    "TOOL_SCHEMAS",
    "WRITE_TOOLS",
    "ToolContext",
    "apply_write_action",
    "execute_tool",
    "payload_to_json",
]
