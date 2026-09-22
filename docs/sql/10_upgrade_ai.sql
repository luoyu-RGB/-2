-- ============================================================
-- 个人理财助手 — AI 增强升级脚本（针对 GaussDB / PostgreSQL）
-- 执行顺序：第10步（02~08 之后执行，可重复执行）
--
-- 设计取舍：01~08 是课程设计的原始交付物，保持不动；
--           本脚本以"增量升级"的方式修掉已知设计问题并补上 AI 所需结构，
--           因此设计说明书里的表结构描述依然成立，只需追加本脚本的说明。
--
-- 本脚本修复/新增：
--   [1] transaction_record 增加 transfer_group_id —— 转账不再靠时间差+备注配对
--   [2] 修正触发器 fn_update_balance 的 UPDATE 分支与余额预检
--   [3] 重写 sp_add_transfer：账户归属校验、禁止同账户、写入 transfer_group_id
--   [4] 全部视图补 user_id 维度（原来多用户会串数据），并去掉视图内 ORDER BY
--   [5] budget.year_month 增加格式约束
--   [6] 新增 ai_action_log 审计表（AI 写操作留痕）
--   [7] 新增 knowledge_document / knowledge_chunk 知识库表
--   [8] 补充索引（transfer_group_id、审计、知识库）
-- ============================================================

-- ============================================================
-- [1] 转账分组字段
-- ============================================================
ALTER TABLE transaction_record
    ADD COLUMN IF NOT EXISTS transfer_group_id VARCHAR(36) DEFAULT NULL;

COMMENT ON COLUMN transaction_record.transfer_group_id
    IS '转账分组ID：同一笔转账的转出与转入两条记录共享该值';

-- 说明：trans_type 的枚举里保留了 '转账'，但余额维护只处理 '收入'/'支出'，
-- 因此实际转账一律写成"支出 + 收入"两条记录（见 sp_add_transfer），
-- 这一点在 02_create_tables.sql 的注释中也应同步说明。

-- ============================================================
-- [5] 预算年月格式约束
-- ============================================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_budget_year_month_format'
    ) THEN
        ALTER TABLE budget
            ADD CONSTRAINT ck_budget_year_month_format
            CHECK (year_month ~ '^\d{4}-(0[1-9]|1[0-2])$');
    END IF;
END $$;

-- ============================================================
-- [2] 触发器函数：余额唯一维护者
--     修复点：
--       a) UPDATE 分支先撤销旧值、再对新值做余额预检（原来不检查新值）
--       b) 禁止修改 user_id，避免把交易挪到别人账下
--       c) 支出预检给出中文业务错误，而不是等 CHECK 约束报错
-- ============================================================
CREATE OR REPLACE FUNCTION fn_update_balance()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.trans_type = '收入' THEN
            UPDATE account SET balance = balance + NEW.amount
             WHERE account_id = NEW.account_id;
        ELSIF NEW.trans_type = '支出' THEN
            PERFORM 1 FROM account
             WHERE account_id = NEW.account_id AND balance >= NEW.amount;
            IF NOT FOUND THEN
                RAISE EXCEPTION '账户余额不足：本次支出会导致账户余额为负数（账户ID: %）', NEW.account_id;
            END IF;
            UPDATE account SET balance = balance - NEW.amount
             WHERE account_id = NEW.account_id;
        END IF;

    ELSIF TG_OP = 'DELETE' THEN
        IF OLD.trans_type = '收入' THEN
            PERFORM 1 FROM account
             WHERE account_id = OLD.account_id AND balance >= OLD.amount;
            IF NOT FOUND THEN
                RAISE EXCEPTION '删除该收入会导致账户余额为负数（账户ID: %）', OLD.account_id;
            END IF;
            UPDATE account SET balance = balance - OLD.amount
             WHERE account_id = OLD.account_id;
        ELSIF OLD.trans_type = '支出' THEN
            UPDATE account SET balance = balance + OLD.amount
             WHERE account_id = OLD.account_id;
        END IF;

    ELSIF TG_OP = 'UPDATE' THEN
        -- 不允许把交易改到别的用户名下
        IF NEW.user_id <> OLD.user_id THEN
            RAISE EXCEPTION '不允许修改交易记录的所属用户';
        END IF;

        -- 撤销旧值
        IF OLD.trans_type = '收入' THEN
            UPDATE account SET balance = balance - OLD.amount
             WHERE account_id = OLD.account_id;
        ELSIF OLD.trans_type = '支出' THEN
            UPDATE account SET balance = balance + OLD.amount
             WHERE account_id = OLD.account_id;
        END IF;

        -- 应用新值（前先预检，覆盖改账户/改金额/改方向）
        IF NEW.trans_type = '收入' THEN
            UPDATE account SET balance = balance + NEW.amount
             WHERE account_id = NEW.account_id;
        ELSIF NEW.trans_type = '支出' THEN
            PERFORM 1 FROM account
             WHERE account_id = NEW.account_id AND balance >= NEW.amount;
            IF NOT FOUND THEN
                RAISE EXCEPTION '账户余额不足：本次修改会导致账户余额为负数（账户ID: %）', NEW.account_id;
            END IF;
            UPDATE account SET balance = balance - NEW.amount
             WHERE account_id = NEW.account_id;
        END IF;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION fn_update_balance() IS '账户余额维护函数（升级版）：增删改交易时自动维护余额并做余额预检';

