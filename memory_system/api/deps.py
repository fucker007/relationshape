"""
依赖注入：共享 DB pool、Redis client、LLM client。
"""
from __future__ import annotations

from fastapi import Request

from llm.client import LLMClient
from storage.pg_store import GraphStore, PgStore
from storage.redis_store import RedisStore


def get_pg(request: Request) -> PgStore:
    return request.app.state.pg


def get_gs(request: Request) -> GraphStore:
    return request.app.state.gs


def get_redis(request: Request) -> RedisStore:
    return request.app.state.redis


def get_llm(request: Request) -> LLMClient:
    return request.app.state.llm
