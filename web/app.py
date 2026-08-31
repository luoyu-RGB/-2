#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
个人理财管理系统 — Flask Web 后端
连接 GaussDB，提供 REST API
"""

import sys
import os
from datetime import date, datetime
from decimal import Decimal

from flask import Flask, render_template, request, jsonify

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("请先安装 psycopg2: pip install psycopg2-binary")
    sys.exit(1)

app = Flask(__name__)

DB_CONFIG = {
    'host': '222.27.161.245',
    'port': 15432,
    'dbname': 'llhdb',
    'user': 's1362',
    'password': 'Test@1234',
    'options': '-c search_path=s1362,public',
}

SCHEMA = 's1362'


def get_db():
    """获取数据库连接"""
    conn = psycopg2.connect(**DB_CONFIG)
    # 确保 search_path 正确
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f"SET search_path TO {SCHEMA}, public")
    cur.close()
    conn.autocommit = False
    return conn


# ============================================================
# 前端页面
# ============================================================
@app.route('/')
def index():
    return render_template('index.html')


# ============================================================
# 账户 API
# ============================================================
@app.route('/api/accounts', methods=['GET'])
def api_accounts():
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT account_id, account_name, account_type, balance FROM account ORDER BY account_id")
    rows = cur.fetchall()
    for r in rows:
        r['balance'] = float(r['balance'])
    cur.close()
    conn.close()
    return jsonify(rows)


@app.route('/api/accounts', methods=['POST'])
def api_add_account():
    data = request.json
    account_name = data.get('account_name', '').strip()
    account_type = data.get('account_type', '').strip()
    try:
        balance = float(data.get('balance', 0))
    except (TypeError, ValueError):
        return jsonify({'error': '余额必须为数字'}), 400

    if not account_name:
        return jsonify({'error': '账户名称不能为空'}), 400
    if balance < 0:
        return jsonify({'error': '余额不能为负数'}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO account (user_id, account_name, account_type, balance) VALUES (1, %s, %s, %s) RETURNING account_id",
            (account_name, account_type, balance),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return jsonify({'success': True, 'account_id': new_id}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/accounts/<int:account_id>', methods=['PUT'])
def api_update_account(account_id):
    data = request.json
    name = data.get('account_name', '').strip()
    acct_type = data.get('account_type', '').strip()
    try:
        balance = float(data.get('balance', 0))
    except (TypeError, ValueError):
        return jsonify({'error': '余额必须为数字'}), 400

    if not name:
        return jsonify({'error': '账户名称不能为空'}), 400
    if balance < 0:
        return jsonify({'error': '余额不能为负数'}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE account SET account_name=%s, account_type=%s, balance=%s
               WHERE account_id = %s""",
            (name, acct_type, balance, account_id),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({'error': '账户不存在'}), 404
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ============================================================
# 类别 API
# ============================================================
@app.route('/api/categories', methods=['GET'])
def api_categories():
    cat_type = request.args.get('type')
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if cat_type:
        cur.execute(
            """SELECT c.category_id, c.category_name, c.category_type,
                      COALESCE(pc.category_name, '') AS parent_name, c.parent_id
               FROM category c
               LEFT JOIN category pc ON c.parent_id = pc.category_id
               WHERE c.category_type = %s
               ORDER BY c.category_type, c.parent_id NULLS FIRST, c.category_id""",
            (cat_type,),
        )
    else:
        cur.execute(
            """SELECT c.category_id, c.category_name, c.category_type,
                      COALESCE(pc.category_name, '') AS parent_name, c.parent_id
               FROM category c
               LEFT JOIN category pc ON c.parent_id = pc.category_id
               ORDER BY c.category_type, c.parent_id NULLS FIRST, c.category_id"""
        )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(rows)