-- ============================================================
-- [3] 转账存储过程（新签名，多了 transfer_group 参数）
--     修复点：
--       a) 校验两个账户都存在且属于该用户
--       b) 禁止同账户转账
--       c) 写入 transfer_group_id，使转账可被确定性识别
--       d) 校验默认类别存在，否则报明确错误
-- ============================================================
DROP PROCEDURE IF EXISTS sp_add_transfer(INT, INT, INT, DECIMAL, DATE, VARCHAR);

CREATE OR REPLACE PROCEDURE sp_add_transfer(
    p_user_id        INT,
    p_from_account   INT,
    p_to_account     INT,
    p_amount         DECIMAL(12,2),
    p_transfer_group VARCHAR(36),
    p_trans_date     DATE DEFAULT CURRENT_DATE,
    p_remark         VARCHAR(200) DEFAULT NULL
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_expense_category_id INT;
    v_income_category_id  INT;
BEGIN
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION '转账金额必须大于 0';
    END IF;
    IF p_from_account = p_to_account THEN
        RAISE EXCEPTION '转出与转入账户不能相同';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM account WHERE account_id = p_from_account AND user_id = p_user_id) THEN
        RAISE EXCEPTION '转出账户不存在或不属于当前用户（账户ID: %）', p_from_account;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM account WHERE account_id = p_to_account AND user_id = p_user_id) THEN
        RAISE EXCEPTION '转入账户不存在或不属于当前用户（账户ID: %）', p_to_account;
    END IF;

    SELECT category_id INTO v_expense_category_id
      FROM category
     WHERE user_id = p_user_id AND category_name = '其他支出' AND category_type = '支出'
     LIMIT 1;

    SELECT category_id INTO v_income_category_id
      FROM category
     WHERE user_id = p_user_id AND category_name = '其他收入' AND category_type = '收入'
     LIMIT 1;

    IF v_expense_category_id IS NULL OR v_income_category_id IS NULL THEN
        RAISE EXCEPTION '缺少默认转账类别（其他支出 / 其他收入），请先初始化类别数据';
    END IF;

    -- 转出：触发器在此校验余额，不足则整个存储过程回滚
    INSERT INTO transaction_record
        (user_id, account_id, category_id, amount, trans_type, trans_date, remark, transfer_group_id)
    VALUES
        (p_user_id, p_from_account, v_expense_category_id, p_amount, '支出', p_trans_date,
         COALESCE(NULLIF(p_remark, ''), '账户间转账') || '（转出）', p_transfer_group);

    INSERT INTO transaction_record
        (user_id, account_id, category_id, amount, trans_type, trans_date, remark, transfer_group_id)
    VALUES
        (p_user_id, p_to_account, v_income_category_id, p_amount, '收入', p_trans_date,
         COALESCE(NULLIF(p_remark, ''), '账户间转账') || '（转入）', p_transfer_group);
END;
$$;

COMMENT ON PROCEDURE sp_add_transfer(INT, INT, INT, DECIMAL, VARCHAR, DATE, VARCHAR)
    IS '转账存储过程（升级版）：校验账户归属、写入 transfer_group_id，余额由触发器维护';

-- ============================================================
-- [4] 视图：全部补 user_id 维度，去掉视图内 ORDER BY
--     列顺序有变化，因此必须先 DROP 再建
-- ============================================================
DROP VIEW IF EXISTS v_transaction_detail CASCADE;
CREATE VIEW v_transaction_detail AS
SELECT
    t.transaction_id,
    t.user_id,
    u.user_name,
    t.account_id,
    a.account_name,
    t.category_id,
    c.category_name,
    pc.category_name AS parent_category_name,
    t.amount,
    t.trans_type,
    t.trans_date,
    t.remark,
    t.transfer_group_id,
    t.create_time
FROM transaction_record t
JOIN user_info u ON t.user_id = u.user_id
JOIN account   a ON t.account_id = a.account_id
JOIN category  c ON t.category_id = c.category_id
LEFT JOIN category pc ON c.parent_id = pc.category_id;

COMMENT ON VIEW v_transaction_detail IS '交易明细视图（含 user_id 维度，便于多用户隔离）';

