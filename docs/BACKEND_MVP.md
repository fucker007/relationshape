# relationshape backend MVP

This backend is the first platform layer around the existing relationshape
engine. It does not change `CompanionEngine`; it adds tenant, API key,
capability configuration, FAQ, device, event, and dashboard primitives around
the runtime.

## Run locally

Install the optional backend dependencies:

```bash
cd /home/zihai/workspace/Agent_server_design/relationshape/relationshape
pip install -e ".[backend,dev]"
```

Start the API from the project root shown above:

```bash
RELATIONSHAPE_BACKEND_DB=runtime/backend.sqlite3 \
python -m uvicorn relationshape.backend.app:app --host 127.0.0.1 --port 8123
```

Or use the wrapper script, which works even if your terminal is currently inside
the Python package directory:

```bash
/home/zihai/workspace/Agent_server_design/relationshape/relationshape/scripts/run_backend.sh
```

Open the console:

```text
http://127.0.0.1:8123/
```

The root page is now the integrated backend UI. It includes:

- Overview: API usage, active devices, costs, retention, provider and FAQ status.
- API Keys: create keys and inspect scopes, status, limits, and last use.
- Capabilities: bind ASR / LLM / TTS / personality / memory / safety config to a key.
- Devices: device list, region distribution, usage, and recent runtime events.
- FAQ: manage FAQ entries and inspect user question events.
- Relationships: reads `RELATIONSHAPE_STATE_DIR` and shows relationship-state users, timeline, and memory graph summaries. This is the integrated version of the `intelligent-bardeen-9pp4o7` relationship visualization work.

The SQLite service is intended for local development and MVP validation. The
service layer is the boundary to keep when moving the tables to Postgres.

Do not start Uvicorn from `relationshape/relationshape/relationshape`. That
directory contains `types.py`, which can shadow Python's standard-library
`types` module in reloader subprocesses.

If you need hot reload on this machine, use polling and restrict the watched
directory. This avoids the `watchfiles` native watcher error `Too many open
files`:

```bash
cd /home/zihai/workspace/Agent_server_design/relationshape/relationshape
PYTHONPATH=. WATCHFILES_FORCE_POLLING=true \
RELATIONSHAPE_BACKEND_DB=runtime/backend.sqlite3 \
python -m uvicorn relationshape.backend.app:app \
  --host 127.0.0.1 --port 8123 \
  --reload --reload-dir relationshape/backend
```

If the port is occupied, either pick another port or inspect the owner:

```bash
lsof -i :8123
python -m uvicorn relationshape.backend.app:app --port 8124
```

## Main flows

1. Create user: `POST /api/v1/admin/users`
2. Create tenant: `POST /api/v1/admin/tenants`
3. Create API key: `POST /api/v1/admin/api-keys`
4. Bind capability config: `PUT /api/v1/admin/capability-configs`
5. Device runtime reads config: `GET /api/v1/runtime/config` with `X-API-Key`
6. Device writes usage event: `POST /api/v1/runtime/events/usage`
7. Device writes question event: `POST /api/v1/runtime/events/question`
8. Admin reads dashboard: `GET /api/v1/admin/tenants/{tenant_id}/dashboard`

For local UI validation, seed a demo workspace from the console or call:

```bash
curl -X POST http://127.0.0.1:8123/api/v1/admin/demo/seed
```

API key plaintext is returned only from the create call. The database stores a
hash plus a short preview.

## Data model

Core tables:

- `users`
- `tenants`
- `tenant_members`
- `api_keys`
- `capability_configs`
- `faq_bases`
- `faq_items`
- `devices`
- `api_usage_events`
- `question_events`

The existing `relationship_state` table remains owned by `relationshape`
state persistence. Runtime orchestration should load a verified API key,
resolve its capability config, call `CompanionEngine.prepare_turn()`, call the
selected ASR/LLM/TTS providers, then call `CompanionEngine.commit()`.

## Dashboard metric definitions

- Active API keys: distinct keys with usage events in the last 24 hours.
- Active devices: devices whose `last_seen_at` is within the last 24 hours.
- Total devices: all devices registered under the tenant.
- Calls: usage events in the last 24 hours.
- Cost: sum of `cost_micros` in the last 24 hours.
- 30-day retention: devices first seen around day 30 that were active in the
  last 24 hours.
- 90-day retention: devices first seen around day 90 that were active in the
  last 24 hours.
- Question ranking: normalized question text grouped over the last 30 days.

## Production hardening still needed

- Replace SQLite with Postgres migrations.
- Add real admin sessions/JWT and tenant membership authorization.
- Enforce rate limits and monthly quotas at the gateway.
- Encrypt provider credentials with KMS or an equivalent secret store.
- Add audit logs for key/config/FAQ changes.
- Split analytics events into ClickHouse or a warehouse once volume grows.
