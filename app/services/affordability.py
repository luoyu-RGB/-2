"""消费可负担性分析引擎。

这是"不依赖大模型也能用"的核心能力：全部结论都由账本真实数据 + 确定性公式得出，
AI 只负责把结论讲清楚。判断口径：

1. 到手总成本 = 标价 + 税费 + 运费 + 必要配件 + 分期费用 + 持续成本 − 可确认优惠
2. 月必要支出 = 最近 N 个完整月份中"必要类目"支出的月均值
3. 应急金目标 = 月必要支出 × 用户设定的月数（默认 6 个月）
4. 购买后应急金月数 = (可动用现金 − 到手总成本) ÷ 月必要支出
5. 需攒钱月数 = (到手总成本 − 可自由动用的现金) ÷ 月结余，向上取整
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

from ..db.base import quantize_money
from ..db.repository import LedgerRepository, previous_months
from .calculator import format_money

# 认定为"必要支出"的类目（可通过环境变量覆盖，见 config.necessary_categories）
DEFAULT_NECESSARY_CATEGORIES: tuple[str, ...] = (
    "居住",
    "餐饮",
    "交通",
    "通讯网络",
    "医疗健康",
)

# 可动用现金只统计流动性账户，投资账户不计入
LIQUID_ACCOUNT_TYPES: tuple[str, ...] = ("现金", "银行卡", "电子钱包")

VERDICT_BUY = "可以购买"
VERDICT_WAIT = "建议暂缓"
VERDICT_NO = "不建议购买"
VERDICT_UNKNOWN = "信息不足，无法判断"


@dataclass
class AffordabilityInput:
    item_name: str = "目标商品"
    price: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")
    shipping: Decimal = Decimal("0")
    accessories: Decimal = Decimal("0")
    subscription: Decimal = Decimal("0")
    installment_fee: Decimal = Decimal("0")
    discount: Decimal = Decimal("0")
    emergency_months_target: int = 6
    months: int = 3


@dataclass
class AffordabilityResult:
    item_name: str
    verdict: str
    total_cost: Decimal
    available_cash: Decimal
    liquid_balance: Decimal
    monthly_necessary_expense: Decimal
    monthly_surplus: Decimal
    emergency_target: Decimal
    emergency_months_after: Decimal
    emergency_months_before: Decimal
    months_to_save: int
    free_cash_above_emergency: Decimal
    reasons: list[str] = field(default_factory=list)
    calculation_steps: list[str] = field(default_factory=list)
    data_window: dict = field(default_factory=dict)
    cost_breakdown: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "item_name": self.item_name,
            "verdict": self.verdict,
            "total_cost": float(self.total_cost),
            "total_cost_text": format_money(self.total_cost),
            "available_cash": float(self.available_cash),
            "liquid_balance": float(self.liquid_balance),
            "monthly_necessary_expense": float(self.monthly_necessary_expense),
            "monthly_surplus": float(self.monthly_surplus),
            "emergency_target": float(self.emergency_target),
            "emergency_months_before": float(self.emergency_months_before),
            "emergency_months_after": float(self.emergency_months_after),
            "months_to_save": self.months_to_save,
            "free_cash_above_emergency": float(self.free_cash_above_emergency),
            "reasons": self.reasons,
            "calculation_steps": self.calculation_steps,
            "data_window": self.data_window,
            "cost_breakdown": self.cost_breakdown,
        }

    def to_prompt_context(self) -> str:
        """给大模型的事实清单：只给数字与结论，禁止它自己重算。"""
        lines = [
            f"【可负担性分析（由系统计算，禁止改写数字）】",
            f"商品：{self.item_name}",
            f"到手总成本：{format_money(self.total_cost)} 元",
            f"可动用现金：{format_money(self.liquid_balance)} 元",
            f"月必要支出（近 {self.data_window.get('months', 0)} 个月均值）：{format_money(self.monthly_necessary_expense)} 元",
            f"月结余：{format_money(self.monthly_surplus)} 元",
            f"应急金目标：{format_money(self.emergency_target)} 元",
            f"购买后应急金：{self.emergency_months_after:.2f} 个月（购买前 {self.emergency_months_before:.2f} 个月）",
            f"需攒钱：{self.months_to_save} 个月" if self.months_to_save else "无需额外攒钱",
            f"系统结论：{self.verdict}",
        ]
        if self.reasons:
            lines.append("判断依据：" + "；".join(self.reasons))
        return "\n".join(lines)


class AffordabilityService:
    def __init__(self, repository: LedgerRepository, necessary_categories: tuple[str, ...] | None = None) -> None:
        self.repository = repository
        self.necessary_categories = necessary_categories or DEFAULT_NECESSARY_CATEGORIES

    # ------------------------------------------------------------------ #
    def analyze(self, user_id: int, payload: AffordabilityInput) -> AffordabilityResult:
        price = quantize_money(payload.price)
        tax = quantize_money(payload.tax)
        shipping = quantize_money(payload.shipping)
        accessories = quantize_money(payload.accessories)
        subscription = quantize_money(payload.subscription)
        installment_fee = quantize_money(payload.installment_fee)
        discount = quantize_money(payload.discount)

        total_cost = quantize_money(
            price + tax + shipping + accessories + subscription + installment_fee - discount
        )
        if total_cost < 0:
            total_cost = Decimal("0.00")

        accounts = self.repository.list_accounts(user_id)
        liquid_balance = quantize_money(
            sum((account["balance"] for account in accounts if account["account_type"] in LIQUID_ACCOUNT_TYPES), Decimal("0"))
        )

        expense_by_category = self.repository.expense_by_category(user_id, months=payload.months)
        necessary_total = quantize_money(
            sum(
                (amount for name, amount in expense_by_category.items() if name in self.necessary_categories),
                Decimal("0"),
            )
        )
        # 月必要支出用最近若干个"完整月份"的平均值（不含当月，避免半个月数据拉低口径）
        month_labels = previous_months(payload.months)
        month_count = max(1, len(month_labels))
        monthly_necessary = quantize_money(necessary_total / month_count)

        income = self.repository.income_stats(user_id, months=payload.months)
        monthly_income = quantize_money(income.get("monthly_average") or 0)
        monthly_surplus = quantize_money(monthly_income - monthly_necessary)

        emergency_target = quantize_money(monthly_necessary * Decimal(payload.emergency_months_target))
        cash_after = quantize_money(liquid_balance - total_cost)
        free_cash = quantize_money(liquid_balance - emergency_target)

        if monthly_necessary > 0:
            emergency_before = float(liquid_balance / monthly_necessary)
            emergency_after = float(cash_after / monthly_necessary)
        else:
            emergency_before = float("inf")
            emergency_after = float("inf")

        shortfall = total_cost - max(Decimal("0"), free_cash)
        if shortfall <= 0:
            months_to_save = 0
        elif monthly_surplus <= 0:
            months_to_save = -1  # 现金流为负，无法通过储蓄达成
        else:
            months_to_save = int(math.ceil(float(shortfall / monthly_surplus)))

        reasons: list[str] = []
        steps: list[str] = [
            f"到手总成本 = {format_money(price)} + {format_money(tax)} + {format_money(shipping)} "
            f"+ {format_money(accessories)} + {format_money(subscription)} + {format_money(installment_fee)} "
            f"− {format_money(discount)} = {format_money(total_cost)} 元",
            f"可动用现金 = 流动性账户余额合计 = {format_money(liquid_balance)} 元",
            f"月必要支出 = 近 {month_count} 个月「{'、'.join(self.necessary_categories)}」支出均值 = {format_money(monthly_necessary)} 元",
            f"应急金目标 = {format_money(monthly_necessary)} × {payload.emergency_months_target} = {format_money(emergency_target)} 元",
            f"月结余 = 月均收入 {format_money(monthly_income)} − 月必要支出 {format_money(monthly_necessary)} = {format_money(monthly_surplus)} 元",
        ]

        if cash_after < 0:
            verdict = VERDICT_NO
            reasons.append("当前可动用现金不足以全款支付，需要动用其他资产或分期")
        elif monthly_necessary > 0 and emergency_after < payload.emergency_months_target:
            verdict = VERDICT_WAIT
            reasons.append(
                f"购买后应急金仅剩 {emergency_after:.2f} 个月，低于目标 {payload.emergency_months_target} 个月"
            )
        elif monthly_surplus <= 0:
            verdict = VERDICT_NO
            reasons.append("月结余不为正，新增支出会加重现金流压力")
        else:
            verdict = VERDICT_BUY
            reasons.append(
                f"购买后应急金仍有 {emergency_after:.2f} 个月，且月结余为正 {format_money(monthly_surplus)} 元"
            )

        if months_to_save == -1:
            reasons.append("月结余不足以支撑储蓄计划，建议先扩大结余或降低预算")
            steps.append("需攒钱月数：月结余 ≤ 0，无法通过储蓄达成")
        elif months_to_save > 0:
            steps.append(
                f"需攒钱月数 = (到手总成本 {format_money(total_cost)} − 应急金以上可用现金 "
                f"{format_money(max(Decimal('0'), free_cash))}) ÷ 月结余 {format_money(monthly_surplus)} "
                f"≈ {months_to_save} 个月（向上取整）"
            )
        else:
            steps.append("应急金以上的可用现金已经覆盖到手总成本，无需额外攒钱")

        budget_note = self._budget_note(user_id)
        if budget_note:
            reasons.append(budget_note)

        return AffordabilityResult(
            item_name=payload.item_name,
            verdict=verdict,
            total_cost=total_cost,
            available_cash=cash_after,
            liquid_balance=liquid_balance,
            monthly_necessary_expense=monthly_necessary,
            monthly_surplus=monthly_surplus,
            emergency_target=emergency_target,
            emergency_months_after=Decimal(str(round(emergency_after, 2))) if emergency_after != float("inf") else Decimal("9999"),
            emergency_months_before=Decimal(str(round(emergency_before, 2))) if emergency_before != float("inf") else Decimal("9999"),
            months_to_save=months_to_save,
            free_cash_above_emergency=free_cash,
            reasons=reasons,
            calculation_steps=steps,
            data_window={
                "months": month_count,
                "labels": month_labels,
                "necessary_categories": list(self.necessary_categories),
            },
            cost_breakdown={
                "price": float(price),
                "tax": float(tax),
                "shipping": float(shipping),
                "accessories": float(accessories),
                "subscription": float(subscription),
                "installment_fee": float(installment_fee),
                "discount": float(discount),
            },
        )

    # ------------------------------------------------------------------ #
    def _budget_note(self, user_id: int) -> str:
        """把当月预算执行情况并入判断依据。"""
        from ..db.base import current_month

        try:
            rows = self.repository.budget_status(user_id, current_month())
        except Exception:  # pragma: no cover - 视图缺失时不影响主流程
            return ""
        overspent = [row for row in rows if str(row.get("status", "")).startswith("超支")]
        if overspent:
            names = "、".join(str(row["category_name"]) for row in overspent[:3])
            return f"本月已有超支类别（{names}），应先处理预算缺口"
        return ""


__all__ = [
    "AffordabilityInput",
    "AffordabilityResult",
    "AffordabilityService",
    "DEFAULT_NECESSARY_CATEGORIES",
    "LIQUID_ACCOUNT_TYPES",
    "VERDICT_BUY",
    "VERDICT_NO",
    "VERDICT_UNKNOWN",
    "VERDICT_WAIT",
]