DROP VIEW IF EXISTS v_account_balances CASCADE;
CREATE VIEW v_account_balances AS
SELECT account_id, user_id, account_name, account_type, balance,
       CASE account_type
           WHEN '现金'     THEN '💰 现金'
           WHEN '银行卡'   THEN '🏦 银行卡'
           WHEN '电子钱包' THEN '📱 电子钱包'
           WHEN '投资账户' THEN '📈 投资账户'
       END AS type_icon
FROM account;

COMMENT ON VIEW v_account_balances IS '账户余额视图（含 user_id）';

DROP VIEW IF EXISTS v_monthly_income_expense CASCADE;
CREATE VIEW v_monthly_income_expense AS
SELECT
    user_id,
    TO_CHAR(trans_date, 'YYYY-MM') AS year_month,
    COALESCE(SUM(CASE WHEN trans_type = '收入' THEN amount ELSE 0 END), 0) AS total_income,
    COALESCE(SUM(CASE WHEN trans_type = '支出' THEN amount ELSE 0 END), 0) AS total_expense,
    COALESCE(SUM(CASE WHEN trans_type = '收入' THEN amount ELSE -amount END), 0) AS net_balance
FROM transaction_record
WHERE trans_type IN ('收入', '支出')
GROUP BY user_id, TO_CHAR(trans_date, 'YYYY-MM');

COMMENT ON VIEW v_monthly_income_expense IS '月度收支汇总视图（含 user_id）';

DROP VIEW IF EXISTS v_category_expense_ratio CASCADE;
CREATE VIEW v_category_expense_ratio AS
WITH monthly_expense AS (
    SELECT user_id,
           TO_CHAR(trans_date, 'YYYY-MM') AS year_month,
           SUM(amount) AS total
      FROM transaction_record
     WHERE trans_type = '支出'
     GROUP BY user_id, TO_CHAR(trans_date, 'YYYY-MM')
)
SELECT
    t.user_id,
    TO_CHAR(t.trans_date, 'YYYY-MM') AS year_month,
    c.category_id,
    c.category_name,
    SUM(t.amount) AS expense_amount,
    ROUND(SUM(t.amount) * 100.0 / me.total, 2) AS expense_ratio
FROM transaction_record t
JOIN category c ON t.category_id = c.category_id
JOIN monthly_expense me
  ON me.user_id = t.user_id
 AND me.year_month = TO_CHAR(t.trans_date, 'YYYY-MM')
WHERE t.trans_type = '支出'
GROUP BY t.user_id, TO_CHAR(t.trans_date, 'YYYY-MM'), c.category_id, c.category_name, me.total;

COMMENT ON VIEW v_category_expense_ratio IS '类别支出占比视图（含 user_id）';

DROP VIEW IF EXISTS v_budget_status CASCADE;
CREATE VIEW v_budget_status AS
SELECT
    b.user_id,
    b.year_month,
    c.category_name,
    b.budget_amount,
    COALESCE((
        SELECT SUM(t.amount)
          FROM transaction_record t
         WHERE t.category_id = b.category_id
           AND t.user_id = b.user_id
           AND t.trans_type = '支出'
           AND TO_CHAR(t.trans_date, 'YYYY-MM') = b.year_month
    ), 0) AS actual_expense,
    ROUND(COALESCE((
        SELECT SUM(t.amount)
          FROM transaction_record t
         WHERE t.category_id = b.category_id
           AND t.user_id = b.user_id
           AND t.trans_type = '支出'
           AND TO_CHAR(t.trans_date, 'YYYY-MM') = b.year_month
    ), 0) * 100.0 / b.budget_amount, 1) AS completion_rate,
    CASE
        WHEN COALESCE((
            SELECT SUM(t.amount) FROM transaction_record t
             WHERE t.category_id = b.category_id AND t.user_id = b.user_id
               AND t.trans_type = '支出'
               AND TO_CHAR(t.trans_date, 'YYYY-MM') = b.year_month), 0) > b.budget_amount THEN '超支 ⚠️'
        WHEN COALESCE((
            SELECT SUM(t.amount) FROM transaction_record t
             WHERE t.category_id = b.category_id AND t.user_id = b.user_id
               AND t.trans_type = '支出'
               AND TO_CHAR(t.trans_date, 'YYYY-MM') = b.year_month), 0) >= b.budget_amount * 0.9 THEN '接近预算 ⚡'
        ELSE '正常 ✅'
    END AS status
FROM budget b
JOIN category c ON b.category_id = c.category_id;

COMMENT ON VIEW v_budget_status IS '预算执行情况视图（含 user_id）';

