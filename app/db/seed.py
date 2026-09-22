"""演示数据（幂等）。

与原 ``docs/sql/03_insert_test_data.sql`` 的两点关键差异：

1. **数据相对当前月份生成**（最近 4 个月），这样"本月概览 / 预算执行"页面
   任何时候打开都有内容，而不是固定在 2026-03 ~ 2026-06。
2. **不再手工重算余额**。原脚本结尾的
   ``UPDATE account SET balance = (SELECT SUM(...))`` 会让"现金""微信钱包"
   这类只有支出没有收入的账户余额变成负数，直接违反 ``balance >= 0`` 约束而报错。
   现在账户带期初余额，交易按时间顺序插入，余额由触发器维护。
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from typing import Any

from .base import DatabaseAdapter, Tx

# (账户名, 账户类型, 期初余额)
ACCOUNTS: tuple[tuple[str, str, str], ...] = (
    ("现金", "现金", "2500.00"),
    ("工商银行储蓄卡", "银行卡", "50000.00"),
    ("支付宝", "电子钱包", "5000.00"),
    ("微信钱包", "电子钱包", "1000.00"),
    ("股票账户", "投资账户", "100000.00"),
)

INCOME_CATEGORIES: tuple[str, ...] = ("工资收入", "投资收益", "其他收入")

EXPENSE_PARENTS: tuple[str, ...] = (
    "餐饮",
    "交通",
    "居住",
    "购物消费",
    "休闲娱乐",
    "通讯网络",
    "医疗健康",
    "教育培训",
    "其他支出",
)

# 子类别 → 父类别
EXPENSE_CHILDREN: tuple[tuple[str, str], ...] = (
    ("早餐", "餐饮"),
    ("午餐", "餐饮"),
    ("晚餐", "餐饮"),
    ("零食饮料", "餐饮"),
    ("公共交通", "交通"),
    ("加油充电", "交通"),
    ("房租", "居住"),
    ("水电燃气", "居住"),
)

# (月份偏移, 日, 账户名, 类别名, 金额, 类型, 备注) ；偏移 0 = 本月
TRANSACTIONS: tuple[tuple[int, int, str, str, str, str, str], ...] = (
    # ---------------- 3 个月前 ----------------
    (3, 5, "工商银行储蓄卡", "工资收入", "15000.00", "收入", "工资"),
    (3, 1, "现金", "早餐", "8.00", "支出", "早餐：豆浆油条"),
    (3, 1, "支付宝", "午餐", "25.00", "支出", "午餐：外卖盖饭"),
    (3, 2, "现金", "晚餐", "35.00", "支出", "晚餐：和朋友聚餐"),
    (3, 3, "支付宝", "零食饮料", "12.00", "支出", "咖啡"),
    (3, 3, "支付宝", "公共交通", "4.00", "支出", "地铁通勤"),
    (3, 8, "支付宝", "公共交通", "4.00", "支出", "公交车"),
    (3, 1, "工商银行储蓄卡", "房租", "3500.00", "支出", "房租"),
    (3, 10, "工商银行储蓄卡", "水电燃气", "280.00", "支出", "水电燃气"),
    (3, 15, "现金", "加油充电", "200.00", "支出", "加油"),
    (3, 8, "支付宝", "购物消费", "158.00", "支出", "淘宝：T恤"),
    (3, 20, "工商银行储蓄卡", "购物消费", "299.00", "支出", "京东：蓝牙耳机"),
    # ---------------- 2 个月前 ----------------
    (2, 3, "工商银行储蓄卡", "工资收入", "15000.00", "收入", "工资"),
    (2, 2, "现金", "早餐", "7.00", "支出", "早餐：鸡蛋灌饼"),
    (2, 2, "微信钱包", "午餐", "23.00", "支出", "午餐：麻辣烫"),
    (2, 5, "支付宝", "晚餐", "45.00", "支出", "晚餐：烧烤"),
    (2, 6, "支付宝", "公共交通", "4.00", "支出", "地铁"),
    (2, 1, "工商银行储蓄卡", "房租", "3500.00", "支出", "房租"),
    (2, 8, "工商银行储蓄卡", "水电燃气", "265.00", "支出", "水电燃气"),
    (2, 12, "现金", "加油充电", "180.00", "支出", "加油"),
    (2, 10, "支付宝", "休闲娱乐", "120.00", "支出", "电影院"),
    (2, 18, "工商银行储蓄卡", "休闲娱乐", "350.00", "支出", "KTV 聚会"),
    (2, 15, "支付宝", "购物消费", "89.00", "支出", "超市日用品"),
    (2, 1, "微信钱包", "通讯网络", "59.00", "支出", "手机话费充值"),
    # ---------------- 1 个月前 ----------------
    (1, 6, "工商银行储蓄卡", "工资收入", "15000.00", "收入", "工资"),
    (1, 4, "现金", "早餐", "6.00", "支出", "早餐"),
    (1, 4, "微信钱包", "午餐", "25.00", "支出", "午餐：煲仔饭"),
    (1, 8, "现金", "晚餐", "30.00", "支出", "晚餐"),
    (1, 10, "支付宝", "零食饮料", "13.00", "支出", "咖啡"),
    (1, 7, "支付宝", "公共交通", "4.00", "支出", "地铁"),
    (1, 1, "工商银行储蓄卡", "房租", "3500.00", "支出", "房租"),
    (1, 9, "工商银行储蓄卡", "水电燃气", "250.00", "支出", "水电燃气"),
    (1, 18, "现金", "加油充电", "220.00", "支出", "加油"),
    (1, 12, "工商银行储蓄卡", "医疗健康", "180.00", "支出", "感冒药+门诊"),
    (1, 16, "支付宝", "教育培训", "299.00", "支出", "在线课程"),
    (1, 20, "支付宝", "购物消费", "450.00", "支出", "运动鞋"),
    (1, 25, "微信钱包", "休闲娱乐", "68.00", "支出", "视频会员年费"),
    # ---------------- 本月 ----------------
    (0, 5, "工商银行储蓄卡", "工资收入", "15000.00", "收入", "工资"),
    (0, 1, "现金", "早餐", "8.00", "支出", "早餐"),
    (0, 1, "微信钱包", "午餐", "28.00", "支出", "午餐：红烧肉"),
    (0, 5, "支付宝", "晚餐", "42.00", "支出", "晚餐：庆祝发工资"),
    (0, 3, "支付宝", "公共交通", "4.00", "支出", "地铁"),
    (0, 1, "工商银行储蓄卡", "房租", "3500.00", "支出", "房租"),
    (0, 8, "工商银行储蓄卡", "水电燃气", "275.00", "支出", "水电燃气"),
    (0, 10, "现金", "加油充电", "190.00", "支出", "加油"),
    (0, 12, "支付宝", "购物消费", "129.00", "支出", "购物：背包"),
    (0, 12, "股票账户", "投资收益", "850.00", "收入", "股票分红收益"),
)

# 每月固定转账：工商银行储蓄卡 → 支付宝
MONTHLY_TRANSFER = ("工商银行储蓄卡", "支付宝", "2000.00", 5)

BUDGETS: tuple[tuple[int, str, str], ...] = (
    (0, "餐饮", "2000.00"),
    (0, "交通", "500.00"),
    (0, "购物消费", "1500.00"),
    (0, "休闲娱乐", "800.00"),
    (1, "餐饮", "2000.00"),
    (1, "交通", "500.00"),
    (1, "购物消费", "1500.00"),
)

ASSET_LIABILITY: tuple[tuple[str, str, str, str, str], ...] = (
    ("定期存款", "资产", "100000.00", "2025-01-15", "一年期定期"),
    ("股票持仓", "资产", "120000.00", "2025-06-01", "沪深300 ETF + 个股"),
    ("住房贷款", "负债", "1500000.00", "2023-03-20", "30 年期按揭"),
    ("信用卡欠款", "负债", "5000.00", "2026-01-10", "本期账单待还"),
    ("消费贷款", "负债", "20000.00", "2026-02-01", "12 期分期"),
)

DEMO_PASSWORD_HASH = "pbkdf2_sha256$demo$not-a-real-hash"  # 演示占位

DELETE_ORDER = (
    "knowledge_chunk",
    "knowledge_document",
    "ai_action_log",
    "transaction_record",
    "budget",
    "asset_liability",
    "account",
    "category",
    "user_info",
)


# --------------------------------------------------------------------------- #
# 日期工具
# --------------------------------------------------------------------------- #
def _month_first_day(offset: int) -> date:
    today = date.today()
    year, month = today.year, today.month
    for _ in range(offset):
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return date(year, month, 1)


def month_label(offset: int) -> str:
    first = _month_first_day(offset)
    return f"{first.year:04d}-{first.month:02d}"


def _day_in_month(offset: int, day: int) -> str:
    first = _month_first_day(offset)
    last_day = calendar.monthrange(first.year, first.month)[1]
    return date(first.year, first.month, min(day, last_day)).isoformat()


def _ph(tx: Tx) -> str:
    """按后端生成占位符，让同一段 SQL 在两种方言下都能用。"""
    return "?" if tx.adapter.mode == "sqlite" else "%s"


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def is_seeded(adapter: DatabaseAdapter) -> bool:
    row = adapter.query_one("SELECT COUNT(*) AS total FROM transaction_record")
    return bool(row and int(row["total"]) > 0)


def seed_database(adapter: DatabaseAdapter, *, force: bool = False) -> dict[str, Any]:
    """写入演示数据。已有交易记录时默认跳过（幂等）。"""
    adapter.init_schema()  # SQLite：建表+触发器+视图；PostgreSQL：结构自检

    if is_seeded(adapter) and not force:
        return {"seeded": False, "reason": "已存在交易数据，跳过"}

    if force:
        _reset(adapter)

    with adapter.write_transaction() as tx:
        user_id = _ensure_user(tx)
        account_ids = _ensure_accounts(tx, user_id)
        category_ids = _ensure_categories(tx, user_id)
        _ensure_transactions(tx, user_id, account_ids, category_ids)
        _ensure_budgets(tx, user_id, category_ids)
        _ensure_assets(tx, user_id)

    return {
        "seeded": True,
        "user_id": user_id,
        "accounts": len(ACCOUNTS),
        "transactions": len(TRANSACTIONS) + 2 * 4,  # 4 次月度转账，各两条记录
        "budgets": len(BUDGETS),
        "asset_liability": len(ASSET_LIABILITY),
    }


def _reset(adapter: DatabaseAdapter) -> None:
    """清空业务数据后重新播种。

    删除交易会触发余额回滚，而清库过程中余额必然短暂为负，
    因此先摘掉 DELETE 触发器，清完再重建（SQLite 的 DDL 也受事务保护）。
    """
    with adapter.write_transaction() as tx:
        if adapter.mode == "sqlite":
            tx.execute("DROP TRIGGER IF EXISTS trg_transaction_balance_delete")
        else:
            tx.execute("ALTER TABLE transaction_record DISABLE TRIGGER trg_transaction_balance")
        for table in DELETE_ORDER:
            tx.execute(f"DELETE FROM {table}")
        if adapter.mode == "sqlite":
            # AUTOINCREMENT 不会复用已删除的 id，重置后需要手动复位序列
            has_sequence = tx.query_one(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'"
            )
            if has_sequence:
                tx.execute("DELETE FROM sqlite_sequence")
        if adapter.mode != "sqlite":
            tx.execute("ALTER TABLE transaction_record ENABLE TRIGGER trg_transaction_balance")
    adapter.init_schema()  # 恢复被摘掉的 DELETE 触发器


# --------------------------------------------------------------------------- #
# 内部分步
# --------------------------------------------------------------------------- #
def _ensure_user(tx: Tx) -> int:
    existing = tx.query_one("SELECT user_id FROM user_info ORDER BY user_id LIMIT 1")
    if existing:
        return int(existing["user_id"])
    # 固定 user_id = 1，与 config.default_user_id 对齐。
    # 否则每次重置数据后自增会漂到 2、3…，前端默认用户就查不到自己的数据了。
    user_id = tx.insert(
        "user_info",
        {"user_id": 1, "user_name": "张三", "password_hash": DEMO_PASSWORD_HASH},
    )
    if tx.adapter.mode == "postgres":
        # PostgreSQL 的 SERIAL 序列不会被显式 id 插入推进，
        # 不修正的话下一次自动插入仍会拿到 1 → 主键冲突（SQLite 的 AUTOINCREMENT 会自动记住最大值）。
        tx.execute(
            "SELECT setval(pg_get_serial_sequence('user_info', 'user_id'), "
            "(SELECT COALESCE(MAX(user_id), 1) FROM user_info))"
        )
    return user_id


def _ensure_accounts(tx: Tx, user_id: int) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for name, account_type, balance in ACCOUNTS:
        row = tx.query_one(
            f"SELECT account_id FROM account WHERE user_id = {_ph(tx)} AND account_name = {_ph(tx)}",
            (user_id, name),
        )
        if row:
            mapping[name] = int(row["account_id"])
            continue
        mapping[name] = tx.insert(
            "account",
            {
                "user_id": user_id,
                "account_name": name,
                "account_type": account_type,
                "balance": Decimal(balance),
            },
        )
    return mapping


def _ensure_categories(tx: Tx, user_id: int) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for name in INCOME_CATEGORIES:
        mapping[name] = _ensure_category(tx, user_id, name, "收入", None)
    for name in EXPENSE_PARENTS:
        mapping[name] = _ensure_category(tx, user_id, name, "支出", None)
    for child, parent in EXPENSE_CHILDREN:
        mapping[child] = _ensure_category(tx, user_id, child, "支出", mapping[parent])
    return mapping


def _ensure_category(tx: Tx, user_id: int, name: str, category_type: str, parent_id: int | None) -> int:
    row = tx.query_one(
        f"SELECT category_id FROM category WHERE user_id = {_ph(tx)} "
        f"AND category_name = {_ph(tx)} AND category_type = {_ph(tx)} LIMIT 1",
        (user_id, name, category_type),
    )
    if row:
        return int(row["category_id"])
    return tx.insert(
        "category",
        {
            "user_id": user_id,
            "category_name": name,
            "category_type": category_type,
            "parent_id": parent_id,
        },
    )


def _ensure_transactions(
    tx: Tx,
    user_id: int,
    account_ids: dict[str, int],
    category_ids: dict[str, int],
) -> None:
    # 按日期先后插入，保证触发器校验余额时账户里已经有了足够的钱
    ordered = sorted(TRANSACTIONS, key=lambda item: (-item[0], item[1]))
    for offset, day, account_name, category_name, amount, trans_type, remark in ordered:
        tx.insert(
            "transaction_record",
            {
                "user_id": user_id,
                "account_id": account_ids[account_name],
                "category_id": category_ids[category_name],
                "amount": Decimal(amount),
                "trans_type": trans_type,
                "trans_date": _day_in_month(offset, day),
                "remark": remark,
            },
        )

    # 月度转账：与生产代码一致——两条记录 + 同一个 transfer_group_id
    from_account, to_account, amount, day = MONTHLY_TRANSFER
    for offset in (3, 2, 1, 0):
        _insert_transfer_pair(
            tx,
            user_id=user_id,
            from_account=account_ids[from_account],
            to_account=account_ids[to_account],
            amount=Decimal(amount),
            trans_date=_day_in_month(offset, day),
            remark="月度资金调拨",
            transfer_group=f"seed-transfer-{month_label(offset)}",
            expense_category=category_ids["其他支出"],
            income_category=category_ids["其他收入"],
        )


def _insert_transfer_pair(
    tx: Tx,
    *,
    user_id: int,
    from_account: int,
    to_account: int,
    amount: Decimal,
    trans_date: str,
    remark: str,
    transfer_group: str,
    expense_category: int,
    income_category: int,
) -> None:
    tx.insert(
        "transaction_record",
        {
            "user_id": user_id,
            "account_id": from_account,
            "category_id": expense_category,
            "amount": amount,
            "trans_type": "支出",
            "trans_date": trans_date,
            "remark": f"{remark}（转出）",
            "transfer_group_id": transfer_group,
        },
    )
    tx.insert(
        "transaction_record",
        {
            "user_id": user_id,
            "account_id": to_account,
            "category_id": income_category,
            "amount": amount,
            "trans_type": "收入",
            "trans_date": trans_date,
            "remark": f"{remark}（转入）",
            "transfer_group_id": transfer_group,
        },
    )


def _ensure_budgets(tx: Tx, user_id: int, category_ids: dict[str, int]) -> None:
    for offset, category_name, amount in BUDGETS:
        label = month_label(offset)
        existing = tx.query_one(
            f"SELECT budget_id FROM budget WHERE user_id = {_ph(tx)} "
            f"AND category_id = {_ph(tx)} AND year_month = {_ph(tx)}",
            (user_id, category_ids[category_name], label),
        )
        if existing:
            continue
        tx.insert(
            "budget",
            {
                "user_id": user_id,
                "category_id": category_ids[category_name],
                "year_month": label,
                "budget_amount": Decimal(amount),
            },
        )


def _ensure_assets(tx: Tx, user_id: int) -> None:
    row = tx.query_one(
        f"SELECT COUNT(*) AS total FROM asset_liability WHERE user_id = {_ph(tx)}",
        (user_id,),
    )
    if row and int(row["total"]) > 0:
        return
    for item_name, item_type, amount, acquire_date, remark in ASSET_LIABILITY:
        tx.insert(
            "asset_liability",
            {
                "user_id": user_id,
                "item_name": item_name,
                "item_type": item_type,
                "amount": Decimal(amount),
                "acquire_date": acquire_date,
                "remark": remark,
            },
        )


__all__ = [
    "ACCOUNTS",
    "ASSET_LIABILITY",
    "BUDGETS",
    "TRANSACTIONS",
    "is_seeded",
    "month_label",
    "seed_database",
]
