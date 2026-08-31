-- ============================================================
-- 个人理财管理系统 — 视图脚本
-- 目标DBMS：GaussDB (PostgreSQL 兼容)
-- 执行顺序：第5步（04_create_indexes.sql 之后）
-- ============================================================

-- ------------------------------------
-- 1. v_transaction_detail — 交易明细视图
-- 关联账户和类别名称，方便用户查看
-- ------------------------------------
CREATE OR REPLACE VIEW v_transaction_detail AS
SELECT
    t.transaction_id,
    u.user_name,
    a.account_name,
    c.category_name,
    pc.category_name AS parent_category_name,
    t.amount,
    t.trans_type,
    t.trans_date,
    t.remark,
    t.create_time
FROM transaction_record t
JOIN user_info  u ON t.user_id = u.user_id
JOIN account    a ON t.account_id = a.account_id
JOIN category   c ON t.category_id = c.category_id
LEFT JOIN category pc ON c.parent_id = pc.category_id
ORDER BY t.trans_date DESC, t.transaction_id DESC;

COMMENT ON VIEW v_transaction_detail IS '交易明细视图：显示账户名、类别名（含父类别）等可读信息';

-- ------------------------------------
-- 2. v_account_balances — 账户余额一览
-- ------------------------------------
CREATE OR REPLACE VIEW v_account_balances AS
SELECT
    account_id,
    account_name,
    account_type,
    balance,
    CASE account_type
        WHEN '现金'     THEN '💰 现金'
        WHEN '银行卡'   THEN '🏦 银行卡'
        WHEN '电子钱包' THEN '📱 电子钱包'
        WHEN '投资账户' THEN '📈 投资账户'
    END AS type_icon
FROM account
ORDER BY balance DESC;

COMMENT ON VIEW v_account_balances IS '账户余额汇总视图';

-- ------------------------------------
-- 3. v_monthly_income_expense — 月度收支汇总
-- ------------------------------------
CREATE OR REPLACE VIEW v_monthly_income_expense AS
SELECT
    TO_CHAR(trans_date, 'YYYY-MM') AS year_month,
    COALESCE(SUM(CASE WHEN trans_type = '收入' THEN amount ELSE 0 END), 0) AS total_income,
    COALESCE(SUM(CASE WHEN trans_type = '支出' THEN amount ELSE 0 END), 0) AS total_expense,
    COALESCE(SUM(CASE WHEN trans_type = '收入' THEN amount ELSE -amount END), 0) AS net_balance
FROM transaction_record
GROUP BY TO_CHAR(trans_date, 'YYYY-MM')
ORDER BY year_month;

COMMENT ON VIEW v_monthly_income_expense IS '月度收支汇总视图：按月统计收入、支出和结余';

-- ------------------------------------
-- 4. v_category_expense_ratio — 类别支出占比
-- 统计当月的各类别支出金额和占比
-- ------------------------------------
CREATE OR REPLACE VIEW v_category_expense_ratio AS
WITH monthly_expense AS (
    SELECT
        TO_CHAR(trans_date, 'YYYY-MM') AS year_month,
        SUM(amount) AS total
    FROM transaction_record
    WHERE trans_type = '支出'
    GROUP BY TO_CHAR(trans_date, 'YYYY-MM')
)
SELECT
    TO_CHAR(t.trans_date, 'YYYY-MM') AS year_month,
    c.category_id,
    c.category_name,
    SUM(t.amount) AS expense_amount,
    ROUND(
        SUM(t.amount) * 100.0 / me.total,
        2
    ) AS expense_ratio
FROM transaction_record t
JOIN category c ON t.category_id = c.category_id
JOIN monthly_expense me ON TO_CHAR(t.trans_date, 'YYYY-MM') = me.year_month
WHERE t.trans_type = '支出'
GROUP BY TO_CHAR(t.trans_date, 'YYYY-MM'), c.category_id, c.category_name, me.total
ORDER BY year_month DESC, expense_amount DESC;

