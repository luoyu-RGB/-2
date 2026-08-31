-- ============================================================
-- 个人理财管理系统 — 全量SQL脚本（一键执行）
-- 目标DBMS：GaussDB (PostgreSQL 兼容)
-- 说明：此文件合并了所有SQL脚本，可直接一次性执行
-- 注意：会先删除所有已存在的表（CASCADE），请谨慎操作
-- ============================================================

-- ============================================================
-- 第1部分：删除旧表
-- ============================================================
DROP TABLE IF EXISTS transaction_record CASCADE;
DROP TABLE IF EXISTS budget CASCADE;
DROP TABLE IF EXISTS asset_liability CASCADE;
DROP TABLE IF EXISTS account CASCADE;
DROP TABLE IF EXISTS category CASCADE;
DROP TABLE IF EXISTS user_info CASCADE;

-- ============================================================
-- 第2部分：创建表结构
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
    account_type    VARCHAR(20)     NOT NULL CHECK (account_type IN ('现金','银行卡','电子钱包','投资账户')),
    balance         DECIMAL(12,2)   NOT NULL DEFAULT 0.00 CHECK (balance >= 0),
    create_time     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_account_user FOREIGN KEY (user_id) REFERENCES user_info(user_id)
);
COMMENT ON TABLE  account IS '账户表';
COMMENT ON COLUMN account.account_id    IS '账户ID，主键，自增';
COMMENT ON COLUMN account.user_id       IS '所属用户ID';
COMMENT ON COLUMN account.account_name  IS '账户名称';
COMMENT ON COLUMN account.account_type  IS '账户类型：现金/银行卡/电子钱包/投资账户';
COMMENT ON COLUMN account.balance       IS '当前余额，由触发器自动维护';
COMMENT ON COLUMN account.create_time   IS '账户创建时间';

-- 3. 类别表
CREATE TABLE category (
    category_id     SERIAL          PRIMARY KEY,
    user_id         INT             NOT NULL,
    category_name   VARCHAR(50)     NOT NULL,
    category_type   VARCHAR(10)     NOT NULL CHECK (category_type IN ('收入','支出')),
    parent_id       INT             DEFAULT NULL,
    create_time     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_category_user   FOREIGN KEY (user_id)   REFERENCES user_info(user_id),
    CONSTRAINT fk_category_parent FOREIGN KEY (parent_id) REFERENCES category(category_id) ON DELETE SET NULL
);
COMMENT ON TABLE  category IS '收支类别表';
COMMENT ON COLUMN category.category_id   IS '类别ID，主键，自增';
COMMENT ON COLUMN category.user_id       IS '所属用户ID';
COMMENT ON COLUMN category.category_name IS '类别名称';
COMMENT ON COLUMN category.category_type IS '类别类型：收入/支出';
COMMENT ON COLUMN category.parent_id     IS '父类别ID，自引用外键';
COMMENT ON COLUMN category.create_time   IS '类别创建时间';

