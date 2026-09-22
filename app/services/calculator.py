"""安全计算器：只解释数字与四则运算，绝不执行任意 Python 代码。

对照参考项目（Personal-Finance-Advisor-Agent）的 `financial_calculator`：
同样使用 ``ast`` 白名单 + ``Decimal``，并额外提供：
* 计算步骤回放（``steps``），便于把口径写进回答里；
* 百分比语法糖（``5%`` → ``0.05``），财务表达里最常见的一种写法。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from decimal import Decimal, DivisionByZero, InvalidOperation, ROUND_HALF_UP

TWO_PLACES = Decimal("0.01")
MAX_EXPRESSION_LENGTH = 200

ALLOWED_OPERATORS = {
    ast.Add: "加法",
    ast.Sub: "减法",
    ast.Mult: "乘法",
    ast.Div: "除法",
}


class CalculatorError(ValueError):
    """表达式非法或计算失败。"""


@dataclass
class CalculationResult:
    expression: str
    normalized: str
    value: Decimal
    steps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "expression": self.expression,
            "normalized": self.normalized,
            "value": float(self.value),
            "value_text": format_money(self.value),
            "steps": self.steps,
        }


def format_money(value: Decimal) -> str:
    return f"{value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP):,.2f}"


def normalize_expression(expression: str) -> str:
    """去掉千分位、全角符号，并把 ``5%`` 改写成 ``(5/100)``。"""
    text = expression.strip()
    if not text:
        raise CalculatorError("表达式不能为空")
    if len(text) > MAX_EXPRESSION_LENGTH:
        raise CalculatorError(f"表达式过长（上限 {MAX_EXPRESSION_LENGTH} 字符）")

    replacements = {
        "，": ",",
        "（": "(",
        "）": ")",
        "＋": "+",
        "－": "-",
        "×": "*",
        "÷": "/",
        "％": "%",
        "。": ".",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = text.replace(",", "").replace(" ", "")

    if "%" in text:
        # 允许 "5%" 与 "12000*5%"：把百分号绑定到它前面的数字
        result: list[str] = []
        index = 0
        while index < len(text):
            char = text[index]
            if char == "%":
                number_start = len(result)
                while number_start > 0 and (result[number_start - 1].isdigit() or result[number_start - 1] == "."):
                    number_start -= 1
                if number_start == len(result):
                    raise CalculatorError("百分号前必须是数字")
                number = "".join(result[number_start:])
                del result[number_start:]
                result.append(f"({number}/100)")
            else:
                result.append(char)
            index += 1
        text = "".join(result)

    return text


def _evaluate(node: ast.AST, steps: list[str]) -> Decimal:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, steps)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculatorError("只允许数字、括号与 + - * / 四则运算")
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp):
        operand = _evaluate(node.operand, steps)
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise CalculatorError("只允许一元正负号")
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in ALLOWED_OPERATORS:
            raise CalculatorError("只允许 + - * / 四则运算")
        left = _evaluate(node.left, steps)
        right = _evaluate(node.right, steps)
        if op_type is ast.Add:
            value = left + right
        elif op_type is ast.Sub:
            value = left - right
        elif op_type is ast.Mult:
            value = left * right
        else:
            if right == 0:
                raise CalculatorError("除数不能为 0")
            value = left / right
        steps.append(f"{format_money(left)} {_symbol(op_type)} {format_money(right)} = {format_money(value)}")
        return value
    raise CalculatorError("表达式包含不允许的语法（仅支持数字、括号与四则运算）")


def _symbol(op_type: type) -> str:
    return {ast.Add: "+", ast.Sub: "-", ast.Mult: "×", ast.Div: "÷"}[op_type]


def calculate(expression: str) -> CalculationResult:
    normalized = normalize_expression(expression)
    try:
        parsed = ast.parse(normalized, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"表达式语法错误：{exc.msg}") from exc

    steps: list[str] = []
    try:
        value = _evaluate(parsed, steps)
    except (InvalidOperation, DivisionByZero) as exc:
        raise CalculatorError(f"计算失败：{exc}") from exc
    except ZeroDivisionError as exc:  # pragma: no cover - 已被上面的显式判断拦住
        raise CalculatorError("除数不能为 0") from exc

    return CalculationResult(
        expression=expression,
        normalized=normalized,
        value=value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
        steps=steps,
    )


__all__ = [
    "CalculationResult",
    "CalculatorError",
    "calculate",
    "format_money",
    "normalize_expression",
]