# ============================================================
# 交易 API
# ============================================================
@app.route('/api/transactions', methods=['GET'])
def api_transactions():
    """查询交易记录，支持筛选"""
    params = []
    conditions = []
    month = request.args.get('month')
    trans_type = request.args.get('trans_type')
    account_id = request.args.get('account_id')
    keyword = request.args.get('keyword')
    limit = int(request.args.get('limit', 100))

    if month:
        conditions.append("TO_CHAR(t.trans_date, 'YYYY-MM') = %s")
        params.append(month)
    if trans_type:
        if trans_type == '转账':
            conditions.append("(t.remark LIKE %s OR t.remark LIKE %s)")
            params.extend(['%转出%', '%转入%'])
        else:
            conditions.append("t.trans_type = %s")
            params.append(trans_type)
    if account_id:
        conditions.append("t.account_id = %s")
        params.append(int(account_id))
    category_id = request.args.get('category_id')

    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if category_id:
        # 父节点也应匹配其下级分类
        cur.execute(
            "WITH RECURSIVE cat_tree AS ("
            " SELECT category_id FROM category WHERE category_id = %s"
            " UNION ALL"
            " SELECT c.category_id FROM category c JOIN cat_tree t ON c.parent_id = t.category_id)"
            " SELECT category_id FROM cat_tree",
            (int(category_id),),
        )
        cat_rows = cur.fetchall()
        if cat_rows:
            cat_ids = [row['category_id'] for row in cat_rows]
            conditions.append("t.category_id = ANY(%s)")
            params.append(cat_ids)
        else:
            conditions.append("FALSE")
    if keyword:
        conditions.append("(c.category_name LIKE %s OR t.remark LIKE %s)")
        kw = f"%{keyword}%"
        params.extend([kw, kw])

    where = ' AND '.join(conditions) if conditions else '1=1'

    cur.execute(
        f"""SELECT t.transaction_id, t.amount, t.trans_type, t.trans_date, t.remark, t.create_time,
                   a.account_name, a.account_type,
                   c.category_name, c.category_type,
                   pc.category_name AS parent_category_name
            FROM transaction_record t
            JOIN account a ON t.account_id = a.account_id
            JOIN category c ON t.category_id = c.category_id
            LEFT JOIN category pc ON c.parent_id = pc.category_id
            WHERE {where}
            ORDER BY t.trans_date DESC, t.transaction_id DESC
            LIMIT %s""",
        params + [limit],
    )
    rows = cur.fetchall()
    for r in rows:
        r['amount'] = float(r['amount'])
        r['trans_date'] = r['trans_date'].isoformat() if hasattr(r['trans_date'], 'isoformat') else str(r['trans_date'])
        r['is_transfer'] = bool(r['remark'] and ('转出' in r['remark'] or '转入' in r['remark']))
    cur.close()
    conn.close()
    return jsonify(rows)