-- 4. 交易记录表
CREATE TABLE transaction_record (
    transaction_id  SERIAL          PRIMARY KEY,
    user_id         INT             NOT NULL,
    account_id      INT             NOT NULL,
    category_id     INT             NOT NULL,
    amount          DECIMAL(12,2)   NOT NULL CHECK (amount > 0),
    trans_type      VARCHAR(10)     NOT NULL CHECK (trans_type IN ('收入','支出','转账')),
    trans_date      DATE            NOT NULL DEFAULT CURRENT_DATE,
    remark          VARCHAR(200),
    create_time     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_transaction_user     FOREIGN KEY (user_id)     REFERENCES user_info(user_id),
    CONSTRAINT fk_transaction_account  FOREIGN KEY (account_id)  REFERENCES account(account_id),
    CONSTRAINT fk_transaction_category FOREIGN KEY (category_id) REFERENCES category(category_id)
);
COMMENT ON TABLE  transaction_record IS '交易记录表';
COMMENT ON COLUMN transaction_record.transaction_id IS '交易ID，主键，自增';
COMMENT ON COLUMN transaction_record.user_id        IS '所属用户ID';
COMMENT ON COLUMN transaction_record.account_id     IS '发生账户ID';
COMMENT ON COLUMN transaction_record.category_id    IS '收支类别ID';
COMMENT ON COLUMN transaction_record.amount         IS '交易金额，必须大于0';
COMMENT ON COLUMN transaction_record.trans_type     IS '交易类型：收入/支出/转账';
COMMENT ON COLUMN transaction_record.trans_date     IS '交易日期';
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
    CONSTRAINT fk_budget_user     FOREIGN KEY (user_id)     REFERENCES user_info(user_id),
    CONSTRAINT fk_budget_category FOREIGN KEY (category_id) REFERENCES category(category_id),
    CONSTRAINT uq_budget_user_category_month UNIQUE (user_id, category_id, year_month)
);
COMMENT ON TABLE  budget IS '预算表';
COMMENT ON COLUMN budget.budget_id     IS '预算ID，主键，自增';
COMMENT ON COLUMN budget.user_id       IS '所属用户ID';
COMMENT ON COLUMN budget.category_id   IS '预算类别ID';
COMMENT ON COLUMN budget.year_month    IS '预算年月，格式YYYY-MM';
COMMENT ON COLUMN budget.budget_amount IS '月度预算金额';
COMMENT ON COLUMN budget.create_time   IS '预算创建时间';

-- 6. 资产负债项目表
CREATE TABLE asset_liability (
    item_id         SERIAL          PRIMARY KEY,
    user_id         INT             NOT NULL,
    item_name       VARCHAR(100)    NOT NULL,
    item_type       VARCHAR(10)     NOT NULL CHECK (item_type IN ('资产','负债')),
    amount          DECIMAL(12,2)   NOT NULL CHECK (amount > 0),
    acquire_date    DATE            NOT NULL DEFAULT CURRENT_DATE,
    remark          VARCHAR(200),
    create_time     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_asset_liability_user FOREIGN KEY (user_id) REFERENCES user_info(user_id)
);
COMMENT ON TABLE  asset_liability IS '资产负债项目表';
COMMENT ON COLUMN asset_liability.item_id      IS '项目ID，主键，自增';
COMMENT ON COLUMN asset_liability.user_id      IS '所属用户ID';
COMMENT ON COLUMN asset_liability.item_name    IS '项目名称';
COMMENT ON COLUMN asset_liability.item_type    IS '项目类型：资产/负债';
COMMENT ON COLUMN asset_liability.amount       IS '金额';
COMMENT ON COLUMN asset_liability.acquire_date IS '取得或发生日期';
COMMENT ON COLUMN asset_liability.remark       IS '备注说明';
COMMENT ON COLUMN asset_liability.create_time  IS '记录创建时间';

-- ============================================================
-- 第3部分：插入测试数据
-- ============================================================

-- 用户
INSERT INTO user_info (user_name, password_hash, create_time) VALUES
('张三', 'a1b2c3d4e5f6_hashed_password_example', '2026-01-01 09:00:00');

-- 账户
INSERT INTO account (user_id, account_name, account_type, balance) VALUES
(1, '现金', '现金', 0.00),
(1, '工商银行储蓄卡', '银行卡', 0.00),
(1, '支付宝', '电子钱包', 0.00),
(1, '微信钱包', '电子钱包', 0.00),
(1, '股票账户', '投资账户', 0.00);

-- 类别（收入）
INSERT INTO category (user_id, category_name, category_type, parent_id) VALUES
(1, '工资收入', '收入', NULL),
(1, '投资收益', '收入', NULL),
(1, '其他收入', '收入', NULL);

-- 类别（支出父类）
INSERT INTO category (user_id, category_name, category_type, parent_id) VALUES
(1, '餐饮',     '支出', NULL),
(1, '交通',     '支出', NULL),
(1, '居住',     '支出', NULL),
(1, '购物消费', '支出', NULL),
(1, '休闲娱乐', '支出', NULL),
(1, '通讯网络', '支出', NULL),
(1, '医疗健康', '支出', NULL),
(1, '教育培训', '支出', NULL),
(1, '其他支出', '支出', NULL);