DROP VIEW IF EXISTS v_cash_flow_trend CASCADE;
CREATE VIEW v_cash_flow_trend AS
SELECT
    user_id,
    trans_date,
    COALESCE(SUM(CASE WHEN trans_type = '收入' THEN amount ELSE 0 END), 0) AS daily_income,
    COALESCE(SUM(CASE WHEN trans_type = '支出' THEN amount ELSE 0 END), 0) AS daily_expense,
    COALESCE(SUM(CASE WHEN trans_type = '收入' THEN amount ELSE -amount END), 0) AS net_cashflow
FROM transaction_record
WHERE trans_type IN ('收入', '支出')
GROUP BY user_id, trans_date;

COMMENT ON VIEW v_cash_flow_trend IS '日现金流趋势视图（含 user_id）';

DROP VIEW IF EXISTS v_net_worth CASCADE;
CREATE VIEW v_net_worth AS
SELECT
    x.user_id,
    x.total_assets,
    x.total_liabilities,
    x.total_account_balance,
    x.total_assets - x.total_liabilities + x.total_account_balance AS net_worth
FROM (
    SELECT
        u.user_id,
        (SELECT COALESCE(SUM(amount), 0) FROM asset_liability al
          WHERE al.user_id = u.user_id AND al.item_type = '资产') AS total_assets,
        (SELECT COALESCE(SUM(amount), 0) FROM asset_liability al
          WHERE al.user_id = u.user_id AND al.item_type = '负债') AS total_liabilities,
        (SELECT COALESCE(SUM(balance), 0) FROM account a
          WHERE a.user_id = u.user_id) AS total_account_balance
    FROM user_info u
) x;

COMMENT ON VIEW v_net_worth IS '净资产视图（按 user_id 分组，原版为全局聚合）';

-- ============================================================
-- [6] AI 操作审计表
-- ============================================================
CREATE TABLE IF NOT EXISTS ai_action_log (
    log_id      SERIAL       PRIMARY KEY,
    user_id     INT          NOT NULL,
    action      VARCHAR(30)  NOT NULL,
    status      VARCHAR(10)  NOT NULL CHECK (status IN ('preview', 'applied', 'rejected', 'failed')),
    payload     TEXT,
    user_input  VARCHAR(500),
    message     VARCHAR(300),
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_ai_action_log_user FOREIGN KEY (user_id) REFERENCES user_info(user_id)
);

COMMENT ON TABLE ai_action_log IS 'AI 写操作审计表：预览/落库/被拒/失败全部留痕';

-- ============================================================
-- [7] 知识库表
-- ============================================================
CREATE TABLE IF NOT EXISTS knowledge_document (
    document_id SERIAL       PRIMARY KEY,
    user_id     INT          NOT NULL,
    title       VARCHAR(100) NOT NULL,
    source      VARCHAR(200),
    chunk_count INT          NOT NULL DEFAULT 0,
    create_time TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_knowledge_document_user FOREIGN KEY (user_id) REFERENCES user_info(user_id)
);

CREATE TABLE IF NOT EXISTS knowledge_chunk (
    chunk_id        SERIAL      PRIMARY KEY,
    document_id     INT         NOT NULL,
    user_id         INT         NOT NULL,
    chunk_index     INT         NOT NULL,
    content         TEXT        NOT NULL,
    embedding       BYTEA,
    embedding_model VARCHAR(50),
    create_time     TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_knowledge_chunk_document
        FOREIGN KEY (document_id) REFERENCES knowledge_document(document_id) ON DELETE CASCADE,
    CONSTRAINT fk_knowledge_chunk_user FOREIGN KEY (user_id) REFERENCES user_info(user_id)
);

COMMENT ON TABLE knowledge_document IS '知识库文档表：用户上传的理财资料、贷款条款等';
COMMENT ON TABLE knowledge_chunk    IS '知识库切片表：embedding 为空时自动退回 BM25 词法检索';

-- ============================================================
-- [8] 索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_transaction_transfer_group ON transaction_record(transfer_group_id);
CREATE INDEX IF NOT EXISTS idx_ai_action_log_user         ON ai_action_log(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_knowledge_document_user    ON knowledge_document(user_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_user       ON knowledge_chunk(user_id, document_id);

-- 可选：如果保留了按月 TO_CHAR 过滤的旧查询，可加表达式索引兜底；
-- 应用层已改用 trans_date >= ? AND trans_date < ? 的范围谓词（可命中 idx_transaction_date），
-- 因此默认不建表达式索引，避免与范围查询重复。
-- CREATE INDEX IF NOT EXISTS idx_transaction_month ON transaction_record((TO_CHAR(trans_date, 'YYYY-MM')));

-- ============================================================
-- 自检：升级完成后应返回 7 个视图 + 2 张 AI/知识库表
-- ============================================================
-- SELECT table_name FROM information_schema.views WHERE table_schema = current_schema() ORDER BY 1;
-- SELECT COUNT(*) FROM transaction_record WHERE transfer_group_id IS NOT NULL;