@app.route('/api/transactions', methods=['POST'])
def api_add_transaction():
    """新增交易"""
    data = request.json
    account_id = int(data['account_id'])
    category_id = int(data['category_id'])
    amount = float(data['amount'])
    trans_type = data['trans_type']
    trans_date = data.get('trans_date', date.today().isoformat())
    remark = data.get('remark', '')

    if amount <= 0:
        return jsonify({'error': '金额必须大于0'}), 400

    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # 校验账户余额不为负数
        cur.execute("SELECT balance FROM account WHERE account_id = %s FOR UPDATE", (account_id,))
        acct = cur.fetchone()
        if not acct:
            return jsonify({'error': '账户不存在'}), 404
        current_balance = Decimal(acct['balance'])
        if trans_type == '支出' and current_balance < Decimal(str(amount)):
            return jsonify({'error': '账户余额不足，余额不能为负数'}), 400

        # 插入交易，账户余额由数据库触发器 fn_update_balance 自动维护
        cur.execute(
            """INSERT INTO transaction_record
               (user_id, account_id, category_id, amount, trans_type, trans_date, remark)
               VALUES (1, %s, %s, %s, %s, %s, %s)
               RETURNING transaction_id""",
            (account_id, category_id, amount, trans_type, trans_date, remark),
        )
        new_id = cur.fetchone()['transaction_id']
        conn.commit()
        return jsonify({'success': True, 'transaction_id': new_id}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/transactions/<int:tid>', methods=['PUT'])
def api_update_transaction(tid):
    """修改交易"""
    data = request.json
    new_acct = int(data['account_id'])
    new_type = data['trans_type']
    new_amt = float(data['amount'])

    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # 获取旧记录
        cur.execute("SELECT account_id, amount, trans_type FROM transaction_record WHERE transaction_id = %s", (tid,))
        old = cur.fetchone()
        if not old:
            return jsonify({'error': '记录不存在'}), 404

        # 锁定相关账户并校验余额变更
        account_ids = {old['account_id'], new_acct}
        cur.execute(
            "SELECT account_id, balance FROM account WHERE account_id IN %s FOR UPDATE",
            (tuple(account_ids),),
        )
        balances = {row['account_id']: Decimal(row['balance']) for row in cur.fetchall()}
        if new_acct not in balances or old['account_id'] not in balances:
            return jsonify({'error': '账户不存在'}), 404

        # 先撤销旧记录对余额的影响
        if old['trans_type'] == '收入':
            balances[old['account_id']] -= Decimal(old['amount'])
        elif old['trans_type'] == '支出':
            balances[old['account_id']] += Decimal(old['amount'])

        # 再应用新记录对余额的影响
        if new_type == '收入':
            balances[new_acct] += Decimal(str(new_amt))
        elif new_type == '支出':
            balances[new_acct] -= Decimal(str(new_amt))

        # 余额不能为负数
        for acct_id, bal in balances.items():
            if bal < 0:
                return jsonify({'error': '账户余额不能为负数'}), 400

        # 更新记录
        cur.execute(
            """UPDATE transaction_record
               SET account_id=%s, category_id=%s, amount=%s, trans_type=%s, trans_date=%s, remark=%s
               WHERE transaction_id=%s""",
            (new_acct, data['category_id'], new_amt, new_type, data['trans_date'], data.get('remark', ''), tid),
        )

        # 交易变更后，余额由数据库触发器 fn_update_balance 自动维护
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/transactions/<int:tid>', methods=['DELETE'])
def api_delete_transaction(tid):
    """删除交易"""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # 获取旧记录
        cur.execute("SELECT account_id, amount, trans_type FROM transaction_record WHERE transaction_id = %s", (tid,))
        old = cur.fetchone()
        if old:
            # 锁定账户并校验删除后余额状态
            cur.execute("SELECT balance FROM account WHERE account_id = %s FOR UPDATE", (old['account_id'],))
            acct = cur.fetchone()
            if not acct:
                return jsonify({'error': '账户不存在'}), 404
            balance = Decimal(acct['balance'])
            if old['trans_type'] == '收入':
                balance -= Decimal(old['amount'])
                if balance < 0:
                    return jsonify({'error': '删除该收入会导致账户余额为负数'}), 400

        cur.execute("DELETE FROM transaction_record WHERE transaction_id = %s", (tid,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ============================================================
# 转账 API
# ============================================================
@app.route('/api/transfers', methods=['POST'])
def api_transfer():
    """转账（支出+收入两笔记录，事务保证原子性）"""
    data = request.json
    from_account = int(data['from_account'])
    to_account = int(data['to_account'])
    amount = float(data['amount'])
    trans_date = data.get('trans_date', date.today().isoformat())
    remark = data.get('remark', '')

    if amount <= 0:
        return jsonify({'error': '金额必须大于0'}), 400
    if from_account == to_account:
        return jsonify({'error': '转出和转入账户不能相同'}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        # 查询账户余额并校验转账条件
        cur.execute("SELECT account_id, balance FROM account WHERE account_id IN (%s, %s) FOR UPDATE", (from_account, to_account))
        accounts = {row[0]: Decimal(row[1]) for row in cur.fetchall()}
        if from_account not in accounts or to_account not in accounts:
            return jsonify({'error': '转账账户不存在'}), 400
        if accounts[from_account] < Decimal(str(amount)):
            return jsonify({'error': '账户余额不足'}), 400

        # 查找默认类别
        cur.execute(
            "SELECT category_id FROM category WHERE category_name='其他支出' AND category_type='支出' LIMIT 1"
        )
        exp_cat = cur.fetchone()
        cur.execute(
            "SELECT category_id FROM category WHERE category_name='其他收入' AND category_type='收入' LIMIT 1"
        )
        inc_cat = cur.fetchone()

        if not exp_cat or not inc_cat:
            raise Exception("缺少默认转账类别（其他支出/其他收入）")

        # 1. 转出记录
        cur.execute(
            """INSERT INTO transaction_record
               (user_id, account_id, category_id, amount, trans_type, trans_date, remark)
               VALUES (1, %s, %s, %s, '支出', %s, %s)""",
            (from_account, exp_cat[0], amount, trans_date,
             (remark or '转账至账户#' + str(to_account)) + '（转出）'),
        )
        # 2. 转入记录
        cur.execute(
            """INSERT INTO transaction_record
               (user_id, account_id, category_id, amount, trans_type, trans_date, remark)
               VALUES (1, %s, %s, %s, '收入', %s, %s)""",
            (to_account, inc_cat[0], amount, trans_date,
             (remark or '来自账户#' + str(from_account)) + '（转入）'),
        )

        # 转账记录插入后，余额由数据库触发器 fn_update_balance 自动维护
        conn.commit()
        return jsonify({'success': True}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ============================================================
# 概览统计 API
# ============================================================
@app.route('/api/overview', methods=['GET'])
def api_overview():
    """总览统计数据"""
    today = date.today().isoformat()
    month = today[:7]
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # 本月收支
    cur.execute(
        """SELECT
             COALESCE(SUM(CASE WHEN trans_type='收入' THEN amount ELSE 0 END), 0) AS income,
             COALESCE(SUM(CASE WHEN trans_type='支出' THEN amount ELSE 0 END), 0) AS expense,
             COUNT(*) AS total_count
           FROM transaction_record
           WHERE TO_CHAR(trans_date, 'YYYY-MM') = %s""",
        (month,),
    )
    monthly = cur.fetchone()

    # 今日收支
    cur.execute(
        """SELECT
             COALESCE(SUM(CASE WHEN trans_type='收入' THEN amount ELSE 0 END), 0) AS income,
             COALESCE(SUM(CASE WHEN trans_type='支出' THEN amount ELSE 0 END), 0) AS expense
           FROM transaction_record
           WHERE trans_date = %s""",
        (today,),
    )
    daily = cur.fetchone()

    # 累计
    cur.execute(
        """SELECT
             COALESCE(SUM(CASE WHEN trans_type='收入' THEN amount ELSE 0 END), 0) AS income,
             COALESCE(SUM(CASE WHEN trans_type='支出' THEN amount ELSE 0 END), 0) AS expense,
             COUNT(*) AS total_count
           FROM transaction_record"""
    )
    total = cur.fetchone()

    # 净资产：资产-负债+账户余额
    cur.execute("""
        SELECT
            COALESCE((SELECT SUM(amount) FROM asset_liability WHERE item_type='资产'), 0)
            - COALESCE((SELECT SUM(amount) FROM asset_liability WHERE item_type='负债'), 0)
            + COALESCE((SELECT SUM(balance) FROM account), 0) AS net_worth
    """)
    nw = cur.fetchone()

    # 日均（本月）
    cur.execute(
        """SELECT
             COALESCE(SUM(CASE WHEN trans_type='支出' THEN amount ELSE 0 END), 0)
             / NULLIF(COUNT(DISTINCT trans_date), 0) AS daily_avg
           FROM transaction_record
           WHERE TO_CHAR(trans_date, 'YYYY-MM') = %s""",
        (month,),
    )
    avg = cur.fetchone()

    cur.close()
    conn.close()

    return jsonify({
        'month_income': float(monthly['income']),
        'month_expense': float(monthly['expense']),
        'month_count': monthly['total_count'],
        'today_income': float(daily['income']),
        'today_expense': float(daily['expense']),
        'total_income': float(total['income']),
        'total_expense': float(total['expense']),
        'total_count': total['total_count'],
        'net_worth': float(nw['net_worth']) if nw['net_worth'] else 0,
        'daily_avg': float(avg['daily_avg']) if avg['daily_avg'] else 0,
        'month': month,
        'today': today,
    })


# ============================================================
# 类别消费占比 API
# ============================================================
@app.route('/api/category-ratio', methods=['GET'])
def api_category_ratio():
    month = request.args.get('month', date.today().isoformat()[:7])
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT category_name, expense_amount, expense_ratio FROM v_category_expense_ratio WHERE year_month = %s",
        (month,),
    )
    rows = cur.fetchall()
    for r in rows:
        r['expense_amount'] = float(r['expense_amount'])
        r['expense_ratio'] = float(r['expense_ratio'])
    cur.close()
    conn.close()
    return jsonify(rows)


# ============================================================
# 预算 API
# ============================================================
@app.route('/api/budgets', methods=['GET'])
def api_budgets():
    month = request.args.get('month', date.today().isoformat()[:7])
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """SELECT b.budget_id, c.category_name, b.year_month, b.budget_amount
           FROM budget b JOIN category c ON b.category_id = c.category_id
           WHERE b.year_month = %s
           ORDER BY b.budget_amount DESC""",
        (month,),
    )
    rows = cur.fetchall()
    for r in rows:
        r['budget_amount'] = float(r['budget_amount'])
    cur.close()
    conn.close()
    return jsonify(rows)


@app.route('/api/budgets', methods=['POST'])
def api_set_budget():
    data = request.json
    category_id = int(data['category_id'])
    year_month = data['year_month']
    amount = float(data['amount'])
    conn = get_db()
    cur = conn.cursor()
    try:
        # 先尝试更新，若不存在则插入
        cur.execute(
            """UPDATE budget SET budget_amount = %s
               WHERE user_id = 1 AND category_id = %s AND year_month = %s""",
            (amount, category_id, year_month),
        )
        if cur.rowcount == 0:
            cur.execute(
                """INSERT INTO budget (user_id, category_id, year_month, budget_amount)
                   VALUES (1, %s, %s, %s)""",
                (category_id, year_month, amount),
            )
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/budget-status', methods=['GET'])
def api_budget_status():
    month = request.args.get('month', date.today().isoformat()[:7])
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # 各类别预算 + 实际支出
    cur.execute(
        """SELECT b.budget_id, c.category_name, b.budget_amount,
                  COALESCE((
                    SELECT SUM(t.amount) FROM transaction_record t
                    WHERE t.category_id = b.category_id AND t.trans_type = '支出'
                    AND TO_CHAR(t.trans_date, 'YYYY-MM') = b.year_month
                  ), 0) AS actual_expense
           FROM budget b
           JOIN category c ON b.category_id = c.category_id
           WHERE b.year_month = %s
           ORDER BY b.budget_amount DESC""",
        (month,),
    )
    rows = cur.fetchall()
    for r in rows:
        r['budget_amount'] = float(r['budget_amount'])
        r['actual_expense'] = float(r['actual_expense'])
        pct = (r['actual_expense'] / r['budget_amount'] * 100) if r['budget_amount'] > 0 else 0
        r['completion_rate'] = round(pct, 1)
    cur.close()
    conn.close()
    return jsonify(rows)


# ============================================================
# 资产负债 API
# ============================================================
@app.route('/api/asset-liability', methods=['GET'])
def api_asset_liability():
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT item_id, item_name, item_type, amount, acquire_date, remark FROM asset_liability WHERE user_id = 1 ORDER BY item_type, amount DESC"
    )
    rows = cur.fetchall()
    for r in rows:
        r['amount'] = float(r['amount'])
        r['acquire_date'] = r['acquire_date'].isoformat() if hasattr(r['acquire_date'], 'isoformat') else str(r['acquire_date'])
    cur.close()
    conn.close()
    return jsonify(rows)


@app.route('/api/asset-liability/<int:item_id>', methods=['PUT'])
def api_update_asset_liability(item_id):
    data = request.json
    item_type = data.get('item_type', '').strip()
    item_name = data.get('item_name', '').strip()
    try:
        amount = float(data.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({'error': '金额必须为数字'}), 400
    acquire_date = data.get('acquire_date', '').strip()
    remark = data.get('remark', '').strip()

    if item_type not in ('资产', '负债'):
        return jsonify({'error': '类型必须是资产或负债'}), 400
    if not item_name:
        return jsonify({'error': '名称不能为空'}), 400
    if amount < 0:
        return jsonify({'error': '金额不能为负数'}), 400
    if not acquire_date:
        return jsonify({'error': '日期不能为空'}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE asset_liability
               SET item_type=%s, item_name=%s, amount=%s, acquire_date=%s, remark=%s
               WHERE item_id = %s AND user_id = 1""",
            (item_type, item_name, amount, acquire_date, remark, item_id),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({'error': '资产/负债项不存在'}), 404
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/asset-liability', methods=['POST'])
def api_add_asset_liability():
    data = request.json
    item_type = data.get('item_type', '').strip()
    item_name = data.get('item_name', '').strip()
    try:
        amount = float(data.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({'error': '金额必须为数字'}), 400
    acquire_date = data.get('acquire_date', '').strip()
    remark = data.get('remark', '').strip()

    if item_type not in ('资产', '负债'):
        return jsonify({'error': '类型必须是资产或负债'}), 400
    if not item_name:
        return jsonify({'error': '名称不能为空'}), 400
    if amount < 0:
        return jsonify({'error': '金额不能为负数'}), 400
    if not acquire_date:
        return jsonify({'error': '日期不能为空'}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO asset_liability (user_id, item_type, item_name, amount, acquire_date, remark) VALUES (1, %s, %s, %s, %s, %s) RETURNING item_id",
            (item_type, item_name, amount, acquire_date, remark),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return jsonify({'success': True, 'item_id': new_id}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/accounts/<int:account_id>', methods=['DELETE'])
def api_delete_account(account_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        # 先删除与账户相关的交易记录，避免外键约束冲突
        cur.execute("DELETE FROM transaction_record WHERE account_id = %s", (account_id,))
        cur.execute("DELETE FROM account WHERE account_id = %s", (account_id,))
        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({'error': '账户不存在'}), 404
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/asset-liability/<int:item_id>', methods=['DELETE'])
def api_delete_asset_liability(item_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM asset_liability WHERE item_id = %s", (item_id,))
        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({'error': '资产/负债项不存在'}), 404
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/clear-data', methods=['DELETE'])
@app.route('/api/clear-data', methods=['DELETE'])
def api_clear_data():
    conn = get_db()
    cur = conn.cursor()
    try:
        # 这里按你的数据结构清空核心表
        cur.execute("DELETE FROM transaction_record")
        cur.execute("DELETE FROM budget")
        cur.execute("DELETE FROM asset_liability")
        cur.execute("DELETE FROM account")
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/net-worth', methods=['GET'])
def api_net_worth():
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """SELECT
             (SELECT COALESCE(SUM(amount),0) FROM asset_liability WHERE item_type='资产') AS assets,
             (SELECT COALESCE(SUM(amount),0) FROM asset_liability WHERE item_type='负债') AS liabilities,
             (SELECT COALESCE(SUM(balance),0) FROM account) AS account_balance"""
    )
    row = cur.fetchone()
    assets = float(row['assets'])
    liabilities = float(row['liabilities'])
    balance = float(row['account_balance'])
    cur.close()
    conn.close()
    return jsonify({
        'assets': assets,
        'liabilities': liabilities,
        'account_balance': balance,
        'net_worth': assets - liabilities + balance,
    })


# ============================================================
# 月度收支趋势 API
# ============================================================
@app.route('/api/monthly-trend', methods=['GET'])
def api_monthly_trend():
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """SELECT TO_CHAR(trans_date, 'YYYY-MM') AS ym,
                  COALESCE(SUM(CASE WHEN trans_type='收入' THEN amount ELSE 0 END), 0) AS income,
                  COALESCE(SUM(CASE WHEN trans_type='支出' THEN amount ELSE 0 END), 0) AS expense
           FROM transaction_record
           GROUP BY TO_CHAR(trans_date, 'YYYY-MM')
           ORDER BY ym"""
    )
    rows = cur.fetchall()
    for r in rows:
        r['income'] = float(r['income'])
        r['expense'] = float(r['expense'])
    cur.close()
    conn.close()
    return jsonify(rows)


# ============================================================
# 日现金流 API（近30天）
# ============================================================
@app.route('/api/daily-cashflow', methods=['GET'])
def api_daily_cashflow():
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM v_cash_flow_trend ORDER BY trans_date DESC LIMIT 30")
    rows = cur.fetchall()
    for r in rows:
        r['daily_income'] = float(r['daily_income'])
        r['daily_expense'] = float(r['daily_expense'])
        r['net_cashflow'] = float(r['net_cashflow'])
        r['trans_date'] = r['trans_date'].isoformat() if hasattr(r['trans_date'], 'isoformat') else str(r['trans_date'])
    cur.close()
    conn.close()
    return jsonify(rows)


# ============================================================
# 启动
# ============================================================
if __name__ == '__main__':
    print("🚀 个人理财管理系统 Web 后端启动中...")
    print(f"   访问地址: http://localhost:5000")
    app.run(debug=True, host='0.0.0.0', port=5000)