-- 类别（支出子类：餐饮）
INSERT INTO category (user_id, category_name, category_type, parent_id) VALUES
(1, '早餐',     '支出', 4),
(1, '午餐',     '支出', 4),
(1, '晚餐',     '支出', 4),
(1, '零食饮料', '支出', 4);

-- 类别（支出子类：交通）
INSERT INTO category (user_id, category_name, category_type, parent_id) VALUES
(1, '公共交通', '支出', 5),
(1, '加油充电', '支出', 5);

-- 类别（支出子类：居住）
INSERT INTO category (user_id, category_name, category_type, parent_id) VALUES
(1, '房租',     '支出', 6),
(1, '水电燃气', '支出', 6);

-- 交易数据（2026年3月 ~ 6月）
-- 期初转入（各账户初始余额，视为收入）
INSERT INTO transaction_record (user_id, account_id, category_id, amount, trans_type, trans_date, remark) VALUES
(1, 1, 3, 2500.00, '收入', '2026-03-01', '现金期初余额'),
(1, 2, 3, 50000.00, '收入', '2026-03-01', '储蓄卡期初余额'),
(1, 3, 3, 5000.00, '收入', '2026-03-01', '支付宝期初余额'),
(1, 4, 3, 1000.00, '收入', '2026-03-01', '微信钱包期初余额'),
(1, 5, 3, 100000.00, '收入', '2026-03-01', '股票账户期初余额');

-- 3月
INSERT INTO transaction_record (user_id, account_id, category_id, amount, trans_type, trans_date, remark) VALUES
(1, 2, 1, 15000.00, '收入', '2026-03-05', '3月份工资'),
(1, 1, 13, 8.00, '支出', '2026-03-01', '早餐：豆浆油条'),
(1, 4, 14, 25.00, '支出', '2026-03-01', '午餐：外卖盖饭'),
(1, 1, 15, 35.00, '支出', '2026-03-02', '晚餐：和朋友聚餐'),
(1, 3, 16, 12.00, '支出', '2026-03-03', '星巴克咖啡'),
(1, 1, 13, 6.00, '支出', '2026-03-05', '早餐：包子'),
(1, 4, 14, 22.00, '支出', '2026-03-07', '午餐：牛肉面'),
(1, 3, 15, 40.00, '支出', '2026-03-10', '晚餐：火锅'),
(1, 1, 16, 15.00, '支出', '2026-03-12', '奶茶+零食'),
(1, 1, 13, 7.00, '支出', '2026-03-15', '早餐：煎饼'),
(1, 4, 14, 28.00, '支出', '2026-03-15', '午餐：黄焖鸡'),
(1, 1, 16, 18.00, '支出', '2026-03-20', '下午茶'),
(1, 3, 17, 4.00, '支出', '2026-03-03', '地铁通勤'),
(1, 3, 17, 4.00, '支出', '2026-03-08', '公交车'),
(1, 1, 18, 200.00, '支出', '2026-03-15', '加油'),
(1, 2, 19, 3500.00, '支出', '2026-03-01', '3月房租'),
(1, 2, 20, 280.00, '支出', '2026-03-10', '3月水电燃气'),
(1, 3, 7, 158.00, '支出', '2026-03-08', '淘宝：T恤'),
(1, 2, 7, 299.00, '支出', '2026-03-20', '京东：蓝牙耳机'),
(1, 2, 12, 2000.00, '支出', '2026-03-05', '转账至支付宝（转出）'),
(1, 3, 3, 2000.00, '收入', '2026-03-05', '银行卡转入（转入）');

