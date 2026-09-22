"""Pydantic 请求/响应模型。

路由层只做参数解析与错误映射，业务校验放在服务层，
因此这里的约束以"类型与基本范围"为主。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TransType = Literal["收入", "支出"]
AccountType = Literal["现金", "银行卡", "电子钱包", "投资账户"]
ItemType = Literal["资产", "负债"]


class AccountCreate(BaseModel):
    account_name: str = Field(min_length=1, max_length=50)
    account_type: AccountType = "银行卡"
    balance: Decimal = Field(default=Decimal("0"), ge=0, description="期初余额")


class AccountUpdate(BaseModel):
    """账户更新不允许改余额：余额只能由交易与触发器驱动。"""

    account_name: str = Field(min_length=1, max_length=50)
    account_type: AccountType


class TransactionCreate(BaseModel):
    account_id: int
    category_id: int
    amount: Decimal = Field(gt=0)
    trans_type: TransType
    trans_date: date = Field(default_factory=date.today)
    remark: str = Field(default="", max_length=200)

    @field_validator("trans_date")
    @classmethod
    def _iso(cls, value: date) -> str:  # type: ignore[override]
        return value.isoformat()


class TransactionUpdate(TransactionCreate):
    pass


class TransferCreate(BaseModel):
    from_account: int
    to_account: int
    amount: Decimal = Field(gt=0)
    trans_date: date = Field(default_factory=date.today)
    remark: str = Field(default="", max_length=200)

    @field_validator("trans_date")
    @classmethod
    def _iso(cls, value: date) -> str:  # type: ignore[override]
        return value.isoformat()


class BudgetSet(BaseModel):
    category_id: int
    year_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    amount: Decimal = Field(gt=0)


class AssetLiabilityIn(BaseModel):
    item_name: str = Field(min_length=1, max_length=100)
    item_type: ItemType
    amount: Decimal = Field(gt=0)
    acquire_date: date = Field(default_factory=date.today)
    remark: str = Field(default="", max_length=200)

    @field_validator("acquire_date")
    @classmethod
    def _iso(cls, value: date) -> str:  # type: ignore[override]
        return value.isoformat()


class AffordabilityRequest(BaseModel):
    """可负担性分析入参：商品到手成本 + 用户约束。"""

    item_name: str = Field(default="目标商品", max_length=100)
    price: Decimal = Field(gt=0, description="商品标价")
    tax: Decimal = Field(default=Decimal("0"), ge=0, description="税费")
    shipping: Decimal = Field(default=Decimal("0"), ge=0, description="运费")
    accessories: Decimal = Field(default=Decimal("0"), ge=0, description="必要配件")
    subscription: Decimal = Field(default=Decimal("0"), ge=0, description="订阅/维护等持续成本")
    installment_fee: Decimal = Field(default=Decimal("0"), ge=0, description="分期利息与手续费")
    discount: Decimal = Field(default=Decimal("0"), ge=0, description="可确认优惠")
    emergency_months_target: int = Field(default=6, ge=0, le=24)
    months: int = Field(default=3, ge=1, le=12, description="统计必要支出所用的完整月份数")


class CalculatorRequest(BaseModel):
    expression: str = Field(min_length=1, max_length=200)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    user_id: int | None = None
    confirm: bool = Field(
        default=False,
        description="AI 提出写操作后，用户确认时才真正落库",
    )


class KnowledgeIngestRequest(BaseModel):
    title: str = Field(default="未命名资料", max_length=100)
    content: str = Field(min_length=1, max_length=20000)


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    success: bool = True
    message: str = ""
    data: Any = None
