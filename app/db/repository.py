"""仓储层：所有领域 SQL 集中在这里。

设计要点
--------
* **报表优先读视图**：``v_category_expense_ratio`` / ``v_budget_status`` /
  ``v_cash_flow_trend`` / ``v_net_worth`` / ``v_monthly_income_expense``
  都被真实调用，不再出现"视图建了却没人用、应用层再写一遍聚合 SQL"。
* **月度过滤一律用左闭右开范围谓词**（``trans_date >= ? AND trans_date <= ?``），
  避免 ``TO_CHAR(trans_date,'YYYY-MM') = ?`` 造成的索引失效。
* **账户余额不允许被应用层写入**：``update_account`` 只改名称与类型。
* 两个后端共用同一段 SQL，只通过 ``self.ph`` 切换占位符；差异部分
  （转账存储过程、报表函数）委托给适配器。
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Sequence

from .base import (
    DatabaseAdapter,
    NotFoundError,
    Tx,
    current_month,
    day_shift,
    month_bounds,
    quantize_money,
)

TRANSACTION_TYPES = ("收入", "支出", "转账")
ACCOUNT_TYPES = ("现金", "银行卡", "电子钱包", "投资账户")


def previous_months(count: int, *, include_current: bool = False) -> list[str]:
    """返回最近 count 个月的年月标签，从旧到新。"""
    today = date.today()
    year, month = today.year, today.month
    labels: list[str] = []
    for step in range(count + (1 if include_current else 0)):
        if step == 0 and include_current:
            labels.append(f"{year:04d}-{month:02d}")
            continue
        target_year, target_month = year, month - step - (0 if include_current else 1)
        while target_month <= 0:
            target_month += 12
            target_year -= 1
        labels.append(f"{target_year:04d}-{target_month:02d}")
    return sorted(set(labels))


class LedgerRepository:
    """账本仓储。所有方法都以 ``user_id`` 作为第一道隔离条件。"""

    def __init__(self, adapter: DatabaseAdapter) -> None:
        self.adapter = adapter

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    @property
    def ph(self) -> str:
        return "?" if self.adapter.mode == "sqlite" else "%s"

    def _placeholders(self, count: int) -> str:
        return ", ".join([self.ph] * count)

    def _like(self, keyword: str) -> str:
        # 通配符放在参数里，避免 psycopg2 把 SQL 文本中的 % 当成占位符
        return f"%{keyword.strip()}%"

    # ------------------------------------------------------------------ #
    # 账户
    # ------------------------------------------------------------------ #
    def list_accounts(self, user_id: int) -> list[dict[str, Any]]:
        # 使用视图 v_account_balances（带类型图标，且已按 user_id 维度拆分）
        return self.adapter.query(
            f"SELECT account_id, user_id, account_name, account_type, balance, type_icon "
            f"FROM v_account_balances WHERE user_id = {self.ph} ORDER BY account_id",
            (user_id,),
        )

    def get_account(self, user_id: int, account_id: int) -> dict[str, Any] | None:
        return self.adapter.query_one(
            f"SELECT account_id, account_name, account_type, balance FROM account "
            f"WHERE account_id = {self.ph} AND user_id = {self.ph}",
            (account_id, user_id),
        )

    def create_account(self, user_id: int, name: str, account_type: str, opening_balance: Decimal) -> int:
        with self.adapter.write_transaction() as tx:
            return tx.insert(
                "account",
                {
                    "user_id": user_id,
                    "account_name": name.strip(),
                    "account_type": account_type,
                    "balance": quantize_money(opening_balance),
                },
            )

    def update_account(self, user_id: int, account_id: int, name: str, account_type: str) -> int:
        """只允许改名称与类型。

        原 Flask 版允许直接改 ``balance``，等于绕开触发器维护的不变量，
        这里直接把这条路堵掉：余额只能由交易驱动。
        """
        with self.adapter.write_transaction() as tx:
            affected = tx.execute(
                f"UPDATE account SET account_name = {self.ph}, account_type = {self.ph} "
                f"WHERE account_id = {self.ph} AND user_id = {self.ph}",
                (name.strip(), account_type, account_id, user_id),
            )
            if affected == 0:
                raise NotFoundError("账户不存在")
            return affected

    def delete_account(self, user_id: int, account_id: int) -> dict[str, Any]:
        """删除账户及其交易。

        余额回滚由 DELETE 触发器完成，应用层不做余额加减。
        转账的另一半会变成孤立记录，因此这里显式返回被影响的转账组数量。
        """
        with self.adapter.write_transaction() as tx:
            account = tx.query_one(
                f"SELECT account_id FROM account WHERE account_id = {self.ph} AND user_id = {self.ph}",
                (account_id, user_id),
            )
            if not account:
                raise NotFoundError("账户不存在")

            orphan_groups = tx.query(
                f"SELECT DISTINCT transfer_group_id FROM transaction_record "
                f"WHERE account_id = {self.ph} AND user_id = {self.ph} "
                f"AND transfer_group_id IS NOT NULL",
                (account_id, user_id),
            )
            removed = tx.execute(
                f"DELETE FROM transaction_record WHERE account_id = {self.ph} AND user_id = {self.ph}",
                (account_id, user_id),
            )
            tx.execute(
                f"DELETE FROM account WHERE account_id = {self.ph} AND user_id = {self.ph}",
                (account_id, user_id),
            )
            return {
                "deleted_account": account_id,
                "deleted_transactions": removed,
                "orphan_transfer_groups": [row["transfer_group_id"] for row in orphan_groups],
            }

    # ------------------------------------------------------------------ #
    # 类别
    # ------------------------------------------------------------------ #
    def list_categories(self, user_id: int, category_type: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT c.category_id, c.category_name, c.category_type, c.parent_id, "
            "       COALESCE(pc.category_name, '') AS parent_name "
            "FROM category c LEFT JOIN category pc ON c.parent_id = pc.category_id "
            f"WHERE c.user_id = {self.ph}"
        )
        params: list[Any] = [user_id]
        if category_type:
            sql += f" AND c.category_type = {self.ph}"
            params.append(category_type)
        sql += " ORDER BY c.category_type, c.parent_id NULLS FIRST, c.category_id"
        if self.adapter.mode == "sqlite":
            # SQLite 不支持 NULLS FIRST，用表达式等价实现
            sql = sql.replace(
                "c.parent_id NULLS FIRST",
                "CASE WHEN c.parent_id IS NULL THEN 0 ELSE 1 END, c.parent_id",
            )
        return self.adapter.query(sql, params)

    def find_category_by_name(self, user_id: int, name: str, category_type: str) -> dict[str, Any] | None:
        return self.adapter.query_one(
            f"SELECT category_id, category_name, category_type, parent_id FROM category "
            f"WHERE user_id = {self.ph} AND category_name = {self.ph} AND category_type = {self.ph} LIMIT 1",
            (user_id, name.strip(), category_type),
        )

    def ensure_category(self, user_id: int, name: str, category_type: str) -> int:
        """按名字查找类别，不存在则新建（供 AI 记账使用）。"""
        existing = self.find_category_by_name(user_id, name, category_type)
        if existing:
            return int(existing["category_id"])
        if category_type not in ("收入", "支出"):
            raise ValueError("类别类型必须是收入或支出")
        with self.adapter.write_transaction() as tx:
            return tx.insert(
                "category",
                {
                    "user_id": user_id,
                    "category_name": name.strip(),
                    "category_type": category_type,
                    "parent_id": None,
                },
            )

    def category_tree_ids(self, user_id: int, category_id: int) -> list[int]:
        """递归取自身 + 所有下级类别（两个后端都支持 WITH RECURSIVE）。"""
        rows = self.adapter.query(
            "WITH RECURSIVE cat_tree AS ("
            f"  SELECT category_id FROM category WHERE category_id = {self.ph} AND user_id = {self.ph}"
            "   UNION ALL"
            "   SELECT c.category_id FROM category c JOIN cat_tree t ON c.parent_id = t.category_id"
            ") SELECT category_id FROM cat_tree",
            (category_id, user_id),
        )
        return [int(row["category_id"]) for row in rows]

    # ------------------------------------------------------------------ #
    # 交易
    # ------------------------------------------------------------------ #
    def list_transactions(
        self,
        user_id: int,
        *,
        month: str | None = None,
        trans_type: str | None = None,
        account_id: int | None = None,
        category_id: int | None = None,
        keyword: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        conditions = [f"t.user_id = {self.ph}"]
        params: list[Any] = [user_id]

        if month:
            start, end = month_bounds(month)
            conditions.append(f"t.trans_date >= {self.ph} AND t.trans_date <= {self.ph}")
            params.extend([start, end])
        if trans_type:
            if trans_type == "转账":
                # 用 transfer_group_id 判定转账（修复：不再靠"时间差 + 备注 LIKE"）
                conditions.append("t.transfer_group_id IS NOT NULL")
            else:
                conditions.append(f"t.trans_type = {self.ph}")
                params.append(trans_type)
        if account_id:
            conditions.append(f"t.account_id = {self.ph}")
            params.append(account_id)
        if category_id:
            ids = self.category_tree_ids(user_id, category_id)
            if not ids:
                return []
            conditions.append(f"t.category_id IN ({self._placeholders(len(ids))})")
            params.extend(ids)
        if keyword:
            conditions.append(f"(c.category_name LIKE {self.ph} OR t.remark LIKE {self.ph})")
            params.extend([self._like(keyword), self._like(keyword)])

        sql = (
            "SELECT t.transaction_id, t.amount, t.trans_type, t.trans_date, t.remark, "
            "       t.transfer_group_id, t.create_time, t.account_id, t.category_id, "
            "       a.account_name, a.account_type, c.category_name, c.category_type, "
            "       pc.category_name AS parent_category_name "
            "FROM transaction_record t "
            "JOIN account a ON t.account_id = a.account_id "
            "JOIN category c ON t.category_id = c.category_id "
            "LEFT JOIN category pc ON c.parent_id = pc.category_id "
            f"WHERE {' AND '.join(conditions)} "
            "ORDER BY t.trans_date DESC, t.transaction_id DESC "
            f"LIMIT {self.ph} OFFSET {self.ph}"
        )
        params.extend([max(1, min(limit, 500)), max(0, offset)])
        rows = self.adapter.query(sql, params)
        for row in rows:
            row["is_transfer"] = bool(row.get("transfer_group_id"))
            row["trans_date"] = _as_iso(row.get("trans_date"))
            row["create_time"] = _as_iso(row.get("create_time"))
        return rows

    def get_transaction(self, user_id: int, transaction_id: int) -> dict[str, Any] | None:
        return self.adapter.query_one(
            f"SELECT transaction_id, account_id, category_id, amount, trans_type, trans_date, "
            f"       remark, transfer_group_id FROM transaction_record "
            f"WHERE transaction_id = {self.ph} AND user_id = {self.ph}",
            (transaction_id, user_id),
        )

    def create_transaction(
        self,
        user_id: int,
        *,
        account_id: int,
        category_id: int,
        amount: Decimal,
        trans_type: str,
        trans_date: str | None = None,
        remark: str = "",
    ) -> int:
        """新增一笔交易。余额由触发器变更，这里不做任何加减。"""
        value = quantize_money(amount)
        if value <= 0:
            raise ValueError("金额必须大于 0")
        if trans_type not in ("收入", "支出"):
            raise ValueError("交易类型必须是收入或支出（转账请使用 /api/transfers）")
        with self.adapter.write_transaction() as tx:
            self._assert_account_owned(tx, user_id, account_id)
            return tx.insert(
                "transaction_record",
                {
                    "user_id": user_id,
                    "account_id": account_id,
                    "category_id": category_id,
                    "amount": value,
                    "trans_type": trans_type,
                    "trans_date": trans_date or date.today().isoformat(),
                    "remark": remark.strip(),
                },
            )

    def update_transaction(
        self,
        user_id: int,
        transaction_id: int,
        *,
        account_id: int,
        category_id: int,
        amount: Decimal,
        trans_type: str,
        trans_date: str,
        remark: str = "",
    ) -> int:
        value = quantize_money(amount)
        if value <= 0:
            raise ValueError("金额必须大于 0")
        if trans_type not in ("收入", "支出"):
            raise ValueError("交易类型必须是收入或支出")
        with self.adapter.write_transaction() as tx:
            existing = tx.query_one(
                f"SELECT transaction_id FROM transaction_record "
                f"WHERE transaction_id = {self.ph} AND user_id = {self.ph}",
                (transaction_id, user_id),
            )
            if not existing:
                raise NotFoundError("交易记录不存在")
            self._assert_account_owned(tx, user_id, account_id)
            affected = tx.execute(
                f"UPDATE transaction_record SET account_id = {self.ph}, category_id = {self.ph}, "
                f"amount = {self.ph}, trans_type = {self.ph}, trans_date = {self.ph}, remark = {self.ph} "
                f"WHERE transaction_id = {self.ph} AND user_id = {self.ph}",
                (account_id, category_id, value, trans_type, trans_date, remark.strip(), transaction_id, user_id),
            )
            # 余额由 UPDATE 触发器撤销旧值、应用新值，并在余额为负时抛错回滚
            return affected

    def delete_transaction(self, user_id: int, transaction_id: int) -> int:
        with self.adapter.write_transaction() as tx:
            existing = tx.query_one(
                f"SELECT transaction_id FROM transaction_record "
                f"WHERE transaction_id = {self.ph} AND user_id = {self.ph}",
                (transaction_id, user_id),
            )
            if not existing:
                raise NotFoundError("交易记录不存在")
            # 余额回滚由 DELETE 触发器完成；若会导致负余额，触发器会中止本次删除
            return tx.execute(
                f"DELETE FROM transaction_record WHERE transaction_id = {self.ph} AND user_id = {self.ph}",
                (transaction_id, user_id),
            )

    def transfer(
        self,
        user_id: int,
        *,
        from_account: int,
        to_account: int,
        amount: Decimal,
        trans_date: str | None = None,
        remark: str = "",
    ) -> dict[str, Any]:
        """账户间转账。PostgreSQL 走存储过程，SQLite 走同事务双记录。"""
        value = quantize_money(amount)
        if value <= 0:
            raise ValueError("转账金额必须大于 0")
        if from_account == to_account:
            raise ValueError("转出与转入账户不能相同")

        transfer_group = str(uuid.uuid4())
        with self.adapter.write_transaction() as tx:
            self._assert_account_owned(tx, user_id, from_account)
            self._assert_account_owned(tx, user_id, to_account)
            self.adapter.call_transfer(
                tx,
                user_id=user_id,
                from_account=from_account,
                to_account=to_account,
                amount=value,
                transfer_group=transfer_group,
                trans_date=trans_date or date.today().isoformat(),
                remark=remark,
            )
        return {
            "transfer_group_id": transfer_group,
            "from_account": from_account,
            "to_account": to_account,
            "amount": value,
        }

    def _assert_account_owned(self, tx: Tx, user_id: int, account_id: int) -> None:
        row = tx.query_one(
            f"SELECT account_id FROM account WHERE account_id = {self.ph} AND user_id = {self.ph}",
            (account_id, user_id),
        )
        if not row:
            raise NotFoundError(f"账户不存在或不属于当前用户：{account_id}")

    # ------------------------------------------------------------------ #
    # 预算
    # ------------------------------------------------------------------ #
    def list_budgets(self, user_id: int, month: str) -> list[dict[str, Any]]:
        return self.adapter.query(
            "SELECT b.budget_id, b.year_month, b.budget_amount, c.category_id, c.category_name "
            "FROM budget b JOIN category c ON b.category_id = c.category_id "
            f"WHERE b.user_id = {self.ph} AND b.year_month = {self.ph} "
            "ORDER BY b.budget_amount DESC",
            (user_id, month),
        )

    def upsert_budget(self, user_id: int, category_id: int, month: str, amount: Decimal) -> None:
        month_bounds(month)  # 校验格式
        value = quantize_money(amount)
        if value <= 0:
            raise ValueError("预算金额必须大于 0")
        # ON CONFLICT 在 PostgreSQL 与 SQLite(3.24+) 上语义一致
        self.adapter.execute(
            "INSERT INTO budget (user_id, category_id, year_month, budget_amount) "
            f"VALUES ({self._placeholders(4)}) "
            "ON CONFLICT (user_id, category_id, year_month) "
            "DO UPDATE SET budget_amount = EXCLUDED.budget_amount",
            (user_id, category_id, month, value),
        )

    def budget_status(self, user_id: int, month: str) -> list[dict[str, Any]]:
        """预算执行情况 —— 直接读视图 v_budget_status。"""
        return self.adapter.query(
            f"SELECT year_month, category_name, budget_amount, actual_expense, completion_rate, status "
            f"FROM v_budget_status WHERE user_id = {self.ph} AND year_month = {self.ph} "
            "ORDER BY completion_rate DESC",
            (user_id, month),
        )

    def copy_budget(self, user_id: int, source_month: str, target_month: str) -> int:
        """把某月预算复制到另一月。PostgreSQL 调用 sp_copy_budget 存储过程。"""
        month_bounds(source_month)
        month_bounds(target_month)
        if self.adapter.mode == "postgres":
            self.adapter.execute(
                "CALL sp_copy_budget(%s, %s, %s)", (user_id, source_month, target_month)
            )
            return len(self.list_budgets(user_id, target_month))
        # SQLite 无存储过程：等价 upsert
        rows = self.list_budgets(user_id, source_month)
        for row in rows:
            self.upsert_budget(user_id, int(row["category_id"]), target_month, row["budget_amount"])
        return len(rows)

    # ------------------------------------------------------------------ #
    # 资产负债
    # ------------------------------------------------------------------ #
    def list_asset_liability(self, user_id: int) -> list[dict[str, Any]]:
        rows = self.adapter.query(
            f"SELECT item_id, item_name, item_type, amount, acquire_date, remark FROM asset_liability "
            f"WHERE user_id = {self.ph} ORDER BY item_type, amount DESC",
            (user_id,),
        )
        for row in rows:
            row["acquire_date"] = _as_iso(row.get("acquire_date"))
        return rows

    def create_asset_liability(
        self,
        user_id: int,
        *,
        item_name: str,
        item_type: str,
        amount: Decimal,
        acquire_date: str,
        remark: str = "",
    ) -> int:
        self._validate_asset(item_name, item_type, amount, acquire_date)
        with self.adapter.write_transaction() as tx:
            return tx.insert(
                "asset_liability",
                {
                    "user_id": user_id,
                    "item_name": item_name.strip(),
                    "item_type": item_type,
                    "amount": quantize_money(amount),
                    "acquire_date": acquire_date,
                    "remark": remark.strip(),
                },
            )

    def update_asset_liability(
        self,
        user_id: int,
        item_id: int,
        *,
        item_name: str,
        item_type: str,
        amount: Decimal,
        acquire_date: str,
        remark: str = "",
    ) -> int:
        self._validate_asset(item_name, item_type, amount, acquire_date)
        with self.adapter.write_transaction() as tx:
            affected = tx.execute(
                f"UPDATE asset_liability SET item_name = {self.ph}, item_type = {self.ph}, "
                f"amount = {self.ph}, acquire_date = {self.ph}, remark = {self.ph} "
                f"WHERE item_id = {self.ph} AND user_id = {self.ph}",
                (item_name.strip(), item_type, quantize_money(amount), acquire_date, remark.strip(), item_id, user_id),
            )
            if affected == 0:
                raise NotFoundError("资产/负债项不存在")
            return affected

    def delete_asset_liability(self, user_id: int, item_id: int) -> int:
        with self.adapter.write_transaction() as tx:
            affected = tx.execute(
                f"DELETE FROM asset_liability WHERE item_id = {self.ph} AND user_id = {self.ph}",
                (item_id, user_id),
            )
            if affected == 0:
                raise NotFoundError("资产/负债项不存在")
            return affected

    @staticmethod
    def _validate_asset(item_name: str, item_type: str, amount: Decimal, acquire_date: str) -> None:
        if item_type not in ("资产", "负债"):
            raise ValueError("类型必须是资产或负债")
        if not item_name.strip():
            raise ValueError("名称不能为空")
        if quantize_money(amount) <= 0:
            raise ValueError("金额必须大于 0")
        date.fromisoformat(acquire_date)  # 格式非法直接抛 ValueError

    # ------------------------------------------------------------------ #
    # 报表
    # ------------------------------------------------------------------ #
    def overview(self, user_id: int, month: str | None = None) -> dict[str, Any]:
        """总览：本月/今日/累计 + 净资产 + 日均支出。净资产读视图 v_net_worth。"""
        target_month = month or current_month()
        start, end = month_bounds(target_month)
        today = date.today().isoformat()

        monthly = self.adapter.query_one(
            "SELECT COALESCE(SUM(CASE WHEN trans_type = '收入' THEN amount ELSE 0 END), 0) AS income, "
            "       COALESCE(SUM(CASE WHEN trans_type = '支出' THEN amount ELSE 0 END), 0) AS expense, "
            "       COUNT(*) AS total_count "
            f"FROM transaction_record WHERE user_id = {self.ph} "
            f"AND trans_date >= {self.ph} AND trans_date <= {self.ph}",
            (user_id, start, end),
        ) or {}
        daily = self.adapter.query_one(
            "SELECT COALESCE(SUM(CASE WHEN trans_type = '收入' THEN amount ELSE 0 END), 0) AS income, "
            "       COALESCE(SUM(CASE WHEN trans_type = '支出' THEN amount ELSE 0 END), 0) AS expense "
            f"FROM transaction_record WHERE user_id = {self.ph} AND trans_date = {self.ph}",
            (user_id, today),
        ) or {}
        total = self.adapter.query_one(
            "SELECT COALESCE(SUM(CASE WHEN trans_type = '收入' THEN amount ELSE 0 END), 0) AS income, "
            "       COALESCE(SUM(CASE WHEN trans_type = '支出' THEN amount ELSE 0 END), 0) AS expense, "
            "       COUNT(*) AS total_count "
            f"FROM transaction_record WHERE user_id = {self.ph}",
            (user_id,),
        ) or {}
        average = self.adapter.query_one(
            "SELECT COALESCE(SUM(CASE WHEN trans_type = '支出' THEN amount ELSE 0 END), 0) "
            "       / NULLIF(COUNT(DISTINCT trans_date), 0) AS daily_avg "
            f"FROM transaction_record WHERE user_id = {self.ph} "
            f"AND trans_date >= {self.ph} AND trans_date <= {self.ph}",
            (user_id, start, end),
        ) or {}
        worth = self.net_worth(user_id)

        return {
            "month": target_month,
            "today": today,
            "month_income": monthly.get("income") or Decimal("0"),
            "month_expense": monthly.get("expense") or Decimal("0"),
            "month_count": int(monthly.get("total_count") or 0),
            "today_income": daily.get("income") or Decimal("0"),
            "today_expense": daily.get("expense") or Decimal("0"),
            "total_income_all": total.get("income") or Decimal("0"),
            "total_expense_all": total.get("expense") or Decimal("0"),
            "total_count": int(total.get("total_count") or 0),
            "daily_avg": average.get("daily_avg") or Decimal("0"),
            "net_worth": worth.get("net_worth", Decimal("0")),
        }

    def net_worth(self, user_id: int) -> dict[str, Any]:
        """净资产 —— 直接读视图 v_net_worth。"""
        row = self.adapter.query_one(
            f"SELECT total_assets, total_liabilities, total_account_balance, net_worth "
            f"FROM v_net_worth WHERE user_id = {self.ph}",
            (user_id,),
        )
        if row:
            return row
        # 用户还没有任何账户/资产负债记录时视图无行，返回零值
        zero = Decimal("0.00")
        return {
            "total_assets": zero,
            "total_liabilities": zero,
            "total_account_balance": zero,
            "net_worth": zero,
        }

    def monthly_trend(self, user_id: int) -> list[dict[str, Any]]:
        """月度收支趋势 —— 读视图 v_monthly_income_expense。"""
        return self.adapter.query(
            f"SELECT year_month, total_income, total_expense, net_balance "
            f"FROM v_monthly_income_expense WHERE user_id = {self.ph} ORDER BY year_month",
            (user_id,),
        )

    def daily_cashflow(self, user_id: int, days: int = 30) -> list[dict[str, Any]]:
        """日现金流 —— 读视图 v_cash_flow_trend，用可索引的日期范围限制近 N 天。"""
        since = day_shift(date.today().isoformat(), -abs(days))
        rows = self.adapter.query(
            f"SELECT trans_date, daily_income, daily_expense, net_cashflow FROM v_cash_flow_trend "
            f"WHERE user_id = {self.ph} AND trans_date >= {self.ph} ORDER BY trans_date DESC",
            (user_id, since),
        )
        for row in rows:
            row["trans_date"] = _as_iso(row.get("trans_date"))
        return rows

    def category_ratio(self, user_id: int, month: str | None = None) -> list[dict[str, Any]]:
        """类别支出占比 —— 读视图 v_category_expense_ratio。"""
        return self.adapter.query(
            f"SELECT year_month, category_id, category_name, expense_amount, expense_ratio "
            f"FROM v_category_expense_ratio WHERE user_id = {self.ph} AND year_month = {self.ph} "
            "ORDER BY expense_amount DESC",
            (user_id, month or current_month()),
        )

    def monthly_report(self, user_id: int, month: str) -> dict[str, Any]:
        """月度收支汇总：PostgreSQL 走 fn_monthly_report()，SQLite 走同名视图。"""
        month_bounds(month)
        row = self.adapter.fetch_monthly_report(user_id, month)
        if not row:
            zero = Decimal("0.00")
            return {"year_month": month, "total_income": zero, "total_expense": zero, "net_balance": zero}
        return {
            "year_month": month,
            "total_income": row.get("total_income") or Decimal("0"),
            "total_expense": row.get("total_expense") or Decimal("0"),
            "net_balance": row.get("net_balance") or Decimal("0"),
        }

    def category_spending(self, user_id: int, start: str, end: str) -> list[dict[str, Any]]:
        """区间类别支出：PostgreSQL 走 fn_category_spending()。"""
        return self.adapter.fetch_category_spending(user_id, start, end)

    def expense_by_category(self, user_id: int, months: int = 3, *, include_current: bool = False) -> dict[str, Decimal]:
        """最近若干月的分类支出合计（子类别归并到父类别），供可负担性分析使用。

        两个容易踩的坑：
        * 默认只统计**已完整结束的月份**，否则当月半个月的数据会把月均值拉低；
        * 交易通常记在子类别上（房租、早餐、地铁），必须归并到父类别
          （居住、餐饮、交通），否则"必要支出"只会统计到直接记在父类别上的少数几笔。
        """
        labels = previous_months(months, include_current=include_current)
        if not labels:
            return {}
        start, _ = month_bounds(labels[0])
        _, end = month_bounds(labels[-1])
        rows = self.adapter.query(
            "SELECT COALESCE(pc.category_name, c.category_name) AS category_name, SUM(t.amount) AS total "
            "FROM transaction_record t "
            "JOIN category c ON t.category_id = c.category_id "
            "LEFT JOIN category pc ON c.parent_id = pc.category_id "
            f"WHERE t.user_id = {self.ph} AND t.trans_type = '支出' "
            f"AND t.trans_date >= {self.ph} AND t.trans_date <= {self.ph} "
            "GROUP BY COALESCE(pc.category_name, c.category_name)",
            (user_id, start, end),
        )
        return {row["category_name"]: row["total"] for row in rows}

    def income_stats(self, user_id: int, months: int = 3) -> dict[str, Decimal]:
        """最近若干完整月份的收入统计（月均、最高、最低）。"""
        labels = previous_months(months)
        if not labels:
            return {"monthly_average": Decimal("0"), "months": Decimal("0")}
        start, _ = month_bounds(labels[0])
        _, end = month_bounds(labels[-1])
        rows = self.adapter.query(
            "SELECT year_month, total_income FROM v_monthly_income_expense "
            f"WHERE user_id = {self.ph} AND year_month >= {self.ph} AND year_month <= {self.ph}",
            (user_id, labels[0], labels[-1]),
        )
        incomes = [quantize_money(row["total_income"]) for row in rows]
        if not incomes:
            return {"monthly_average": Decimal("0"), "months": Decimal("0"), "window_start": start, "window_end": end}
        return {
            "monthly_average": quantize_money(sum(incomes) / len(incomes)),
            "max": max(incomes),
            "min": min(incomes),
            "months": Decimal(len(incomes)),
            "window_start": start,
            "window_end": end,
        }

    # ------------------------------------------------------------------ #
    # AI 审计
    # ------------------------------------------------------------------ #
    def log_ai_action(
        self,
        user_id: int,
        *,
        action: str,
        status: str,
        payload: str = "",
        user_input: str = "",
        message: str = "",
    ) -> int:
        if status not in ("preview", "applied", "rejected", "failed"):
            raise ValueError("审计状态非法")
        with self.adapter.write_transaction() as tx:
            return tx.insert(
                "ai_action_log",
                {
                    "user_id": user_id,
                    "action": action,
                    "status": status,
                    "payload": payload,
                    "user_input": user_input[:500],
                    "message": message[:300],
                },
            )

    def list_ai_actions(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.adapter.query(
            f"SELECT log_id, action, status, payload, user_input, message, created_at "
            f"FROM ai_action_log WHERE user_id = {self.ph} ORDER BY log_id DESC LIMIT {self.ph}",
            (user_id, max(1, min(limit, 200))),
        )
        for row in rows:
            row["created_at"] = _as_iso(row.get("created_at"))
        return rows

    def reject_ai_action(self, user_id: int, log_id: int) -> int:
        """把待确认的 AI 写操作标记为已取消。账本不受任何影响。"""
        with self.adapter.write_transaction() as tx:
            affected = tx.execute(
                f"UPDATE ai_action_log SET status = 'rejected' "
                f"WHERE log_id = {self.ph} AND user_id = {self.ph} AND status = 'preview'",
                (log_id, user_id),
            )
            if affected == 0:
                raise NotFoundError("没有找到待确认的操作（可能已被执行或已取消）")
            return affected

    # ------------------------------------------------------------------ #
    # 维护
    # ------------------------------------------------------------------ #
    def clear_data(self, user_id: int) -> dict[str, int]:
        """清空当前用户的业务数据（保留用户与类别）。"""
        with self.adapter.write_transaction() as tx:
            if self.adapter.mode == "sqlite":
                # 清库时余额必然短暂为负，临时摘掉 DELETE 触发器
                tx.execute("DROP TRIGGER IF EXISTS trg_transaction_balance_delete")
            else:
                tx.execute("ALTER TABLE transaction_record DISABLE TRIGGER trg_transaction_balance")
            counts = {
                "transactions": tx.execute(
                    f"DELETE FROM transaction_record WHERE user_id = {self.ph}", (user_id,)
                ),
                "budgets": tx.execute(f"DELETE FROM budget WHERE user_id = {self.ph}", (user_id,)),
                "asset_liability": tx.execute(
                    f"DELETE FROM asset_liability WHERE user_id = {self.ph}", (user_id,)
                ),
                "accounts": tx.execute(f"DELETE FROM account WHERE user_id = {self.ph}", (user_id,)),
                "ai_action_log": tx.execute(
                    f"DELETE FROM ai_action_log WHERE user_id = {self.ph}", (user_id,)
                ),
            }
            if self.adapter.mode != "sqlite":
                tx.execute("ALTER TABLE transaction_record ENABLE TRIGGER trg_transaction_balance")
        if self.adapter.mode == "sqlite":
            self.adapter.init_schema()
        return counts

    def stats(self, user_id: int) -> dict[str, Any]:
        row = self.adapter.query_one(
            "SELECT (SELECT COUNT(*) FROM account WHERE user_id = {p}) AS accounts, "
            "(SELECT COUNT(*) FROM transaction_record WHERE user_id = {p}) AS transactions, "
            "(SELECT COUNT(*) FROM category WHERE user_id = {p}) AS categories, "
            "(SELECT COUNT(*) FROM budget WHERE user_id = {p}) AS budgets, "
            "(SELECT COUNT(*) FROM asset_liability WHERE user_id = {p}) AS asset_liability".format(p=self.ph),
            (user_id,) * 5,
        ) or {}
        return {
            "accounts": int(row.get("accounts") or 0),
            "transactions": int(row.get("transactions") or 0),
            "categories": int(row.get("categories") or 0),
            "budgets": int(row.get("budgets") or 0),
            "asset_liability": int(row.get("asset_liability") or 0),
        }


def _as_iso(value: Any) -> Any:
    """把 date/datetime 统一成 ISO 字符串（两个后端返回类型不同）。"""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


__all__ = [
    "ACCOUNT_TYPES",
    "LedgerRepository",
    "TRANSACTION_TYPES",
    "previous_months",
]