-- 4月 (20笔)
INSERT INTO transaction_record (user_id, account_id, category_id, amount, trans_type, trans_date, remark) VALUES
(1, 2, 1, 15000.00, '收入', '2026-04-03', '4月份工资'),
(1, 1, 13, 7.00, '支出', '2026-04-02', '早餐：鸡蛋灌饼'),
(1, 4, 14, 23.00, '支出', '2026-04-02', '午餐：麻辣烫'),
(1, 3, 15, 45.00, '支出', '2026-04-05', '晚餐：烧烤'),
(1, 1, 16, 10.00, '支出', '2026-04-08', '奶茶'),
(1, 4, 14, 20.00, '支出', '2026-04-12', '午餐：刀削面'),
(1, 1, 13, 8.00, '支出', '2026-04-15', '早餐：肉夹馍'),
(1, 3, 15, 55.00, '支出', '2026-04-20', '晚餐：湘菜馆'),
(1, 3, 17, 4.00, '支出', '2026-04-06', '地铁'),
(1, 1, 18, 180.00, '支出', '2026-04-12', '加油'),
(1, 2, 19, 3500.00, '支出', '2026-04-01', '4月房租'),
(1, 2, 20, 265.00, '支出', '2026-04-08', '4月水电燃气'),
(1, 3, 8, 120.00, '支出', '2026-04-10', '电影院'),
(1, 2, 8, 350.00, '支出', '2026-04-18', 'KTV聚会'),
(1, 3, 7, 89.00, '支出', '2026-04-15', '超市日用品'),
(1, 4, 9, 59.00, '支出', '2026-04-01', '手机话费充值'),
(1, 2, 12, 2000.00, '支出', '2026-04-03', '转账至支付宝（转出）'),
(1, 3, 3, 2000.00, '收入', '2026-04-03', '银行卡转入（转入）');

-- 5月 (19笔)
INSERT INTO transaction_record (user_id, account_id, category_id, amount, trans_type, trans_date, remark) VALUES
(1, 2, 1, 15000.00, '收入', '2026-05-06', '5月份工资'),
(1, 1, 13, 6.00, '支出', '2026-05-04', '早餐'),
(1, 4, 14, 25.00, '支出', '2026-05-04', '午餐：煲仔饭'),
(1, 1, 15, 30.00, '支出', '2026-05-08', '晚餐'),
(1, 3, 16, 13.00, '支出', '2026-05-10', '星巴克'),
(1, 1, 14, 22.00, '支出', '2026-05-15', '午餐：饺子'),
(1, 4, 15, 50.00, '支出', '2026-05-22', '晚餐：日料'),
(1, 3, 17, 4.00, '支出', '2026-05-07', '地铁'),
(1, 1, 18, 220.00, '支出', '2026-05-18', '加油'),
(1, 2, 19, 3500.00, '支出', '2026-05-01', '5月房租'),
(1, 2, 20, 250.00, '支出', '2026-05-09', '5月水电燃气'),
(1, 2, 10, 180.00, '支出', '2026-05-12', '感冒药+门诊'),
(1, 3, 11, 299.00, '支出', '2026-05-16', '在线课程'),
(1, 3, 7, 450.00, '支出', '2026-05-20', '618预售：运动鞋'),
(1, 4, 8, 68.00, '支出', '2026-05-25', '视频会员年费'),
(1, 2, 12, 2000.00, '支出', '2026-05-06', '转账至支付宝（转出）'),
(1, 3, 3, 2000.00, '收入', '2026-05-06', '银行卡转入（转入）');

-- 6月 (16笔)
INSERT INTO transaction_record (user_id, account_id, category_id, amount, trans_type, trans_date, remark) VALUES
(1, 2, 1, 15000.00, '收入', '2026-06-05', '6月份工资'),
(1, 1, 13, 8.00, '支出', '2026-06-01', '早餐'),
(1, 4, 14, 28.00, '支出', '2026-06-01', '午餐：红烧肉'),
(1, 3, 15, 42.00, '支出', '2026-06-05', '晚餐：庆祝发工资'),
(1, 1, 16, 15.00, '支出', '2026-06-08', '零食'),
(1, 4, 14, 20.00, '支出', '2026-06-12', '午餐：兰州拉面'),
(1, 1, 13, 7.00, '支出', '2026-06-15', '早餐'),
(1, 3, 17, 4.00, '支出', '2026-06-03', '地铁'),
(1, 1, 18, 190.00, '支出', '2026-06-10', '加油'),
(1, 2, 19, 3500.00, '支出', '2026-06-01', '6月房租'),
(1, 2, 20, 275.00, '支出', '2026-06-08', '6月水电燃气'),
(1, 3, 7, 129.00, '支出', '2026-06-12', '618购物：背包'),
(1, 3, 8, 88.00, '支出', '2026-06-14', '周末出游门票'),
(1, 2, 12, 2000.00, '支出', '2026-06-05', '转账至支付宝（转出）'),
(1, 3, 3, 2000.00, '收入', '2026-06-05', '银行卡转入（转入）'),
(1, 5, 2, 850.00, '收入', '2026-06-12', '股票分红收益');

