-- ============================================================
-- 个人理财管理系统 — 建表脚本
-- 目标DBMS：GaussDB (PostgreSQL 兼容)
-- 执行顺序：第2步（01_drop_tables.sql 之后）
-- ============================================================

-- 1. 用户信息表
CREATE TABLE user_info (
    user_id         SERIAL          PRIMARY KEY,
    user_name       VARCHAR(50)     NOT NULL,
    password_hash   VARCHAR(255)    NOT NULL,
    create_time     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE  user_info IS '用户信息表';
COMMENT ON COLUMN user_info.user_id       IS '用户ID，主键，自增';
COMMENT ON COLUMN user_info.user_name     IS '用户名，登录用';
COMMENT ON COLUMN user_info.password_hash IS '密码哈希值，安全存储';
COMMENT ON COLUMN user_info.create_time   IS '用户创建时间';

-- 2. 账户表
CREATE TABLE account (
    account_id      SERIAL          PRIMARY KEY,
    user_id         INT             NOT NULL,
    account_name    VARCHAR(50)     NOT NULL,
    account_type    VARCHAR(20)     NOT NULL CHECK (account_type IN ('现金', '银行卡', '电子钱包', '投资账户')),
    balance         DECIMAL(12,2)   NOT NULL DEFAULT 0.00 CHECK (balance >= 0),
    create_time     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_account_user
        FOREIGN KEY (user_id) REFERENCES user_info(user_id)
);

COMMENT ON TABLE  account IS '账户表';
COMMENT ON COLUMN account.account_id    IS '账户ID，主键，自增';
COMMENT ON COLUMN account.user_id       IS '所属用户ID，外键引用user_info';
COMMENT ON COLUMN account.account_name  IS '账户名称，如"工商银行储蓄卡"';
COMMENT ON COLUMN account.account_type  IS '账户类型：现金/银行卡/电子钱包/投资账户';
COMMENT ON COLUMN account.balance       IS '当前余额，由交易触发器自动维护';
COMMENT ON COLUMN account.create_time   IS '账户创建时间';

-- 3. 类别表
CREATE TABLE category (
    category_id     SERIAL          PRIMARY KEY,
    user_id         INT             NOT NULL,
    category_name   VARCHAR(50)     NOT NULL,
    category_type   VARCHAR(10)     NOT NULL CHECK (category_type IN ('收入', '支出')),
    parent_id       INT             DEFAULT NULL,
    create_time     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_category_user
        FOREIGN KEY (user_id) REFERENCES user_info(user_id),
    CONSTRAINT fk_category_parent
        FOREIGN KEY (parent_id) REFERENCES category(category_id)
        ON DELETE SET NULL
);

COMMENT ON TABLE  category IS '收支类别表';
COMMENT ON COLUMN category.category_id   IS '类别ID，主键，自增';
COMMENT ON COLUMN category.user_id       IS '所属用户ID，外键引用user_info';
COMMENT ON COLUMN category.category_name IS '类别名称，如"餐饮"、"工资收入"';
COMMENT ON COLUMN category.category_type IS '类别类型：收入/支出';
COMMENT ON COLUMN category.parent_id     IS '父类别ID，自引用外键，支持层级结构';
COMMENT ON COLUMN category.create_time   IS '类别创建时间';

-- 4. 交易记录表
CREATE TABLE transaction_record (
    transaction_id  SERIAL          PRIMARY KEY,
    user_id         INT             NOT NULL,
    account_id      INT             NOT NULL,
    category_id     INT             NOT NULL,
    amount          DECIMAL(12,2)   NOT NULL CHECK (amount > 0),
    trans_type      VARCHAR(10)     NOT NULL CHECK (trans_type IN ('收入', '支出', '转账')),
    trans_date      DATE            NOT NULL DEFAULT CURRENT_DATE,
    remark          VARCHAR(200),
    create_time     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_transaction_user
        FOREIGN KEY (user_id) REFERENCES user_info(user_id),
    CONSTRAINT fk_transaction_account
        FOREIGN KEY (account_id) REFERENCES account(account_id),
    CONSTRAINT fk_transaction_category
        FOREIGN KEY (category_id) REFERENCES category(category_id)
);

COMMENT ON TABLE  transaction_record IS '交易记录表';
COMMENT ON COLUMN transaction_record.transaction_id IS '交易ID，主键，自增';
COMMENT ON COLUMN transaction_record.user_id        IS '所属用户ID，外键引用user_info';
COMMENT ON COLUMN transaction_record.account_id     IS '发生账户ID，外键引用account';
COMMENT ON COLUMN transaction_record.category_id    IS '收支类别ID，外键引用category';
COMMENT ON COLUMN transaction_record.amount         IS '交易金额，必须大于0';
COMMENT ON COLUMN transaction_record.trans_type     IS '交易类型：收入/支出/转账';
COMMENT ON COLUMN transaction_record.trans_date     IS '交易日期，默认当天';
COMMENT ON COLUMN transaction_record.remark         IS '备注说明';
COMMENT ON COLUMN transaction_record.create_time    IS '记录创建时间';

-- 5. 预算表
CREATE TABLE budget (
    budget_id       SERIAL          PRIMARY KEY,
    user_id         INT             NOT NULL,
    category_id     INT             NOT NULL,
    year_month      VARCHAR(7)      NOT NULL,
    budget_amount   DECIMAL(12,2)   NOT NULL CHECK (budget_amount > 0),
    create_time     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_budget_user
        FOREIGN KEY (user_id) REFERENCES user_info(user_id),
    CONSTRAINT fk_budget_category
        FOREIGN KEY (category_id) REFERENCES category(category_id),
    CONSTRAINT uq_budget_user_category_month
        UNIQUE (user_id, category_id, year_month)
);

COMMENT ON TABLE  budget IS '预算表';
COMMENT ON COLUMN budget.budget_id     IS '预算ID，主键，自增';
COMMENT ON COLUMN budget.user_id       IS '所属用户ID，外键引用user_info';
COMMENT ON COLUMN budget.category_id   IS '预算类别ID，外键引用category';
COMMENT ON COLUMN budget.year_month    IS '预算年月，格式YYYY-MM，如2026-06';
COMMENT ON COLUMN budget.budget_amount IS '月度预算金额，必须大于0';
COMMENT ON COLUMN budget.create_time   IS '预算创建时间';

-- 6. 资产负债项目表
CREATE TABLE asset_liability (
    item_id         SERIAL          PRIMARY KEY,
    user_id         INT             NOT NULL,
    item_name       VARCHAR(100)    NOT NULL,
    item_type       VARCHAR(10)     NOT NULL CHECK (item_type IN ('资产', '负债')),
    amount          DECIMAL(12,2)   NOT NULL CHECK (amount > 0),
    acquire_date    DATE            NOT NULL DEFAULT CURRENT_DATE,
    remark          VARCHAR(200),
    create_time     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_asset_liability_user
        FOREIGN KEY (user_id) REFERENCES user_info(user_id)
);

COMMENT ON TABLE  asset_liability IS '资产负债项目表';
COMMENT ON COLUMN asset_liability.item_id      IS '项目ID，主键，自增';
COMMENT ON COLUMN asset_liability.user_id      IS '所属用户ID，外键引用user_info';
COMMENT ON COLUMN asset_liability.item_name    IS '项目名称，如"房产"、"房贷"';
COMMENT ON COLUMN asset_liability.item_type    IS '项目类型：资产/负债';
COMMENT ON COLUMN asset_liability.amount       IS '金额，必须大于0';
COMMENT ON COLUMN asset_liability.acquire_date IS '取得或发生日期';
COMMENT ON COLUMN asset_liability.remark       IS '备注说明';
COMMENT ON COLUMN asset_liability.create_time  IS '记录创建时间';

-- 完成提示
-- 所有表创建完毕，接下来执行 03_insert_test_data.sql 插入测试数据
