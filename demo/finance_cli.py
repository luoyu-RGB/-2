#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
个人理财管理系统 — 命令行演示程序
使用 psycopg2 连接 GaussDB / PostgreSQL
"""

import sys
from datetime import date, datetime
from decimal import Decimal

try:
    import psycopg2
    from psycopg2 import sql, errors
except ImportError:
    print("请先安装 psycopg2: pip install psycopg2-binary")
    sys.exit(1)

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False

# ============================================================
# 数据库配置（请根据实际环境修改）
# ============================================================
DB_CONFIG = {
    'host': '222.27.161.245',
    'port': 15432,
    'dbname': 'llhdb',
    'user': 's1362',
    'password': 'Test@1234',
}


# ============================================================
# 数据库操作类
# ============================================================
class FinanceDB:
    """封装数据库连接和核心操作"""

    def __init__(self, config: dict):
        self.config = config
        self.conn = None
        self.cur = None

    def connect(self):
        """连接数据库"""
        try:
            self.conn = psycopg2.connect(**self.config)
            self.cur = self.conn.cursor()
            self.cur.execute("SET search_path TO s1362, public")
            print("✅ 数据库连接成功")
        except psycopg2.Error as e:
            print(f"❌ 数据库连接失败: {e}")
            sys.exit(1)

    def close(self):
        """关闭连接"""
        if self.cur:
            self.cur.close()
        if self.conn:
            self.conn.close()

    def commit(self):
        self.conn.commit()

    # -------------------- 账户操作 --------------------
    def get_accounts(self):
        self.cur.execute(
            "SELECT account_id, account_name, account_type, balance FROM account ORDER BY account_id"
        )
        return self.cur.fetchall()

    def add_account(self, name, atype, balance=0):
        self.cur.execute(
            "INSERT INTO account (user_id, account_name, account_type, balance) VALUES (1, %s, %s, %s)",
            (name, atype, balance),
        )
        self.commit()

    # -------------------- 类别操作 --------------------
    def get_categories(self, cat_type=None):
        if cat_type:
            self.cur.execute(
                """SELECT c.category_id, c.category_name, c.category_type,
                          COALESCE(pc.category_name, '-') AS parent_name
                   FROM category c
                   LEFT JOIN category pc ON c.parent_id = pc.category_id
                   WHERE c.category_type = %s
                   ORDER BY c.category_type, c.parent_id, c.category_id""",
                (cat_type,),
            )
        else:
            self.cur.execute(
                """SELECT c.category_id, c.category_name, c.category_type,
                          COALESCE(pc.category_name, '-') AS parent_name
                   FROM category c
                   LEFT JOIN category pc ON c.parent_id = pc.category_id
                   ORDER BY c.category_type, c.parent_id, c.category_id"""
            )
        return self.cur.fetchall()

    # -------------------- 交易操作 --------------------
    def add_transaction(self, account_id, category_id, amount, trans_type, trans_date=None, remark=None):
        if trans_date is None:
            trans_date = date.today()
        self.cur.execute(
            """INSERT INTO transaction_record
               (user_id, account_id, category_id, amount, trans_type, trans_date, remark)
               VALUES (1, %s, %s, %s, %s, %s, %s)""",
            (account_id, category_id, amount, trans_type, trans_date, remark),
        )
        self.commit()

    def add_transfer(self, from_account, to_account, amount, trans_date=None, remark=None):
        if trans_date is None:
            trans_date = date.today()
        self.cur.execute(
            "CALL sp_add_transfer(1, %s, %s, %s, %s, %s)",
            (from_account, to_account, amount, trans_date, remark),
        )
        self.commit()

    def query_transactions(self, start_date=None, end_date=None, trans_type=None, limit=20):
        conditions = ["user_id = 1"]
        params = []
        if start_date:
            conditions.append("trans_date >= %s")
            params.append(start_date)
        if end_date:
            conditions.append("trans_date <= %s")
            params.append(end_date)
        if trans_type:
            conditions.append("trans_type = %s")
            params.append(trans_type)
        where = " AND ".join(conditions)
        query = f"""
            SELECT transaction_id, a.account_name, c.category_name, amount,
                   trans_type, trans_date, COALESCE(remark,'')
            FROM transaction_record t
            JOIN account a ON t.account_id = a.account_id
            JOIN category c ON t.category_id = c.category_id
            WHERE {where}
            ORDER BY trans_date DESC, transaction_id DESC
            LIMIT %s
        """
        params.append(limit)
        self.cur.execute(query, params)
        return self.cur.fetchall()

    # -------------------- 报表操作 --------------------
    def get_monthly_summary(self, year_month=None):
        if year_month:
            self.cur.execute(
                "SELECT * FROM fn_monthly_report(1, %s)", (year_month,)
            )
        else:
            self.cur.execute("SELECT * FROM v_monthly_income_expense")
        return self.cur.fetchall()

    def get_category_ratio(self, year_month=None):
        if year_month is None:
            year_month = date.today().strftime("%Y-%m")
        self.cur.execute(
            "SELECT category_name, expense_amount, expense_ratio FROM v_category_expense_ratio WHERE year_month = %s",
            (year_month,),
        )
        return self.cur.fetchall()

    def get_budget_status(self, year_month=None):
        if year_month is None:
            year_month = date.today().strftime("%Y-%m")
        self.cur.execute(
            "SELECT category_name, budget_amount, actual_expense, completion_rate FROM v_budget_status WHERE year_month = %s",
            (year_month,),
        )
        return self.cur.fetchall()

    def get_net_worth(self):
        self.cur.execute("SELECT fn_net_worth(1)")
        return self.cur.fetchone()[0]

    def get_asset_liability_detail(self):
        self.cur.execute(
            "SELECT item_name, item_type, amount, acquire_date, COALESCE(remark,'') FROM asset_liability WHERE user_id = 1 ORDER BY item_type, amount DESC"
        )
        return self.cur.fetchall()

    # -------------------- 预算操作 --------------------
    def set_budget(self, category_id, year_month, amount):
        self.cur.execute(
            """INSERT INTO budget (user_id, category_id, year_month, budget_amount)
               VALUES (1, %s, %s, %s)
               ON CONFLICT (user_id, category_id, year_month)
               DO UPDATE SET budget_amount = EXCLUDED.budget_amount""",
            (category_id, year_month, amount),
        )
        self.commit()

    # -------------------- 数据检查 --------------------
    def check_balance_consistency(self):
        self.cur.execute("""
            SELECT a.account_id, a.account_name, a.balance,
                   COALESCE(SUM(CASE WHEN t.trans_type='收入' THEN t.amount ELSE -t.amount END),0) AS calc_balance,
                   a.balance - COALESCE(SUM(CASE WHEN t.trans_type='收入' THEN t.amount ELSE -t.amount END),0) AS diff
            FROM account a
            LEFT JOIN transaction_record t ON a.account_id = t.account_id
            GROUP BY a.account_id, a.account_name, a.balance
            HAVING a.balance <> COALESCE(SUM(CASE WHEN t.trans_type='收入' THEN t.amount ELSE -t.amount END),0)
        """)
        return self.cur.fetchall()


# ============================================================
# 命令行界面
# ============================================================
class FinanceCLI:
    """命令行交互界面"""

    def __init__(self, db: FinanceDB):
        self.db = db

    @staticmethod
    def print_table(headers, rows):
        if HAS_TABULATE:
            print(tabulate(rows, headers=headers, tablefmt="rounded_outline", floatfmt=",.2f"))
        else:
            print("\t".join(headers))
            for row in rows:
                print("\t".join(str(c) for c in row))

    def input_date(self, prompt="日期 (YYYY-MM-DD, 回车=今天): "):
        s = input(prompt).strip()
        if not s:
            return date.today()
        return datetime.strptime(s, "%Y-%m-%d").date()

    def input_decimal(self, prompt="金额: ") -> Decimal:
        while True:
            try:
                return Decimal(input(prompt).strip())
            except Exception:
                print("⚠️ 请输入有效金额")

    def menu_select_account(self):
        accounts = self.db.get_accounts()
        self.print_table(["ID", "账户名", "类型", "余额"], accounts)
        ids = [str(a[0]) for a in accounts]
        while True:
            aid = input("选择账户ID: ").strip()
            if aid in ids:
                return int(aid)
            print("⚠️ 无效ID")

    def menu_select_category(self, cat_type=None):
        cats = self.db.get_categories(cat_type)
        self.print_table(["ID", "类别名", "类型", "父类别"], cats)
        ids = [str(c[0]) for c in cats]
        while True:
            cid = input("选择类别ID: ").strip()
            if cid in ids:
                return int(cid)
            print("⚠️ 无效ID")

    # ---------- 主菜单 ----------
    def run(self):
        while True:
            print("\n" + "=" * 50)
            print("  个人理财管理系统")
            print("=" * 50)
            print("  1. 交易管理")
            print("  2. 报表中心")
            print("  3. 基础数据")
            print("  4. 系统管理")
            print("  0. 退出")
            print("-" * 50)
            choice = input("请选择: ").strip()

            if choice == "1":
                self.menu_transaction()
            elif choice == "2":
                self.menu_report()
            elif choice == "3":
                self.menu_basic()
            elif choice == "4":
                self.menu_system()
            elif choice == "0":
                print("👋 再见！")
                break
            else:
                print("⚠️ 无效选择")

    # ---------- 交易管理 ----------
    def menu_transaction(self):
        while True:
            print("\n--- 交易管理 ---")
            print("  1. 记录收入")
            print("  2. 记录支出")
            print("  3. 转账")
            print("  4. 查询交易")
            print("  0. 返回")
            choice = input("请选择: ").strip()

            if choice == "1":
                self.do_income()
            elif choice == "2":
                self.do_expense()
            elif choice == "3":
                self.do_transfer()
            elif choice == "4":
                self.do_query_transactions()
            elif choice == "0":
                break
            else:
                print("⚠️ 无效选择")

    def do_income(self):
        print("\n--- 记录收入 ---")
        aid = self.menu_select_account()
        cid = self.menu_select_category("收入")
        amount = self.input_decimal()
        d = self.input_date()
        remark = input("备注 (可选): ").strip() or None
        self.db.add_transaction(aid, cid, amount, "收入", d, remark)
        print("✅ 收入记录已保存！")

    def do_expense(self):
        print("\n--- 记录支出 ---")
        aid = self.menu_select_account()
        cid = self.menu_select_category("支出")
        amount = self.input_decimal()
        d = self.input_date()
        remark = input("备注 (可选): ").strip() or None
        self.db.add_transaction(aid, cid, amount, "支出", d, remark)
        print("✅ 支出记录已保存！")

    def do_transfer(self):
        print("\n--- 转账 ---")
        print("【转出账户】")
        from_acc = self.menu_select_account()
        print("【转入账户】")
        to_acc = self.menu_select_account()
        if from_acc == to_acc:
            print("⚠️ 转出和转入账户不能相同！")
            return
        amount = self.input_decimal()
        d = self.input_date()
        remark = input("备注 (可选): ").strip() or None
        try:
            self.db.add_transfer(from_acc, to_acc, amount, d, remark)
            print("✅ 转账完成！")
        except Exception as e:
            print(f"❌ 转账失败: {e}")

    def do_query_transactions(self):
        print("\n--- 查询交易 ---")
        sd = input("开始日期 (YYYY-MM-DD, 回车=不限): ").strip() or None
        ed = input("结束日期 (YYYY-MM-DD, 回车=不限): ").strip() or None
        tt = input("交易类型 (收入/支出/转账, 回车=全部): ").strip() or None
        rows = self.db.query_transactions(sd, ed, tt, limit=50)
        if rows:
            self.print_table(
                ["ID", "账户", "类别", "金额", "类型", "日期", "备注"], rows
            )
            print(f"共 {len(rows)} 条记录")
        else:
            print("📭 无记录")

    # ---------- 报表中心 ----------
    def menu_report(self):
        while True:
            print("\n--- 报表中心 ---")
            print("  1. 月度收支汇总")
            print("  2. 类别支出占比")
            print("  3. 预算执行情况")
            print("  4. 净资产查询")
            print("  5. 账户余额一览")
            print("  0. 返回")
            choice = input("请选择: ").strip()

            if choice == "1":
                self.do_monthly_summary()
            elif choice == "2":
                self.do_category_ratio()
            elif choice == "3":
                self.do_budget_status()
            elif choice == "4":
                self.do_net_worth()
            elif choice == "5":
                self.do_account_balances()
            elif choice == "0":
                break
            else:
                print("⚠️ 无效选择")

    def do_monthly_summary(self):
        ym = input("年月 (YYYY-MM, 回车=全部): ").strip() or None
        rows = self.db.get_monthly_summary(ym)
        if rows:
            if ym:
                headers = ["收入总额", "支出总额", "结余"]
            else:
                headers = ["年月", "收入总额", "支出总额", "结余"]
            self.print_table(headers, rows)

    def do_category_ratio(self):
        ym = input("年月 (YYYY-MM, 回车=本月): ").strip() or None
        rows = self.db.get_category_ratio(ym)
        if rows:
            self.print_table(["类别", "支出金额", "占比(%)"], rows)
        else:
            print("📭 无数据")

    def do_budget_status(self):
        ym = input("年月 (YYYY-MM, 回车=本月): ").strip() or None
        rows = self.db.get_budget_status(ym)
        if rows:
            self.print_table(["类别", "预算", "实际支出", "完成率(%)"], rows)

    def do_net_worth(self):
        nw = self.db.get_net_worth()
        print(f"\n💰 净资产: {nw:,.2f} 元")
        print("\n资产负债明细:")
        rows = self.db.get_asset_liability_detail()
        self.print_table(["项目", "类型", "金额", "日期", "备注"], rows)

    def do_account_balances(self):
        rows = self.db.get_accounts()
        self.print_table(["ID", "账户名", "类型", "余额"], rows)

    # ---------- 基础数据 ----------
    def menu_basic(self):
        while True:
            print("\n--- 基础数据 ---")
            print("  1. 查看账户")
            print("  2. 查看类别")
            print("  3. 设置预算")
            print("  0. 返回")
            choice = input("请选择: ").strip()

            if choice == "1":
                self.do_account_balances()
            elif choice == "2":
                rows = self.db.get_categories()
                self.print_table(["ID", "类别名", "类型", "父类别"], rows)
            elif choice == "3":
                self.do_set_budget()
            elif choice == "0":
                break

    def do_set_budget(self):
        print("\n--- 设置预算 ---")
        ym = input("年月 (YYYY-MM): ").strip()
        if not ym:
            print("⚠️ 年月不能为空")
            return
        cid = self.menu_select_category("支出")
        amount = self.input_decimal("预算金额: ")
        self.db.set_budget(cid, ym, amount)
        print("✅ 预算已保存！")

    # ---------- 系统管理 ----------
    def menu_system(self):
        while True:
            print("\n--- 系统管理 ---")
            print("  1. 数据一致性检查")
            print("  0. 返回")
            choice = input("请选择: ").strip()

            if choice == "1":
                diffs = self.db.check_balance_consistency()
                if diffs:
                    print("⚠️ 发现余额不一致:")
                    self.print_table(["账户ID", "账户名", "余额", "计算余额", "差异"], diffs)
                else:
                    print("✅ 所有账户余额与交易记录一致！")
            elif choice == "0":
                break


# ============================================================
# 入口
# ============================================================
def main():
    db = FinanceDB(DB_CONFIG)
    db.connect()
    try:
        cli = FinanceCLI(db)
        cli.run()
    finally:
        db.close()


if __name__ == "__main__":
    main()
