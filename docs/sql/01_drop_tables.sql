-- ============================================================
-- 个人理财管理系统 — 删除表脚本
-- 目标DBMS：GaussDB (PostgreSQL 兼容)
-- 执行顺序：第1步（调试用，正常流程可跳过）
-- 注意：按外键依赖逆序删除，避免外键冲突
-- ============================================================

DROP TABLE IF EXISTS transaction_record CASCADE;
DROP TABLE IF EXISTS budget CASCADE;
DROP TABLE IF EXISTS asset_liability CASCADE;
DROP TABLE IF EXISTS account CASCADE;
DROP TABLE IF EXISTS category CASCADE;
DROP TABLE IF EXISTS user_info CASCADE;

-- 所有表已删除