COMMENT ON VIEW v_category_expense_ratio IS '各类别支出占比视图：按月份统计各类别消费金额和百分比';

-- ------------------------------------
-- 5. v_budget_status — 预算执行情况
-- 关联预算表和实际支出，计算完成率
-- ------------------------------------
CREATE OR REPLACE VIEW v_budget_status AS
SELECT
    b.year_month,
    c.category_name,
    b.budget_amount,
    COALESCE(
        (SELECT SUM(t.amount)
         FROM transaction_record t
         WHERE t.category_id = b.category_id
           AND t.trans_type = '支出'
           AND TO_CHAR(t.trans_date, 'YYYY-MM') = b.year_month),
        0
    ) AS actual_expense,
    ROUND(
        COALESCE(
            (SELECT SUM(t.amount)
             FROM transaction_record t
             WHERE t.category_id = b.category_id
               AND t.trans_type = '支出'
               AND TO_CHAR(t.trans_date, 'YYYY-MM') = b.year_month),
            0
        ) * 100.0 / b.budget_amount,
        1
    ) AS completion_rate,
    CASE
        WHEN COALESCE(
            (SELECT SUM(t.amount)
             FROM transaction_record t
             WHERE t.category_id = b.category_id
               AND t.trans_type = '支出'
               AND TO_CHAR(t.trans_date, 'YYYY-MM') = b.year_month),
            0
        ) > b.budget_amount THEN '超支 ⚠️'
        WHEN COALESCE(
            (SELECT SUM(t.amount)
             FROM transaction_record t
             WHERE t.category_id = b.category_id
               AND t.trans_type = '支出'
               AND TO_CHAR(t.trans_date, 'YYYY-MM') = b.year_month),
            0
        ) >= b.budget_amount * 0.9 THEN '接近预算 ⚡'
        ELSE '正常 ✅'
    END AS status
FROM budget b
JOIN category c ON b.category_id = c.category_id
ORDER BY b.year_month DESC, completion_rate DESC;

COMMENT ON VIEW v_budget_status IS '预算执行情况视图：展示各类别的预算金额、实际支出和完成率';

-- ------------------------------------
-- 6. v_cash_flow_trend — 现金流趋势（按日）
-- ------------------------------------
CREATE OR REPLACE VIEW v_cash_flow_trend AS
SELECT
    trans_date,
    COALESCE(SUM(CASE WHEN trans_type = '收入' THEN amount ELSE 0 END), 0) AS daily_income,
    COALESCE(SUM(CASE WHEN trans_type = '支出' THEN amount ELSE 0 END), 0) AS daily_expense,
    COALESCE(SUM(CASE WHEN trans_type = '收入' THEN amount ELSE -amount END), 0) AS net_cashflow
FROM transaction_record
GROUP BY trans_date
ORDER BY trans_date;

COMMENT ON VIEW v_cash_flow_trend IS '日现金流趋势视图：按日统计收入、支出和净现金流';

-- ------------------------------------
-- 7. v_net_worth — 净资产视图
-- ------------------------------------
CREATE OR REPLACE VIEW v_net_worth AS
SELECT
    (SELECT COALESCE(SUM(amount), 0) FROM asset_liability WHERE item_type = '资产')   AS total_assets,
    (SELECT COALESCE(SUM(amount), 0) FROM asset_liability WHERE item_type = '负债')   AS total_liabilities,
    (SELECT COALESCE(SUM(balance), 0) FROM account)                                   AS total_account_balance,
    (SELECT COALESCE(SUM(amount), 0) FROM asset_liability WHERE item_type = '资产')
    - (SELECT COALESCE(SUM(amount), 0) FROM asset_liability WHERE item_type = '负债')
    + (SELECT COALESCE(SUM(balance), 0) FROM account)                                 AS net_worth;

COMMENT ON VIEW v_net_worth IS '净资产视图：资产总额 - 负债总额 + 账户总余额';

-- 视图创建完成
