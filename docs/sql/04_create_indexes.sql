-- ============================================================
-- 个人理财管理系统 — 索引脚本
-- 目标DBMS：GaussDB (PostgreSQL 兼容)
-- 执行顺序：第4步（03_insert_test_data.sql 之后）
-- ============================================================

-- ------------------------------------
-- 1. 外键索引（加速JOIN操作）
-- ------------------------------------
CREATE INDEX idx_account_user_id        ON account(user_id);
CREATE INDEX idx_category_user_id       ON category(user_id);
CREATE INDEX idx_category_parent_id     ON category(parent_id);
CREATE INDEX idx_transaction_user_id    ON transaction_record(user_id);
CREATE INDEX idx_transaction_account_id ON transaction_record(account_id);
CREATE INDEX idx_transaction_category_id ON transaction_record(category_id);
CREATE INDEX idx_budget_user_id         ON budget(user_id);
CREATE INDEX idx_budget_category_id     ON budget(category_id);
CREATE INDEX idx_asset_liability_user_id ON asset_liability(user_id);

-- ------------------------------------
-- 2. 日期查询索引
-- ------------------------------------
-- 单列日期索引（按日期范围筛选交易）
CREATE INDEX idx_transaction_date ON transaction_record(trans_date);

-- 复合索引：用户 + 日期（最常用查询组合）
CREATE INDEX idx_transaction_user_date ON transaction_record(user_id, trans_date);

-- 复合索引：交易类型 + 日期（按月统计收入/支出）
CREATE INDEX idx_transaction_type_date ON transaction_record(trans_type, trans_date);

-- ------------------------------------
-- 3. 部分索引（仅索引常用子集，节省空间）
-- ------------------------------------
-- 仅支出记录（预算对比、类别占比分析常用）
CREATE INDEX idx_transaction_expense ON transaction_record(trans_date)
    WHERE trans_type = '支出';

-- 仅收入记录（收入分析常用）
CREATE INDEX idx_transaction_income ON transaction_record(trans_date)
    WHERE trans_type = '收入';

-- ------------------------------------
-- 4. 类别查询索引
-- ------------------------------------
-- 类别类型索引（筛选收入/支出类别）
CREATE INDEX idx_category_type ON category(category_type);

-- 预算年月索引（按月份查预算）
CREATE INDEX idx_budget_year_month ON budget(year_month);

-- 索引创建完成