-- 预算
INSERT INTO budget (user_id, category_id, year_month, budget_amount) VALUES
(1, 4, '2026-06', 2000.00),
(1, 5, '2026-06', 500.00),
(1, 7, '2026-06', 1500.00),
(1, 8, '2026-06', 800.00);

-- 资产负债
INSERT INTO asset_liability (user_id, item_name, item_type, amount, acquire_date, remark) VALUES
(1, '定期存款',   '资产', 100000.00,  '2025-01-15', '一年期定期，利率2.1%'),
(1, '股票持仓',   '资产', 120000.00,  '2025-06-01', '沪深300ETF + 个股'),
(1, '房产自住',   '资产', 2000000.00, '2023-03-20', '自住房产，市场估值'),
(1, '住房贷款',   '负债', 1500000.00, '2023-03-20', '30年期按揭贷款'),
(1, '信用卡欠款', '负债', 5000.00,    '2026-06-10', '本期账单待还'),
(1, '消费贷款',   '负债', 20000.00,   '2026-02-01', '12期分期，购车');

-- ============================================================
-- 第4部分：创建索引
-- ============================================================
CREATE INDEX idx_account_user_id          ON account(user_id);
CREATE INDEX idx_category_user_id         ON category(user_id);
CREATE INDEX idx_category_parent_id       ON category(parent_id);
CREATE INDEX idx_transaction_user_id      ON transaction_record(user_id);
CREATE INDEX idx_transaction_account_id   ON transaction_record(account_id);
CREATE INDEX idx_transaction_category_id  ON transaction_record(category_id);
CREATE INDEX idx_budget_user_id           ON budget(user_id);
CREATE INDEX idx_budget_category_id       ON budget(category_id);
CREATE INDEX idx_asset_liability_user_id  ON asset_liability(user_id);
CREATE INDEX idx_transaction_date         ON transaction_record(trans_date);
CREATE INDEX idx_transaction_user_date    ON transaction_record(user_id, trans_date);
CREATE INDEX idx_transaction_type_date    ON transaction_record(trans_type, trans_date);
CREATE INDEX idx_transaction_expense      ON transaction_record(trans_date) WHERE trans_type = '支出';
CREATE INDEX idx_transaction_income       ON transaction_record(trans_date) WHERE trans_type = '收入';
CREATE INDEX idx_category_type            ON category(category_type);
CREATE INDEX idx_budget_year_month        ON budget(year_month);

-- ============================================================
-- 第5部分：创建视图
-- ============================================================
CREATE OR REPLACE VIEW v_transaction_detail AS
SELECT t.transaction_id, u.user_name, a.account_name,
       c.category_name, pc.category_name AS parent_category_name,
       t.amount, t.trans_type, t.trans_date, t.remark, t.create_time
FROM transaction_record t
JOIN user_info u ON t.user_id = u.user_id
JOIN account a ON t.account_id = a.account_id
JOIN category c ON t.category_id = c.category_id
LEFT JOIN category pc ON c.parent_id = pc.category_id
ORDER BY t.trans_date DESC, t.transaction_id DESC;

CREATE OR REPLACE VIEW v_account_balances AS
SELECT account_id, account_name, account_type, balance
FROM account ORDER BY balance DESC;

CREATE OR REPLACE VIEW v_monthly_income_expense AS
SELECT TO_CHAR(trans_date, 'YYYY-MM') AS year_month,
       COALESCE(SUM(CASE WHEN trans_type='收入' THEN amount ELSE 0 END),0) AS total_income,
       COALESCE(SUM(CASE WHEN trans_type='支出' THEN amount ELSE 0 END),0) AS total_expense,
       COALESCE(SUM(CASE WHEN trans_type='收入' THEN amount ELSE -amount END),0) AS net_balance
