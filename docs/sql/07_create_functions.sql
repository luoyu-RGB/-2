-- ============================================================
-- 个人理财管理系统 — 存储过程/函数脚本
-- 目标DBMS：GaussDB (PostgreSQL 兼容)
-- 执行顺序：第7步（06_create_triggers.sql 之后）
-- ============================================================

-- ------------------------------------
-- 1. sp_add_transfer — 转账存储过程
-- 功能：在一个事务中完成转账（A账户支出 + B账户收入）
-- 参数：
--   p_user_id        用户ID
--   p_from_account   转出账户ID
--   p_to_account     转入账户ID
--   p_amount         转账金额
--   p_trans_date     交易日期
--   p_remark         备注
-- ------------------------------------
CREATE OR REPLACE PROCEDURE sp_add_transfer(
    p_user_id       INT,
    p_from_account  INT,
    p_to_account    INT,
    p_amount        DECIMAL(12,2),
    p_trans_date    DATE DEFAULT CURRENT_DATE,
    p_remark        VARCHAR(200) DEFAULT NULL
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_expense_category_id INT;
    v_income_category_id  INT;
BEGIN
    -- 查找或使用默认类别
    -- 转出记录归类为"其他支出"
    SELECT category_id INTO v_expense_category_id
    FROM category
    WHERE user_id = p_user_id AND category_name = '其他支出' AND category_type = '支出'
    LIMIT 1;

    -- 转入记录归类为"其他收入"
    SELECT category_id INTO v_income_category_id
    FROM category
    WHERE user_id = p_user_id AND category_name = '其他收入' AND category_type = '收入'
    LIMIT 1;

    -- 开始事务
    BEGIN
        -- 1. 插入转出记录（支出）
        INSERT INTO transaction_record (user_id, account_id, category_id, amount, trans_type, trans_date, remark)
        VALUES (p_user_id, p_from_account, v_expense_category_id, p_amount, '支出', p_trans_date,
                COALESCE(p_remark, '转账至账户#' || p_to_account) || '（转出）');

        -- 2. 插入转入记录（收入）
        INSERT INTO transaction_record (user_id, account_id, category_id, amount, trans_type, trans_date, remark)
        VALUES (p_user_id, p_to_account, v_income_category_id, p_amount, '收入', p_trans_date,
                COALESCE(p_remark, '来自账户#' || p_from_account) || '（转入）');

        -- 3. 触发器会自动更新两个账户的余额
    EXCEPTION
        WHEN OTHERS THEN
            RAISE EXCEPTION '转账失败: %', SQLERRM;
    END;
END;
$$;

COMMENT ON PROCEDURE sp_add_transfer(INT, INT, INT, DECIMAL, DATE, VARCHAR)
    IS '转账存储过程：在单个事务中完成转出支出+转入收入两笔记录，保证原子性';

-- ------------------------------------
-- 2. fn_monthly_report — 月度报表函数
-- 功能：返回指定用户指定月份的收支汇总
-- 参数：
--   p_user_id    用户ID
--   p_year_month 年月（YYYY-MM格式）
-- 返回：收入总额、支出总额、结余
-- ------------------------------------
CREATE OR REPLACE FUNCTION fn_monthly_report(
    p_user_id   INT,
    p_year_month VARCHAR(7)
)
RETURNS TABLE(
    total_income  DECIMAL(12,2),
    total_expense DECIMAL(12,2),
    net_balance   DECIMAL(12,2)
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        COALESCE(SUM(CASE WHEN trans_type = '收入' THEN amount ELSE 0 END), 0) AS total_income,
        COALESCE(SUM(CASE WHEN trans_type = '支出' THEN amount ELSE 0 END), 0) AS total_expense,
        COALESCE(SUM(CASE WHEN trans_type = '收入' THEN amount ELSE -amount END), 0) AS net_balance
    FROM transaction_record
    WHERE user_id = p_user_id
      AND TO_CHAR(trans_date, 'YYYY-MM') = p_year_month;
END;
$$;

COMMENT ON FUNCTION fn_monthly_report(INT, VARCHAR) IS '月度报表函数：返回指定月份的收支汇总';

-- ------------------------------------
-- 3. fn_category_spending — 类别消费统计函数
-- 功能：返回指定时间范围内各类别的支出金额和占比
-- 参数：
--   p_user_id   用户ID
--   p_start     开始日期
--   p_end       结束日期
-- ------------------------------------
CREATE OR REPLACE FUNCTION fn_category_spending(
    p_user_id INT,
    p_start   DATE,
    p_end     DATE
)
RETURNS TABLE(
    category_name   VARCHAR(50),
    expense_amount  DECIMAL(12,2),
    expense_ratio   NUMERIC(5,2)
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_total DECIMAL(12,2);
BEGIN
    -- 计算总支出
    SELECT COALESCE(SUM(amount), 0) INTO v_total
    FROM transaction_record
    WHERE user_id = p_user_id
      AND trans_type = '支出'
      AND trans_date BETWEEN p_start AND p_end;

    -- 返回各类别统计
    RETURN QUERY
    SELECT
        c.category_name,
        SUM(t.amount)::DECIMAL(12,2),
        CASE
            WHEN v_total > 0 THEN ROUND(SUM(t.amount) * 100.0 / v_total, 2)
            ELSE 0
        END
    FROM transaction_record t
    JOIN category c ON t.category_id = c.category_id
    WHERE t.user_id = p_user_id
      AND t.trans_type = '支出'
      AND t.trans_date BETWEEN p_start AND p_end
    GROUP BY c.category_name
    ORDER BY SUM(t.amount) DESC;
END;
$$;

COMMENT ON FUNCTION fn_category_spending(INT, DATE, DATE) IS '类别消费统计函数：返回指定时间范围的各类别支出金额和占比';

-- ------------------------------------
-- 4. fn_net_worth — 净资产计算函数
-- 功能：计算用户的净资产（资产-负债+账户余额）
-- 参数：p_user_id 用户ID
-- ------------------------------------
CREATE OR REPLACE FUNCTION fn_net_worth(p_user_id INT)
RETURNS DECIMAL(12,2)
LANGUAGE plpgsql
AS $$
DECLARE
    v_assets      DECIMAL(12,2);
    v_liabilities DECIMAL(12,2);
    v_balance     DECIMAL(12,2);
BEGIN
    SELECT COALESCE(SUM(amount), 0) INTO v_assets
    FROM asset_liability WHERE user_id = p_user_id AND item_type = '资产';

    SELECT COALESCE(SUM(amount), 0) INTO v_liabilities
    FROM asset_liability WHERE user_id = p_user_id AND item_type = '负债';

    SELECT COALESCE(SUM(balance), 0) INTO v_balance
    FROM account WHERE user_id = p_user_id;

    RETURN v_assets - v_liabilities + v_balance;
END;
$$;

COMMENT ON FUNCTION fn_net_worth(INT) IS '净资产计算函数：资产总额-负债总额+账户总余额';

-- ------------------------------------
-- 5. sp_copy_budget — 批量复制预算存储过程
-- 功能：将某月的预算复制到目标月份（快速设置新月份预算）
-- 参数：
--   p_user_id          用户ID
--   p_source_month     源年月
--   p_target_month     目标年月
-- ------------------------------------
CREATE OR REPLACE PROCEDURE sp_copy_budget(
    p_user_id       INT,
    p_source_month  VARCHAR(7),
    p_target_month  VARCHAR(7)
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_count INT;
BEGIN
    INSERT INTO budget (user_id, category_id, year_month, budget_amount)
    SELECT
        user_id,
        category_id,
        p_target_month,
        budget_amount
    FROM budget
    WHERE user_id = p_user_id
      AND year_month = p_source_month
    ON CONFLICT (user_id, category_id, year_month)
    DO UPDATE SET budget_amount = EXCLUDED.budget_amount;

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE '已将 % 条预算从 % 复制到 %', v_count, p_source_month, p_target_month;
END;
$$;

COMMENT ON PROCEDURE sp_copy_budget(INT, VARCHAR, VARCHAR) IS '批量复制预算：将指定月份的预算复制到另一个月份';

-- 函数创建完成
