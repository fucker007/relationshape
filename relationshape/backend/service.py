"""Business service for the relationshape platform backend."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from relationshape.backend.crypto import (
    default_pepper,
    generate_api_key,
    hash_api_key,
    key_preview,
    password_hash,
    verify_password,
)
from relationshape.backend.store import SQLiteBackendStore, dumps, loads


ACTIVE_KEY_STATUS = "active"
REVOKED_KEY_STATUS = "revoked"
SUSPENDED_KEY_STATUS = "suspended"


class BackendError(ValueError):
    pass


class AuthError(BackendError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).replace(microsecond=0).isoformat()


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def normalize_question(text: str) -> str:
    compact = re.sub(r"\s+", "", (text or "").strip().lower())
    compact = re.sub(r"[?？!！。.,，、;；:：]+$", "", compact)
    return compact[:180]


class BackendService:
    def __init__(self, store: SQLiteBackendStore, *, key_pepper: str | None = None) -> None:
        self.store = store
        self.key_pepper = default_pepper() if key_pepper is None else key_pepper

    # ------------------------------------------------------------------ users

    def create_user(self, email: str, password: str, display_name: str = "") -> dict[str, Any]:
        user_id = new_id("usr")
        now = iso()
        self.store.execute(
            "INSERT INTO users (id, email, password_hash, display_name, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, email.lower().strip(), password_hash(password), display_name, now),
        )
        return self.get_user(user_id)

    def authenticate_user(self, email: str, password: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM users WHERE email = ?", (email.lower().strip(),))
        if not row or not verify_password(password, row["password_hash"]):
            raise AuthError("invalid email or password")
        row.pop("password_hash", None)
        return row

    def get_user(self, user_id: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
        if not row:
            raise BackendError("user not found")
        row.pop("password_hash", None)
        return row

    def list_users(self) -> list[dict[str, Any]]:
        rows = self.store.query_all("SELECT * FROM users ORDER BY created_at DESC")
        for row in rows:
            row.pop("password_hash", None)
        return rows

    # ---------------------------------------------------------------- tenants

    def create_tenant(self, owner_user_id: str, name: str) -> dict[str, Any]:
        self.get_user(owner_user_id)
        tenant_id = new_id("ten")
        now = iso()
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO tenants (id, name, owner_user_id, created_at) VALUES (?, ?, ?, ?)",
                (tenant_id, name, owner_user_id, now),
            )
            conn.execute(
                "INSERT INTO tenant_members (tenant_id, user_id, role, created_at) VALUES (?, ?, ?, ?)",
                (tenant_id, owner_user_id, "owner", now),
            )
        return self.get_tenant(tenant_id)

    def get_tenant(self, tenant_id: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM tenants WHERE id = ?", (tenant_id,))
        if not row:
            raise BackendError("tenant not found")
        return row

    def list_tenants(self) -> list[dict[str, Any]]:
        return self.store.query_all("SELECT * FROM tenants ORDER BY created_at DESC")

    # --------------------------------------------------------------- api keys

    def create_api_key(
        self,
        tenant_id: str,
        created_by: str,
        name: str,
        *,
        scopes: list[str] | None = None,
        expires_at: str | None = None,
        rate_limit_per_minute: int | None = None,
        monthly_quota: int | None = None,
        allowed_ips: list[str] | None = None,
        allowed_origins: list[str] | None = None,
    ) -> dict[str, Any]:
        self.get_tenant(tenant_id)
        self.get_user(created_by)
        api_key = generate_api_key()
        key_id = new_id("key")
        now = iso()
        self.store.execute(
            "INSERT INTO api_keys "
            "(id, tenant_id, name, key_hash, preview, scopes_json, status, expires_at, "
            "rate_limit_per_minute, monthly_quota, allowed_ips_json, allowed_origins_json, "
            "created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                key_id,
                tenant_id,
                name,
                hash_api_key(api_key, self.key_pepper),
                key_preview(api_key),
                dumps(scopes or ["runtime:invoke"]),
                ACTIVE_KEY_STATUS,
                expires_at,
                rate_limit_per_minute,
                monthly_quota,
                dumps(allowed_ips or []),
                dumps(allowed_origins or []),
                created_by,
                now,
            ),
        )
        record = self.get_api_key(key_id)
        record["api_key"] = api_key
        return record

    def get_api_key(self, key_id: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM api_keys WHERE id = ?", (key_id,))
        if not row:
            raise BackendError("api key not found")
        return self._api_key_from_row(row)

    def list_api_keys(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = self.store.query_all(
            "SELECT * FROM api_keys WHERE tenant_id = ? ORDER BY created_at DESC", (tenant_id,)
        )
        return [self._api_key_from_row(row) for row in rows]

    def update_api_key(
        self,
        key_id: str,
        *,
        name: str | None = None,
        scopes: list[str] | None = None,
        status: str | None = None,
        expires_at: str | None = None,
        rate_limit_per_minute: int | None = None,
        monthly_quota: int | None = None,
        allowed_ips: list[str] | None = None,
        allowed_origins: list[str] | None = None,
    ) -> dict[str, Any]:
        current = self.get_api_key(key_id)
        values = {
            "name": name if name is not None else current["name"],
            "scopes_json": dumps(scopes if scopes is not None else current["scopes"]),
            "status": status if status is not None else current["status"],
            "expires_at": expires_at if expires_at is not None else current["expires_at"],
            "rate_limit_per_minute": (
                rate_limit_per_minute
                if rate_limit_per_minute is not None
                else current["rate_limit_per_minute"]
            ),
            "monthly_quota": monthly_quota if monthly_quota is not None else current["monthly_quota"],
            "allowed_ips_json": dumps(allowed_ips if allowed_ips is not None else current["allowed_ips"]),
            "allowed_origins_json": dumps(
                allowed_origins if allowed_origins is not None else current["allowed_origins"]
            ),
        }
        self.store.execute(
            "UPDATE api_keys SET name = ?, scopes_json = ?, status = ?, expires_at = ?, "
            "rate_limit_per_minute = ?, monthly_quota = ?, allowed_ips_json = ?, "
            "allowed_origins_json = ? WHERE id = ?",
            (
                values["name"],
                values["scopes_json"],
                values["status"],
                values["expires_at"],
                values["rate_limit_per_minute"],
                values["monthly_quota"],
                values["allowed_ips_json"],
                values["allowed_origins_json"],
                key_id,
            ),
        )
        return self.get_api_key(key_id)

    def revoke_api_key(self, key_id: str) -> dict[str, Any]:
        return self.update_api_key(key_id, status=REVOKED_KEY_STATUS)

    def verify_api_key(self, api_key: str, *, required_scope: str | None = None) -> dict[str, Any]:
        digest = hash_api_key(api_key, self.key_pepper)
        row = self.store.query_one("SELECT * FROM api_keys WHERE key_hash = ?", (digest,))
        if not row:
            raise AuthError("invalid api key")
        record = self._api_key_from_row(row)
        if record["status"] != ACTIVE_KEY_STATUS:
            raise AuthError("api key is not active")
        expires_at = parse_time(record["expires_at"])
        if expires_at and expires_at <= utcnow():
            raise AuthError("api key expired")
        if required_scope and required_scope not in record["scopes"]:
            raise AuthError("api key missing required scope")
        self.store.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (iso(), record["id"]))
        return record

    def _api_key_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        clean = dict(row)
        clean.pop("key_hash", None)
        clean.pop("scopes_json", None)
        clean.pop("allowed_ips_json", None)
        clean.pop("allowed_origins_json", None)
        return {
            **clean,
            "scopes": loads(row.get("scopes_json"), []),
            "allowed_ips": loads(row.get("allowed_ips_json"), []),
            "allowed_origins": loads(row.get("allowed_origins_json"), []),
        }

    # --------------------------------------------------------- configurations

    def upsert_capability_config(
        self,
        tenant_id: str,
        *,
        api_key_id: str | None = None,
        name: str = "default",
        asr_provider: str = "none",
        llm_provider: str = "none",
        tts_provider: str = "none",
        personality: dict[str, Any] | None = None,
        memory: dict[str, Any] | None = None,
        faq_base_id: str | None = None,
        safety: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.get_tenant(tenant_id)
        existing = None
        if api_key_id:
            existing = self.store.query_one(
                "SELECT * FROM capability_configs WHERE api_key_id = ?", (api_key_id,)
            )
        now = iso()
        if existing:
            self.store.execute(
                "UPDATE capability_configs SET name = ?, asr_provider = ?, llm_provider = ?, "
                "tts_provider = ?, personality_json = ?, memory_json = ?, faq_base_id = ?, "
                "safety_json = ?, updated_at = ? WHERE id = ?",
                (
                    name,
                    asr_provider,
                    llm_provider,
                    tts_provider,
                    dumps(personality or {}),
                    dumps(memory or {"enabled": True}),
                    faq_base_id,
                    dumps(safety or {}),
                    now,
                    existing["id"],
                ),
            )
            return self.get_capability_config(existing["id"])
        config_id = new_id("cfg")
        self.store.execute(
            "INSERT INTO capability_configs "
            "(id, tenant_id, api_key_id, name, asr_provider, llm_provider, tts_provider, "
            "personality_json, memory_json, faq_base_id, safety_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                config_id,
                tenant_id,
                api_key_id,
                name,
                asr_provider,
                llm_provider,
                tts_provider,
                dumps(personality or {}),
                dumps(memory or {"enabled": True}),
                faq_base_id,
                dumps(safety or {}),
                now,
                now,
            ),
        )
        return self.get_capability_config(config_id)

    def get_capability_config(self, config_id: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM capability_configs WHERE id = ?", (config_id,))
        if not row:
            raise BackendError("capability config not found")
        return self._capability_from_row(row)

    def get_capability_for_key(self, api_key_id: str) -> dict[str, Any] | None:
        row = self.store.query_one("SELECT * FROM capability_configs WHERE api_key_id = ?", (api_key_id,))
        return self._capability_from_row(row) if row else None

    def list_capability_configs(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = self.store.query_all(
            "SELECT * FROM capability_configs WHERE tenant_id = ? ORDER BY updated_at DESC",
            (tenant_id,),
        )
        return [self._capability_from_row(row) for row in rows]

    def _capability_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        clean = dict(row)
        clean.pop("personality_json", None)
        clean.pop("memory_json", None)
        clean.pop("safety_json", None)
        return {
            **clean,
            "personality": loads(row.get("personality_json"), {}),
            "memory": loads(row.get("memory_json"), {}),
            "safety": loads(row.get("safety_json"), {}),
        }

    # ------------------------------------------------------------------- FAQ

    def create_faq_base(self, tenant_id: str, name: str) -> dict[str, Any]:
        self.get_tenant(tenant_id)
        faq_base_id = new_id("faqb")
        now = iso()
        self.store.execute(
            "INSERT INTO faq_bases (id, tenant_id, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (faq_base_id, tenant_id, name, now, now),
        )
        return self.store.query_one("SELECT * FROM faq_bases WHERE id = ?", (faq_base_id,))

    def list_faq_bases(self, tenant_id: str) -> list[dict[str, Any]]:
        return self.store.query_all(
            "SELECT * FROM faq_bases WHERE tenant_id = ? ORDER BY updated_at DESC", (tenant_id,)
        )

    def list_faq_items(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = self.store.query_all(
            "SELECT i.* FROM faq_items i JOIN faq_bases b ON i.faq_base_id = b.id "
            "WHERE b.tenant_id = ? ORDER BY i.updated_at DESC",
            (tenant_id,),
        )
        for row in rows:
            row["tags"] = loads(row.get("tags_json"), [])
            row["enabled"] = bool(row["enabled"])
            row.pop("tags_json", None)
        return rows

    def upsert_faq_item(
        self,
        faq_base_id: str,
        question: str,
        answer: str,
        *,
        tags: list[str] | None = None,
        enabled: bool = True,
        item_id: str | None = None,
    ) -> dict[str, Any]:
        now = iso()
        if item_id and self.store.query_one("SELECT id FROM faq_items WHERE id = ?", (item_id,)):
            self.store.execute(
                "UPDATE faq_items SET question = ?, answer = ?, tags_json = ?, enabled = ?, "
                "updated_at = ? WHERE id = ?",
                (question, answer, dumps(tags or []), 1 if enabled else 0, now, item_id),
            )
        else:
            item_id = item_id or new_id("faqi")
            self.store.execute(
                "INSERT INTO faq_items "
                "(id, faq_base_id, question, answer, tags_json, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (item_id, faq_base_id, question, answer, dumps(tags or []), 1 if enabled else 0, now, now),
            )
        row = self.store.query_one("SELECT * FROM faq_items WHERE id = ?", (item_id,))
        assert row is not None
        row["tags"] = loads(row.get("tags_json"), [])
        row["enabled"] = bool(row["enabled"])
        row["tags_json"] = None
        return row

    # ------------------------------------------------------------ devices/logs

    def register_device(
        self,
        tenant_id: str,
        api_key_id: str,
        external_device_id: str,
        *,
        region: str = "",
        model: str = "",
        seen_at: str | None = None,
    ) -> dict[str, Any]:
        now = seen_at or iso()
        existing = self.store.query_one(
            "SELECT * FROM devices WHERE tenant_id = ? AND external_device_id = ?",
            (tenant_id, external_device_id),
        )
        if existing:
            self.store.execute(
                "UPDATE devices SET api_key_id = ?, region = ?, model = ?, last_seen_at = ? WHERE id = ?",
                (api_key_id, region or existing["region"], model or existing["model"], now, existing["id"]),
            )
            return self.get_device(existing["id"])
        device_id = new_id("dev")
        self.store.execute(
            "INSERT INTO devices "
            "(id, tenant_id, api_key_id, external_device_id, region, model, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (device_id, tenant_id, api_key_id, external_device_id, region, model, now, now),
        )
        return self.get_device(device_id)

    def get_device(self, device_id: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM devices WHERE id = ?", (device_id,))
        if not row:
            raise BackendError("device not found")
        return row

    def list_devices(self, tenant_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.query_all(
            "SELECT * FROM devices WHERE tenant_id = ? ORDER BY last_seen_at DESC LIMIT ?",
            (tenant_id, limit),
        )

    def record_usage_event(
        self,
        api_key: str,
        *,
        external_device_id: str,
        event_type: str,
        region: str = "",
        model: str = "",
        provider: str = "",
        latency_ms: int | None = None,
        status_code: int | None = None,
        cost_micros: int = 0,
        usage_seconds: int = 0,
        metadata: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        key = self.verify_api_key(api_key, required_scope="runtime:invoke")
        now = created_at or iso()
        device = self.register_device(
            key["tenant_id"],
            key["id"],
            external_device_id,
            region=region,
            model=model,
            seen_at=now,
        )
        event_id = new_id("evt")
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO api_usage_events "
                "(id, tenant_id, api_key_id, device_id, event_type, provider, latency_ms, "
                "status_code, cost_micros, usage_seconds, created_at, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    key["tenant_id"],
                    key["id"],
                    device["id"],
                    event_type,
                    provider,
                    latency_ms,
                    status_code,
                    cost_micros,
                    usage_seconds,
                    now,
                    dumps(metadata or {}),
                ),
            )
            if usage_seconds:
                conn.execute(
                    "UPDATE devices SET total_usage_seconds = total_usage_seconds + ?, "
                    "last_seen_at = ? WHERE id = ?",
                    (usage_seconds, now, device["id"]),
                )
        row = self.store.query_one("SELECT * FROM api_usage_events WHERE id = ?", (event_id,))
        assert row is not None
        return row

    def record_question_event(
        self,
        api_key: str,
        *,
        external_device_id: str,
        question_text: str,
        region: str = "",
        model: str = "",
        intent: str = "",
        faq_item_id: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        key = self.verify_api_key(api_key, required_scope="runtime:invoke")
        now = created_at or iso()
        device = self.register_device(
            key["tenant_id"],
            key["id"],
            external_device_id,
            region=region,
            model=model,
            seen_at=now,
        )
        event_id = new_id("qst")
        self.store.execute(
            "INSERT INTO question_events "
            "(id, tenant_id, api_key_id, device_id, question_text, normalized_question, intent, "
            "faq_item_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                key["tenant_id"],
                key["id"],
                device["id"],
                question_text,
                normalize_question(question_text),
                intent,
                faq_item_id,
                now,
            ),
        )
        row = self.store.query_one("SELECT * FROM question_events WHERE id = ?", (event_id,))
        assert row is not None
        return row

    def recent_usage_events(self, tenant_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.store.query_all(
            "SELECT e.*, d.external_device_id, k.preview AS api_key_preview "
            "FROM api_usage_events e "
            "LEFT JOIN devices d ON e.device_id = d.id "
            "LEFT JOIN api_keys k ON e.api_key_id = k.id "
            "WHERE e.tenant_id = ? ORDER BY e.created_at DESC LIMIT ?",
            (tenant_id, limit),
        )
        for row in rows:
            row["metadata"] = loads(row.get("metadata_json"), {})
            row.pop("metadata_json", None)
        return rows

    def recent_question_events(self, tenant_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.query_all(
            "SELECT q.*, d.external_device_id, k.preview AS api_key_preview "
            "FROM question_events q "
            "LEFT JOIN devices d ON q.device_id = d.id "
            "LEFT JOIN api_keys k ON q.api_key_id = k.id "
            "WHERE q.tenant_id = ? ORDER BY q.created_at DESC LIMIT ?",
            (tenant_id, limit),
        )

    # --------------------------------------------------------------- metrics

    def dashboard_overview(self, tenant_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        anchor = now or utcnow()
        day_ago = iso(anchor - timedelta(days=1))
        month_ago = iso(anchor - timedelta(days=30))
        three_months_ago = iso(anchor - timedelta(days=90))
        tenant = self.get_tenant(tenant_id)
        active_keys = self.store.query_one(
            "SELECT COUNT(DISTINCT api_key_id) AS n FROM api_usage_events "
            "WHERE tenant_id = ? AND created_at >= ?",
            (tenant_id, day_ago),
        )["n"]
        active_devices = self.store.query_one(
            "SELECT COUNT(*) AS n FROM devices WHERE tenant_id = ? AND last_seen_at >= ?",
            (tenant_id, day_ago),
        )["n"]
        total_devices = self.store.query_one(
            "SELECT COUNT(*) AS n FROM devices WHERE tenant_id = ?", (tenant_id,)
        )["n"]
        usage = self.store.query_one(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(cost_micros), 0) AS cost_micros, "
            "COALESCE(AVG(latency_ms), 0) AS avg_latency_ms "
            "FROM api_usage_events WHERE tenant_id = ? AND created_at >= ?",
            (tenant_id, day_ago),
        )
        regions = self.store.query_all(
            "SELECT region, COUNT(*) AS devices FROM devices WHERE tenant_id = ? "
            "GROUP BY region ORDER BY devices DESC",
            (tenant_id,),
        )
        question_rank = self.store.query_all(
            "SELECT normalized_question, COUNT(*) AS count FROM question_events "
            "WHERE tenant_id = ? AND created_at >= ? GROUP BY normalized_question "
            "ORDER BY count DESC, normalized_question ASC LIMIT 20",
            (tenant_id, month_ago),
        )
        return {
            "tenant": tenant,
            "window": {"active_since": day_ago, "question_rank_since": month_ago},
            "active_api_keys_24h": active_keys,
            "active_devices_24h": active_devices,
            "total_devices": total_devices,
            "calls_24h": usage["calls"],
            "cost_micros_24h": usage["cost_micros"],
            "avg_latency_ms_24h": round(float(usage["avg_latency_ms"] or 0), 2),
            "retention_30d": self._retention(tenant_id, anchor, days=30),
            "retention_90d": self._retention(tenant_id, anchor, days=90),
            "device_regions": regions,
            "top_questions_30d": question_rank,
            "cohort_starts": {"30d_since": month_ago, "90d_since": three_months_ago},
        }

    def ui_snapshot(self, tenant_id: str | None = None) -> dict[str, Any]:
        tenants = self.list_tenants()
        selected = tenant_id or (tenants[0]["id"] if tenants else None)
        if not selected:
            return {
                "users": self.list_users(),
                "tenants": tenants,
                "selected_tenant_id": None,
                "overview": None,
                "api_keys": [],
                "capability_configs": [],
                "faq_bases": [],
                "faq_items": [],
                "devices": [],
                "usage_events": [],
                "question_events": [],
            }
        return {
            "users": self.list_users(),
            "tenants": tenants,
            "selected_tenant_id": selected,
            "overview": self.dashboard_overview(selected),
            "api_keys": self.list_api_keys(selected),
            "capability_configs": self.list_capability_configs(selected),
            "faq_bases": self.list_faq_bases(selected),
            "faq_items": self.list_faq_items(selected),
            "devices": self.list_devices(selected),
            "usage_events": self.recent_usage_events(selected, limit=20),
            "question_events": self.recent_question_events(selected, limit=20),
        }

    def seed_demo_workspace(self) -> dict[str, Any]:
        existing = self.store.query_one("SELECT * FROM users WHERE email = ?", ("demo@relationshape.local",))
        if existing:
            tenants = self.store.query_all(
                "SELECT * FROM tenants WHERE owner_user_id = ? ORDER BY created_at DESC", (existing["id"],)
            )
            if tenants:
                return self.ui_snapshot(tenants[0]["id"])

        user = self.create_user("demo@relationshape.local", "password-123", "Demo Admin")
        tenant = self.create_tenant(user["id"], "relationshape Demo")
        faq_base = self.create_faq_base(tenant["id"], "默认 FAQ")
        self.upsert_faq_item(faq_base["id"], "设备离线怎么办？", "先确认网络、电源和最近一次心跳，再尝试重启设备。", tags=["device"])
        self.upsert_faq_item(faq_base["id"], "如何切换角色性格？", "在能力配置里调整 personality 字段并保存到对应 API Key。", tags=["config"])
        key = self.create_api_key(
            tenant["id"],
            user["id"],
            "家庭陪伴设备",
            scopes=["runtime:invoke", "faq:read"],
            rate_limit_per_minute=120,
            monthly_quota=500000,
        )
        self.upsert_capability_config(
            tenant["id"],
            api_key_id=key["id"],
            name="儿童陪伴默认配置",
            asr_provider="volcengine",
            llm_provider="deepseek",
            tts_provider="azure",
            personality={"tone": "warm", "energy": "medium", "age_band": "child"},
            memory={"enabled": True, "episodic_cap": 400, "recall_top_k": 3},
            faq_base_id=faq_base["id"],
            safety={"child_mode": True, "crisis_escalation": True},
        )
        now = utcnow()
        samples = [
            ("dev-shanghai-01", "Shanghai", "speaker-v1", "deepseek", "今天能不能继续讲恐龙故事？", 610, 44),
            ("dev-beijing-02", "Beijing", "speaker-v2", "volcengine", "设备离线怎么办？", 240, 18),
            ("dev-shenzhen-03", "Shenzhen", "speaker-v1", "azure", "我上次说我喜欢什么？", 410, 31),
            ("dev-hangzhou-04", "Hangzhou", "speaker-lite", "deepseek", "怎么换声音？", 330, 26),
        ]
        for idx, (dev, region, model, provider, question, latency, seconds) in enumerate(samples):
            at = iso(now - timedelta(hours=idx * 3))
            self.record_usage_event(
                key["api_key"],
                external_device_id=dev,
                event_type="conversation_turn",
                region=region,
                model=model,
                provider=provider,
                latency_ms=latency,
                status_code=200,
                cost_micros=900 + idx * 120,
                usage_seconds=seconds,
                created_at=at,
            )
            self.record_question_event(
                key["api_key"],
                external_device_id=dev,
                question_text=question,
                region=region,
                model=model,
                created_at=at,
            )
        return self.ui_snapshot(tenant["id"])

    def _retention(self, tenant_id: str, anchor: datetime, *, days: int) -> dict[str, Any]:
        cohort_start = iso(anchor - timedelta(days=days + 1))
        cohort_end = iso(anchor - timedelta(days=days - 1))
        active_after = iso(anchor - timedelta(days=1))
        row = self.store.query_one(
            "SELECT COUNT(*) AS cohort, "
            "SUM(CASE WHEN last_seen_at >= ? THEN 1 ELSE 0 END) AS retained "
            "FROM devices WHERE tenant_id = ? AND first_seen_at >= ? AND first_seen_at <= ?",
            (active_after, tenant_id, cohort_start, cohort_end),
        )
        cohort = row["cohort"] or 0
        retained = row["retained"] or 0
        return {
            "days": days,
            "cohort": cohort,
            "retained": retained,
            "rate": round(retained / cohort, 4) if cohort else None,
        }