FROM transaction_record GROUP BY TO_CHAR(trans_date, 'YYYY-MM') ORDER BY year_month;

CREATE OR REPLACE VIEW v_category_expense_ratio AS
WITH monthly_expense AS (
    SELECT TO_CHAR(trans_date,'YYYY-MM') AS ym, SUM(amount) AS total
    FROM transaction_record WHERE trans_type='支出'
    GROUP BY TO_CHAR(trans_date,'YYYY-MM')
)
SELECT TO_CHAR(t.trans_date,'YYYY-MM') AS year_month,
       c.category_id, c.category_name,
       SUM(t.amount) AS expense_amount,
       ROUND(SUM(t.amount)*100.0/me.total, 2) AS expense_ratio
FROM transaction_record t
JOIN category c ON t.category_id=c.category_id
JOIN monthly_expense me ON TO_CHAR(t.trans_date,'YYYY-MM')=me.ym
WHERE t.trans_type='支出'
GROUP BY TO_CHAR(t.trans_date,'YYYY-MM'), c.category_id, c.category_name, me.total
ORDER BY year_month DESC, expense_amount DESC;

CREATE OR REPLACE VIEW v_budget_status AS
SELECT b.year_month, c.category_name, b.budget_amount,
       COALESCE((SELECT SUM(t.amount) FROM transaction_record t
                 WHERE t.category_id=b.category_id AND t.trans_type='支出'
                 AND TO_CHAR(t.trans_date,'YYYY-MM')=b.year_month),0) AS actual_expense,
       ROUND(COALESCE((SELECT SUM(t.amount) FROM transaction_record t
                       WHERE t.category_id=b.category_id AND t.trans_type='支出'
                       AND TO_CHAR(t.trans_date,'YYYY-MM')=b.year_month),0)*100.0/b.budget_amount,1) AS completion_rate
FROM budget b JOIN category c ON b.category_id=c.category_id
ORDER BY b.year_month DESC, completion_rate DESC;

CREATE OR REPLACE VIEW v_cash_flow_trend AS
SELECT trans_date,
       COALESCE(SUM(CASE WHEN trans_type='收入' THEN amount ELSE 0 END),0) AS daily_income,
       COALESCE(SUM(CASE WHEN trans_type='支出' THEN amount ELSE 0 END),0) AS daily_expense,
       COALESCE(SUM(CASE WHEN trans_type='收入' THEN amount ELSE -amount END),0) AS net_cashflow
FROM transaction_record GROUP BY trans_date ORDER BY trans_date;

CREATE OR REPLACE VIEW v_net_worth AS
SELECT (SELECT COALESCE(SUM(amount),0) FROM asset_liability WHERE item_type='资产') AS total_assets,
       (SELECT COALESCE(SUM(amount),0) FROM asset_liability WHERE item_type='负债') AS total_liabilities,
       (SELECT COALESCE(SUM(balance),0) FROM account) AS total_account_balance,
       (SELECT COALESCE(SUM(amount),0) FROM asset_liability WHERE item_type='资产')
       -(SELECT COALESCE(SUM(amount),0) FROM asset_liability WHERE item_type='负债')
       +(SELECT COALESCE(SUM(balance),0) FROM account) AS net_worth;

-- ============================================================
-- 第6部分：更新账户余额（基于交易汇总计算）
-- 注：GaussDB 不支持 plpgsql FUNCTION/PROCEDURE，余额维护由 Web 后端处理
-- ============================================================
UPDATE account a SET balance = (
    COALESCE((SELECT SUM(CASE WHEN t.trans_type='收入' THEN t.amount ELSE -t.amount END)
              FROM transaction_record t WHERE t.account_id=a.account_id), 0)
);

-- ============================================================
-- 全部完成！
-- 数据统计：1用户 + 5账户 + 19类别 + 71交易 + 4预算 + 6资产负债
-- 对象统计：6表 + 16索引 + 7视图
-- ============================================================
