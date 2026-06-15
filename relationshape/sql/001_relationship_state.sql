-- relationshape 每用户关系状态（复用 memory_system 的 PostgreSQL 实例）。
-- 整存为 JSONB 文档：事务/并发安全、可按 JSON 路径查询、随引擎 schema 演进零迁移。
CREATE TABLE IF NOT EXISTS relationship_state (
    user_id    TEXT PRIMARY KEY,
    data       JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 需要时可加：按更新时间清理冷状态、或按 JSON 字段建索引
-- CREATE INDEX IF NOT EXISTS idx_relstate_updated ON relationship_state (updated_at);
