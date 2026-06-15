from datetime import timedelta

import pytest

from relationshape.backend.service import AuthError, BackendService, utcnow, iso
from relationshape.backend.store import SQLiteBackendStore


@pytest.fixture()
def svc():
    store = SQLiteBackendStore(":memory:")
    try:
        yield BackendService(store, key_pepper="test-pepper")
    finally:
        store.close()


def make_tenant(svc):
    user = svc.create_user("owner@example.com", "password-123", "Owner")
    tenant = svc.create_tenant(user["id"], "Acme")
    return user, tenant


def test_api_key_lifecycle_and_scope_checks(svc):
    user, tenant = make_tenant(svc)
    created = svc.create_api_key(
        tenant["id"],
        user["id"],
        "device fleet",
        scopes=["runtime:invoke", "faq:read"],
    )

    assert created["api_key"].startswith("rsk_live_")
    assert created["preview"].endswith(created["api_key"][-4:])
    assert "key_hash" not in created

    listed = svc.list_api_keys(tenant["id"])
    assert listed[0]["id"] == created["id"]
    assert "api_key" not in listed[0]
    assert "key_hash" not in listed[0]

    verified = svc.verify_api_key(created["api_key"], required_scope="runtime:invoke")
    assert verified["tenant_id"] == tenant["id"]

    with pytest.raises(AuthError):
        svc.verify_api_key(created["api_key"], required_scope="admin:write")

    svc.revoke_api_key(created["id"])
    with pytest.raises(AuthError):
        svc.verify_api_key(created["api_key"], required_scope="runtime:invoke")


def test_expired_api_key_is_rejected(svc):
    user, tenant = make_tenant(svc)
    expired = svc.create_api_key(
        tenant["id"],
        user["id"],
        "expired",
        expires_at=iso(utcnow() - timedelta(days=1)),
    )

    with pytest.raises(AuthError):
        svc.verify_api_key(expired["api_key"], required_scope="runtime:invoke")


def test_capability_config_and_faq_are_bound_to_key(svc):
    user, tenant = make_tenant(svc)
    key = svc.create_api_key(tenant["id"], user["id"], "runtime")
    faq_base = svc.create_faq_base(tenant["id"], "default faq")
    faq_item = svc.upsert_faq_item(
        faq_base["id"],
        "How do I reset the device?",
        "Hold the back button for five seconds.",
        tags=["device", "reset"],
    )

    config = svc.upsert_capability_config(
        tenant["id"],
        api_key_id=key["id"],
        name="kids companion",
        asr_provider="volcengine",
        llm_provider="deepseek",
        tts_provider="azure",
        personality={"tone": "warm", "age_band": "child"},
        memory={"enabled": True, "episodic_cap": 400},
        faq_base_id=faq_base["id"],
        safety={"child_mode": True},
    )

    assert faq_item["tags"] == ["device", "reset"]
    assert config["llm_provider"] == "deepseek"
    assert config["personality"]["tone"] == "warm"
    assert svc.get_capability_for_key(key["id"])["faq_base_id"] == faq_base["id"]


def test_usage_events_devices_dashboard_and_retention(svc):
    anchor = utcnow().replace(microsecond=0)
    user, tenant = make_tenant(svc)
    key = svc.create_api_key(tenant["id"], user["id"], "runtime")

    svc.record_usage_event(
        key["api_key"],
        external_device_id="device-a",
        event_type="conversation_started",
        region="Shanghai",
        model="speaker-v1",
        provider="deepseek",
        latency_ms=120,
        cost_micros=300,
        usage_seconds=60,
        created_at=iso(anchor - timedelta(days=30)),
    )
    svc.record_usage_event(
        key["api_key"],
        external_device_id="device-a",
        event_type="conversation_ended",
        region="Shanghai",
        model="speaker-v1",
        provider="deepseek",
        latency_ms=140,
        cost_micros=400,
        usage_seconds=120,
        created_at=iso(anchor),
    )
    svc.record_usage_event(
        key["api_key"],
        external_device_id="device-b",
        event_type="conversation_started",
        region="Beijing",
        model="speaker-v2",
        provider="openai",
        latency_ms=220,
        cost_micros=900,
        usage_seconds=30,
        created_at=iso(anchor),
    )
    svc.record_question_event(
        key["api_key"],
        external_device_id="device-a",
        question_text="What is the weather?",
        region="Shanghai",
        created_at=iso(anchor),
    )
    svc.record_question_event(
        key["api_key"],
        external_device_id="device-b",
        question_text="what is the weather？",
        region="Beijing",
        created_at=iso(anchor),
    )

    overview = svc.dashboard_overview(tenant["id"], now=anchor)

    assert overview["active_api_keys_24h"] == 1
    assert overview["active_devices_24h"] == 2
    assert overview["total_devices"] == 2
    assert overview["calls_24h"] == 2
    assert overview["cost_micros_24h"] == 1300
    assert overview["retention_30d"]["cohort"] == 1
    assert overview["retention_30d"]["retained"] == 1
    assert overview["top_questions_30d"][0] == {
        "normalized_question": "whatistheweather",
        "count": 2,
    }
    assert {r["region"]: r["devices"] for r in overview["device_regions"]} == {
        "Shanghai": 1,
        "Beijing": 1,
    }
