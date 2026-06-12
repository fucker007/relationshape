-- ============================================================================
-- add_primary_person_unique_index.sql
-- ----------------------------------------------------------------------------
-- 目的:
--   在 person_nodes 上为 role='primary' 的行建立 (owner_id) 唯一索引，
--   作为「单 owner 至多一个 primary」语义的 schema 级兜底，防止应用层
--   再次出现刘杨 bug 那种「按 user_name miss → 重复 upsert primary」。
--
-- 前置条件 (必须先做，否则本 DDL 会因为已有重复数据而失败):
--   1. 跑 dry-run 检查:
--        python scripts/dedupe_primary_persons.py
--      或限定单 owner:
--        python scripts/dedupe_primary_persons.py --owner-id <uuid>
--   2. review dry-run 输出无误后，真执行清理:
--        python scripts/dedupe_primary_persons.py --execute
--   3. 确认 SQL 验证:
--        SELECT owner_id, count(*) FROM person_nodes
--        WHERE role='primary' GROUP BY owner_id HAVING count(*) > 1;
--      返回 0 行后，再执行下面这条 DDL。
--
-- 使用 CONCURRENTLY 避免锁全表；注意 CONCURRENTLY 不能在事务块内运行，
-- 直接用 psql 顶层执行：
--   psql "$PG_DSN" -f scripts/add_primary_person_unique_index.sql
--
-- 回滚:
--   DROP INDEX CONCURRENTLY IF EXISTS uq_primary_person_per_owner;
-- ============================================================================

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_primary_person_per_owner
    ON person_nodes (owner_id)
    WHERE role = 'primary';
