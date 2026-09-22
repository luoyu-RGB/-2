"""服务层：把确定性能力（计算、可负担性、报表整形）从路由与 AI 中抽出来。"""

from __future__ import annotations

from .affordability import (
    AffordabilityInput,
    AffordabilityResult,
    AffordabilityService,
    DEFAULT_NECESSARY_CATEGORIES,
    LIQUID_ACCOUNT_TYPES,
    VERDICT_BUY,
    VERDICT_NO,
    VERDICT_UNKNOWN,
    VERDICT_WAIT,
)
from .calculator import CalculationResult, CalculatorError, calculate, format_money

__all__ = [
    "AffordabilityInput",
    "AffordabilityResult",
    "AffordabilityService",
    "CalculationResult",
    "CalculatorError",
    "DEFAULT_NECESSARY_CATEGORIES",
    "LIQUID_ACCOUNT_TYPES",
    "VERDICT_BUY",
    "VERDICT_NO",
    "VERDICT_UNKNOWN",
    "VERDICT_WAIT",
    "calculate",
    "format_money",
]
