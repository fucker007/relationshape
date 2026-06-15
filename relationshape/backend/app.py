"""Optional FastAPI entrypoint for the platform backend MVP.

Run locally:

    uvicorn relationshape.backend.app:app --reload
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from relationshape.backend.admin_ui import ADMIN_HTML
from relationshape.backend.relationship_viz import relationship_detail, relationship_users
from relationshape.backend.service import AuthError, BackendError, BackendService
from relationshape.backend.store import SQLiteBackendStore


def build_service() -> BackendService:
    db_path = os.environ.get("RELATIONSHAPE_BACKEND_DB", "runtime/backend.sqlite3")
    return BackendService(SQLiteBackendStore(db_path))


app = FastAPI(
    title="relationshape platform backend",
    version="0.1.0",
    description="API key, capability, device, FAQ, and metrics backend for relationshape.",
)
app.state.backend = build_service()


def service() -> BackendService:
    return app.state.backend


def api_key_record(
    x_api_key: str = Header(..., alias="X-API-Key"),
    svc: BackendService = Depends(service),
) -> dict[str, Any]:
    try:
        return svc.verify_api_key(x_api_key, required_scope="runtime:invoke")
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthError):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, BackendError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail="internal error")


def model_data(payload: BaseModel, *, exclude_unset: bool = False) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=exclude_unset)
    return payload.dict(exclude_unset=exclude_unset)


class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=8)
    display_name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class TenantCreate(BaseModel):
    owner_user_id: str
    name: str


class ApiKeyCreate(BaseModel):
    tenant_id: str
    created_by: str
    name: str
    scopes: list[str] = Field(default_factory=lambda: ["runtime:invoke"])
    expires_at: str | None = None
    rate_limit_per_minute: int | None = None
    monthly_quota: int | None = None
    allowed_ips: list[str] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=list)


class ApiKeyUpdate(BaseModel):
    name: str | None = None
    scopes: list[str] | None = None
    status: str | None = None
    expires_at: str | None = None
    rate_limit_per_minute: int | None = None
    monthly_quota: int | None = None
    allowed_ips: list[str] | None = None
    allowed_origins: list[str] | None = None


class CapabilityConfigUpsert(BaseModel):
    tenant_id: str
    api_key_id: str | None = None
    name: str = "default"
    asr_provider: str = "none"
    llm_provider: str = "none"
    tts_provider: str = "none"
    personality: dict[str, Any] = Field(default_factory=dict)
    memory: dict[str, Any] = Field(default_factory=lambda: {"enabled": True})
    faq_base_id: str | None = None
    safety: dict[str, Any] = Field(default_factory=dict)


class FaqBaseCreate(BaseModel):
    tenant_id: str
    name: str


class FaqItemUpsert(BaseModel):
    faq_base_id: str
    question: str
    answer: str
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    item_id: str | None = None


class UsageEventCreate(BaseModel):
    external_device_id: str
    event_type: str
    region: str = ""
    model: str = ""
    provider: str = ""
    latency_ms: int | None = None
    status_code: int | None = None
    cost_micros: int = 0
    usage_seconds: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


class QuestionEventCreate(BaseModel):
    external_device_id: str
    question_text: str
    region: str = ""
    model: str = ""
    intent: str = ""
    faq_item_id: str | None = None
    created_at: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return ADMIN_HTML


@app.get("/api/v1/admin/ui/snapshot")
def ui_snapshot(
    tenant_id: str | None = None,
    svc: BackendService = Depends(service),
) -> dict[str, Any]:
    try:
        return svc.ui_snapshot(tenant_id)
    except Exception as exc:
        raise translate_error(exc) from exc


@app.post("/api/v1/admin/demo/seed")
def seed_demo(svc: BackendService = Depends(service)) -> dict[str, Any]:
    try:
        return svc.seed_demo_workspace()
    except Exception as exc:
        raise translate_error(exc) from exc


@app.get("/api/v1/admin/relationships")
def list_relationships() -> dict[str, Any]:
    try:
        return relationship_users()
    except Exception as exc:
        raise translate_error(exc) from exc


@app.get("/api/v1/admin/relationships/{user_id}")
def get_relationship(user_id: str) -> dict[str, Any]:
    try:
        return relationship_detail(user_id)
    except Exception as exc:
        raise translate_error(exc) from exc


@app.post("/api/v1/admin/users")
def create_user(payload: UserCreate, svc: BackendService = Depends(service)) -> dict[str, Any]:
    try:
        return svc.create_user(payload.email, payload.password, payload.display_name)
    except Exception as exc:
        raise translate_error(exc) from exc


@app.post("/api/v1/admin/login")
def login(payload: LoginRequest, svc: BackendService = Depends(service)) -> dict[str, Any]:
    try:
        return svc.authenticate_user(payload.email, payload.password)
    except Exception as exc:
        raise translate_error(exc) from exc


@app.post("/api/v1/admin/tenants")
def create_tenant(payload: TenantCreate, svc: BackendService = Depends(service)) -> dict[str, Any]:
    try:
        return svc.create_tenant(payload.owner_user_id, payload.name)
    except Exception as exc:
        raise translate_error(exc) from exc


@app.post("/api/v1/admin/api-keys")
def create_api_key(payload: ApiKeyCreate, svc: BackendService = Depends(service)) -> dict[str, Any]:
    try:
        return svc.create_api_key(
            payload.tenant_id,
            payload.created_by,
            payload.name,
            scopes=payload.scopes,
            expires_at=payload.expires_at,
            rate_limit_per_minute=payload.rate_limit_per_minute,
            monthly_quota=payload.monthly_quota,
            allowed_ips=payload.allowed_ips,
            allowed_origins=payload.allowed_origins,
        )
    except Exception as exc:
        raise translate_error(exc) from exc


@app.get("/api/v1/admin/tenants/{tenant_id}/api-keys")
def list_api_keys(tenant_id: str, svc: BackendService = Depends(service)) -> list[dict[str, Any]]:
    try:
        return svc.list_api_keys(tenant_id)
    except Exception as exc:
        raise translate_error(exc) from exc


@app.patch("/api/v1/admin/api-keys/{key_id}")
def update_api_key(
    key_id: str,
    payload: ApiKeyUpdate,
    svc: BackendService = Depends(service),
) -> dict[str, Any]:
    try:
        return svc.update_api_key(key_id, **model_data(payload, exclude_unset=True))
    except Exception as exc:
        raise translate_error(exc) from exc


@app.delete("/api/v1/admin/api-keys/{key_id}")
def revoke_api_key(key_id: str, svc: BackendService = Depends(service)) -> dict[str, Any]:
    try:
        return svc.revoke_api_key(key_id)
    except Exception as exc:
        raise translate_error(exc) from exc


@app.put("/api/v1/admin/capability-configs")
def upsert_capability_config(
    payload: CapabilityConfigUpsert,
    svc: BackendService = Depends(service),
) -> dict[str, Any]:
    try:
        return svc.upsert_capability_config(**model_data(payload))
    except Exception as exc:
        raise translate_error(exc) from exc


@app.get("/api/v1/admin/api-keys/{key_id}/capability-config")
def get_capability_for_key(key_id: str, svc: BackendService = Depends(service)) -> dict[str, Any] | None:
    try:
        return svc.get_capability_for_key(key_id)
    except Exception as exc:
        raise translate_error(exc) from exc


@app.post("/api/v1/admin/faq-bases")
def create_faq_base(payload: FaqBaseCreate, svc: BackendService = Depends(service)) -> dict[str, Any]:
    try:
        return svc.create_faq_base(payload.tenant_id, payload.name)
    except Exception as exc:
        raise translate_error(exc) from exc


@app.put("/api/v1/admin/faq-items")
def upsert_faq_item(payload: FaqItemUpsert, svc: BackendService = Depends(service)) -> dict[str, Any]:
    try:
        return svc.upsert_faq_item(**model_data(payload))
    except Exception as exc:
        raise translate_error(exc) from exc


@app.get("/api/v1/admin/tenants/{tenant_id}/dashboard")
def dashboard(tenant_id: str, svc: BackendService = Depends(service)) -> dict[str, Any]:
    try:
        return svc.dashboard_overview(tenant_id)
    except Exception as exc:
        raise translate_error(exc) from exc


@app.get("/api/v1/runtime/config")
def runtime_config(
    key: dict[str, Any] = Depends(api_key_record),
    svc: BackendService = Depends(service),
) -> dict[str, Any]:
    config = svc.get_capability_for_key(key["id"])
    return {"api_key": key, "capability_config": config}


@app.post("/api/v1/runtime/events/usage")
def record_usage_event(
    payload: UsageEventCreate,
    x_api_key: str = Header(..., alias="X-API-Key"),
    svc: BackendService = Depends(service),
) -> dict[str, Any]:
    try:
        return svc.record_usage_event(x_api_key, **model_data(payload))
    except Exception as exc:
        raise translate_error(exc) from exc


@app.post("/api/v1/runtime/events/question")
def record_question_event(
    payload: QuestionEventCreate,
    x_api_key: str = Header(..., alias="X-API-Key"),
    svc: BackendService = Depends(service),
) -> dict[str, Any]:
    try:
        return svc.record_question_event(x_api_key, **model_data(payload))
    except Exception as exc:
        raise translate_error(exc) from exc
