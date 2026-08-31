-- ============================================================
-- 个人理财管理系统 — 触发器脚本
-- 目标DBMS：GaussDB (PostgreSQL 兼容)
-- 执行顺序：第6步（05_create_views.sql 之后）
-- ============================================================

-- ------------------------------------
-- 触发器函数：自动维护账户余额
-- 功能：在 transaction_record 表发生 INSERT/UPDATE/DELETE 时
--       自动更新对应 account 表的 balance 字段
-- ------------------------------------
CREATE OR REPLACE FUNCTION fn_update_balance()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        -- 新增交易：根据交易类型更新账户余额
        IF NEW.trans_type = '收入' THEN
            UPDATE account SET balance = balance + NEW.amount
            WHERE account_id = NEW.account_id;
        ELSIF NEW.trans_type = '支出' THEN
            -- 强制检查余额充足，避免触发器写入负余额
            PERFORM 1 FROM account
            WHERE account_id = NEW.account_id AND balance >= NEW.amount;
            IF NOT FOUND THEN
                RAISE EXCEPTION '账户余额不足！账户ID: %，当前余额不足以支出 %',
                    NEW.account_id, NEW.amount;
            END IF;
            UPDATE account SET balance = balance - NEW.amount
            WHERE account_id = NEW.account_id;
        END IF;
        -- 转账类型不在此处理，由 sp_add_transfer 存储过程管理

    ELSIF TG_OP = 'DELETE' THEN
        -- 删除交易：反向恢复余额
        IF OLD.trans_type = '收入' THEN
            UPDATE account SET balance = balance - OLD.amount
            WHERE account_id = OLD.account_id;
        ELSIF OLD.trans_type = '支出' THEN
            UPDATE account SET balance = balance + OLD.amount
            WHERE account_id = OLD.account_id;
        END IF;

    ELSIF TG_OP = 'UPDATE' THEN
        -- 更新交易：先撤销旧记录影响，再应用新记录影响
        -- 撤销旧值
        IF OLD.trans_type = '收入' THEN
            UPDATE account SET balance = balance - OLD.amount
            WHERE account_id = OLD.account_id;
        ELSIF OLD.trans_type = '支出' THEN
            UPDATE account SET balance = balance + OLD.amount
            WHERE account_id = OLD.account_id;
        END IF;

        -- 应用新值
        IF NEW.trans_type = '收入' THEN
            UPDATE account SET balance = balance + NEW.amount
            WHERE account_id = NEW.account_id;
        ELSIF NEW.trans_type = '支出' THEN
            UPDATE account SET balance = balance - NEW.amount
            WHERE account_id = NEW.account_id;
        END IF;
    END IF;

    RETURN NULL;  -- AFTER触发器返回NULL
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION fn_update_balance() IS '账户余额维护函数：根据交易记录的增删改自动更新账户余额';

-- ------------------------------------
-- 创建触发器
-- ------------------------------------
DROP TRIGGER IF EXISTS trg_transaction_balance ON transaction_record;

CREATE TRIGGER trg_transaction_balance
    AFTER INSERT OR UPDATE OR DELETE ON transaction_record
    FOR EACH ROW
    EXECUTE FUNCTION fn_update_balance();

COMMENT ON TRIGGER trg_transaction_balance ON transaction_record
    IS '交易记录变更后自动更新账户余额';

-- ------------------------------------
-- 触发器函数：创建账户时自动记录日志（示例）
-- ------------------------------------
CREATE OR REPLACE FUNCTION fn_log_account_create()
RETURNS TRIGGER AS $$
BEGIN
    -- 此处可插入日志表（如需要可扩展）
    -- INSERT INTO system_log(log_type, log_message, log_time)
    -- VALUES ('ACCOUNT_CREATE', '新账户创建: ' || NEW.account_name, CURRENT_TIMESTAMP);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 如不需要日志触发器可注释掉
-- CREATE TRIGGER trg_account_create_log
--     AFTER INSERT ON account
--     FOR EACH ROW
--     EXECUTE FUNCTION fn_log_account_create();

-- 触发器创建完成
