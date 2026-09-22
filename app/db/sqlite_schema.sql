-- ============================================================
-- 个人理财助手 — SQLite 离线演示库结构
-- 与 docs/sql/02_create_tables.sql（GaussDB/PostgreSQL）逐列对齐
--
-- 方言差异（仅这三处，其余完全一致）：
--   1. SERIAL                    → INTEGER PRIMARY KEY AUTOINCREMENT
--   2. 年月格式 CHECK 的 ~ 正则  → GLOB 字符类
--   3. 视图里的 TO_CHAR(...,'YYYY-MM') → strftime('%Y-%m', ...)
--
-- 余额唯一由触发器维护（trg_transaction_balance_*），应用层不得直接写 balance。
-- 本文件可重复执行（IF NOT EXISTS）。
-- ============================================================

PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------
-- 1. 用户信息表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_info (
    user_id         INTEGER     PRIMARY KEY AUTOINCREMENT,
    user_name       VARCHAR(50) NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    create_time     TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- 2. 账户表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS account (
    account_id      INTEGER     PRIMARY KEY AUTOINCREMENT,
    user_id         INT         NOT NULL,
    account_name    VARCHAR(50) NOT NULL,
    account_type    VARCHAR(20) NOT NULL CHECK (account_type IN ('现金', '银行卡', '电子钱包', '投资账户')),
    balance         DECIMAL(12,2) NOT NULL DEFAULT 0.00 CHECK (balance >= 0),
    create_time     TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_account_user FOREIGN KEY (user_id) REFERENCES user_info(user_id)
);

-- ------------------------------------------------------------
-- 3. 类别表（自引用，支持父子层级）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS category (
    category_id     INTEGER     PRIMARY KEY AUTOINCREMENT,
    user_id         INT         NOT NULL,
    category_name   VARCHAR(50) NOT NULL,
    category_type   VARCHAR(10) NOT NULL CHECK (category_type IN ('收入', '支出')),
    parent_id       INT         DEFAULT NULL,
    create_time     TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_category_user   FOREIGN KEY (user_id) REFERENCES user_info(user_id),
    CONSTRAINT fk_category_parent FOREIGN KEY (parent_id) REFERENCES category(category_id) ON DELETE SET NULL
);

-- ------------------------------------------------------------
-- 4. 交易记录表
--    transfer_group_id：同一笔转账的两条记录共享该值
--    （修复方案第五节第 3 条：不再靠"时间差 + 备注 LIKE"配对）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transaction_record (
    transaction_id      INTEGER     PRIMARY KEY AUTOINCREMENT,
    user_id             INT         NOT NULL,
    account_id          INT         NOT NULL,
    category_id         INT         NOT NULL,
    amount              DECIMAL(12,2) NOT NULL CHECK (amount > 0),
    trans_type          VARCHAR(10) NOT NULL CHECK (trans_type IN ('收入', '支出', '转账')),
    trans_date          DATE        NOT NULL DEFAULT CURRENT_DATE,
    remark              VARCHAR(200),
    transfer_group_id   VARCHAR(36) DEFAULT NULL,
    create_time         TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_transaction_user     FOREIGN KEY (user_id) REFERENCES user_info(user_id),
    CONSTRAINT fk_transaction_account  FOREIGN KEY (account_id) REFERENCES account(account_id),
    CONSTRAINT fk_transaction_category FOREIGN KEY (category_id) REFERENCES category(category_id)
);

-- ------------------------------------------------------------
-- 5. 预算表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS budget (
    budget_id       INTEGER     PRIMARY KEY AUTOINCREMENT,
    user_id         INT         NOT NULL,
    category_id     INT         NOT NULL,
    year_month      VARCHAR(7)  NOT NULL
                    CHECK (year_month GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'),
    budget_amount   DECIMAL(12,2) NOT NULL CHECK (budget_amount > 0),
    create_time     TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_budget_user     FOREIGN KEY (user_id) REFERENCES user_info(user_id),
    CONSTRAINT fk_budget_category FOREIGN KEY (category_id) REFERENCES category(category_id),
    CONSTRAINT uq_budget_user_category_month UNIQUE (user_id, category_id, year_month)
);

-- ------------------------------------------------------------
-- 6. 资产负债项目表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_liability (
    item_id         INTEGER     PRIMARY KEY AUTOINCREMENT,
    user_id         INT         NOT NULL,
    item_name       VARCHAR(100) NOT NULL,
    item_type       VARCHAR(10) NOT NULL CHECK (item_type IN ('资产', '负债')),
    amount          DECIMAL(12,2) NOT NULL CHECK (amount > 0),
    acquire_date    DATE        NOT NULL DEFAULT CURRENT_DATE,
    remark          VARCHAR(200),
    create_time     TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_asset_liability_user FOREIGN KEY (user_id) REFERENCES user_info(user_id)
);

-- ------------------------------------------------------------
-- 7. AI 操作审计表（新增）
--    AI 的每一次写操作都要留痕：预览、落库、被拒绝、失败
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_action_log (
    log_id          INTEGER     PRIMARY KEY AUTOINCREMENT,
    user_id         INT         NOT NULL,
    action          VARCHAR(30) NOT NULL,
    status          VARCHAR(10) NOT NULL CHECK (status IN ('preview', 'applied', 'rejected', 'failed')),
    payload         TEXT,
    user_input      VARCHAR(500),
    message         VARCHAR(300),
    created_at      TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_ai_action_log_user FOREIGN KEY (user_id) REFERENCES user_info(user_id)
);

-- ============================================================
-- 8. 知识库文档表（新增）
--    理财资料、贷款条款等文本资料，按 user_id 隔离
-- ============================================================
CREATE TABLE IF NOT EXISTS knowledge_document (
    document_id     INTEGER     PRIMARY KEY AUTOINCREMENT,
    user_id         INT         NOT NULL,
    title           VARCHAR(100) NOT NULL,
    source          VARCHAR(200),
    chunk_count     INT         NOT NULL DEFAULT 0,
    create_time     TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_knowledge_document_user FOREIGN KEY (user_id) REFERENCES user_info(user_id)
);

-- ============================================================
-- 9. 知识库切片表（新增）
--    embedding 为可选列：安装了 fastembed 时存 float32 向量，
--    否则保持 NULL 并自动退回 BM25 词法检索
-- ============================================================
CREATE TABLE IF NOT EXISTS knowledge_chunk (
    chunk_id        INTEGER     PRIMARY KEY AUTOINCREMENT,
    document_id     INT         NOT NULL,
    user_id         INT         NOT NULL,
    chunk_index     INT         NOT NULL,
    content         TEXT        NOT NULL,
    embedding       BLOB,
    embedding_model VARCHAR(50),
    create_time     TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_knowledge_chunk_document
        FOREIGN KEY (document_id) REFERENCES knowledge_document(document_id) ON DELETE CASCADE,
    CONSTRAINT fk_knowledge_chunk_user FOREIGN KEY (user_id) REFERENCES user_info(user_id)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_document_user ON knowledge_document(user_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_user    ON knowledge_chunk(user_id, document_id);

-- ============================================================
-- 索引（对照 docs/sql/04_create_indexes.sql）
-- SQLite 不支持部分索引，故省略 WHERE trans_type = '支出' 两个
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_account_user_id          ON account(user_id);
CREATE INDEX IF NOT EXISTS idx_category_user_id         ON category(user_id);
CREATE INDEX IF NOT EXISTS idx_category_parent_id       ON category(parent_id);
CREATE INDEX IF NOT EXISTS idx_transaction_user_id      ON transaction_record(user_id);
CREATE INDEX IF NOT EXISTS idx_transaction_account_id   ON transaction_record(account_id);
CREATE INDEX IF NOT EXISTS idx_transaction_category_id  ON transaction_record(category_id);
CREATE INDEX IF NOT EXISTS idx_budget_user_id           ON budget(user_id);
CREATE INDEX IF NOT EXISTS idx_budget_category_id       ON budget(category_id);
CREATE INDEX IF NOT EXISTS idx_asset_liability_user_id  ON asset_liability(user_id);
CREATE INDEX IF NOT EXISTS idx_transaction_date         ON transaction_record(trans_date);
CREATE INDEX IF NOT EXISTS idx_transaction_user_date    ON transaction_record(user_id, trans_date);
CREATE INDEX IF NOT EXISTS idx_transaction_type_date    ON transaction_record(trans_type, trans_date);
CREATE INDEX IF NOT EXISTS idx_category_type            ON category(category_type);
CREATE INDEX IF NOT EXISTS idx_budget_year_month        ON budget(year_month);
CREATE INDEX IF NOT EXISTS idx_transaction_transfer_group ON transaction_record(transfer_group_id);
CREATE INDEX IF NOT EXISTS idx_ai_action_log_user       ON ai_action_log(user_id, created_at);

-- ============================================================
-- 触发器：账户余额唯一维护者
-- 对照 docs/sql/06_create_triggers.sql 的 fn_update_balance()
-- 差异：SQLite 无 plpgsql，拆成 INSERT / UPDATE / DELETE 三个触发器；
--       触发器内先做余额预检，抛出中文错误而不是依赖 CHECK 报错。
-- ============================================================

DROP TRIGGER IF EXISTS trg_transaction_balance_insert;
CREATE TRIGGER trg_transaction_balance_insert
AFTER INSERT ON transaction_record
FOR EACH ROW
WHEN NEW.trans_type IN ('收入', '支出')
BEGIN
    -- 先预检再扣款：抛出可读的业务错误，而不是让 CHECK 约束报错
    SELECT CASE
        WHEN NEW.trans_type = '支出'
         AND (SELECT balance FROM account WHERE account_id = NEW.account_id) < NEW.amount
        THEN RAISE(ABORT, '账户余额不足：本次支出会导致账户余额为负数')
    END;

    UPDATE account
       SET balance = balance + CASE NEW.trans_type WHEN '收入' THEN NEW.amount ELSE -NEW.amount END
     WHERE account_id = NEW.account_id;
END;

DROP TRIGGER IF EXISTS trg_transaction_balance_delete;
CREATE TRIGGER trg_transaction_balance_delete
AFTER DELETE ON transaction_record
FOR EACH ROW
WHEN OLD.trans_type IN ('收入', '支出')
BEGIN
    SELECT CASE
        WHEN OLD.trans_type = '收入'
         AND (SELECT balance FROM account WHERE account_id = OLD.account_id) < OLD.amount
        THEN RAISE(ABORT, '删除该收入会导致账户余额为负数')
    END;

    UPDATE account
       SET balance = balance - CASE OLD.trans_type WHEN '收入' THEN OLD.amount ELSE -OLD.amount END
     WHERE account_id = OLD.account_id;
END;

DROP TRIGGER IF EXISTS trg_transaction_balance_update;
CREATE TRIGGER trg_transaction_balance_update
AFTER UPDATE ON transaction_record
FOR EACH ROW
BEGIN
    -- 撤销旧值
    UPDATE account
       SET balance = balance - CASE OLD.trans_type WHEN '收入' THEN OLD.amount WHEN '支出' THEN -OLD.amount ELSE 0 END
     WHERE account_id = OLD.account_id;

    -- 应用新值前预检（覆盖改账户 / 改金额 / 改方向三种情况）
    SELECT CASE
        WHEN NEW.trans_type = '支出'
         AND (SELECT balance FROM account WHERE account_id = NEW.account_id) < NEW.amount
        THEN RAISE(ABORT, '账户余额不足：本次修改会导致账户余额为负数')
    END;

    UPDATE account
       SET balance = balance + CASE NEW.trans_type WHEN '收入' THEN NEW.amount WHEN '支出' THEN -NEW.amount ELSE 0 END
     WHERE account_id = NEW.account_id;
END;

-- ============================================================
-- 视图（对照 docs/sql/05_create_viewse.sql，全部补上 user_id 维度）
-- 说明：视图内不写 ORDER BY（排序交给查询侧，避免视图被物化时排序失效）；
--       年月一律用 strftime('%Y-%m', trans_date)，查询侧用范围谓词走索引。
-- ============================================================

DROP VIEW IF EXISTS v_transaction_detail;
CREATE VIEW v_transaction_detail AS
SELECT
    t.transaction_id,
    t.user_id,
    u.user_name,
    a.account_name,
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

DROP VIEW IF EXISTS v_account_balances;
CREATE VIEW v_account_balances AS
SELECT
    account_id,
    user_id,
    account_name,
    account_type,
    balance,
    CASE account_type
        WHEN '现金'     THEN '现金'
        WHEN '银行卡'   THEN '银行卡'
        WHEN '电子钱包' THEN '电子钱包'
        WHEN '投资账户' THEN '投资账户'
    END AS type_icon
FROM account;

DROP VIEW IF EXISTS v_monthly_income_expense;
CREATE VIEW v_monthly_income_expense AS
SELECT
    user_id,
    strftime('%Y-%m', trans_date) AS year_month,
    COALESCE(SUM(CASE WHEN trans_type = '收入' THEN amount ELSE 0 END), 0) AS total_income,
    COALESCE(SUM(CASE WHEN trans_type = '支出' THEN amount ELSE 0 END), 0) AS total_expense,
    COALESCE(SUM(CASE WHEN trans_type = '收入' THEN amount ELSE -amount END), 0) AS net_balance
FROM transaction_record
WHERE trans_type IN ('收入', '支出')
GROUP BY user_id, strftime('%Y-%m', trans_date);

DROP VIEW IF EXISTS v_category_expense_ratio;
CREATE VIEW v_category_expense_ratio AS
WITH monthly_expense AS (
    SELECT
        user_id,
        strftime('%Y-%m', trans_date) AS year_month,
        SUM(amount) AS total
    FROM transaction_record
    WHERE trans_type = '支出'
    GROUP BY user_id, strftime('%Y-%m', trans_date)
)
SELECT
    t.user_id,
    strftime('%Y-%m', t.trans_date) AS year_month,
    c.category_id,
    c.category_name,
    SUM(t.amount) AS expense_amount,
    ROUND(SUM(t.amount) * 100.0 / me.total, 2) AS expense_ratio
FROM transaction_record t
JOIN category c ON t.category_id = c.category_id
JOIN monthly_expense me
  ON me.user_id = t.user_id
 AND me.year_month = strftime('%Y-%m', t.trans_date)
WHERE t.trans_type = '支出'
GROUP BY t.user_id, strftime('%Y-%m', t.trans_date), c.category_id, c.category_name, me.total;

DROP VIEW IF EXISTS v_budget_status;
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
          AND strftime('%Y-%m', t.trans_date) = b.year_month
    ), 0) AS actual_expense,
    ROUND(
        COALESCE((
            SELECT SUM(t.amount)
            FROM transaction_record t
            WHERE t.category_id = b.category_id
              AND t.user_id = b.user_id
              AND t.trans_type = '支出'
              AND strftime('%Y-%m', t.trans_date) = b.year_month
        ), 0) * 100.0 / b.budget_amount,
        1
    ) AS completion_rate,
    CASE
        WHEN COALESCE((
            SELECT SUM(t.amount)
            FROM transaction_record t
            WHERE t.category_id = b.category_id
              AND t.user_id = b.user_id
              AND t.trans_type = '支出'
              AND strftime('%Y-%m', t.trans_date) = b.year_month
        ), 0) > b.budget_amount THEN '超支'
        WHEN COALESCE((
            SELECT SUM(t.amount)
            FROM transaction_record t
            WHERE t.category_id = b.category_id
              AND t.user_id = b.user_id
              AND t.trans_type = '支出'
              AND strftime('%Y-%m', t.trans_date) = b.year_month
        ), 0) >= b.budget_amount * 0.9 THEN '接近预算'
        ELSE '正常'
    END AS status
FROM budget b
JOIN category c ON b.category_id = c.category_id;

DROP VIEW IF EXISTS v_cash_flow_trend;
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

DROP VIEW IF EXISTS v_net_worth;
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
