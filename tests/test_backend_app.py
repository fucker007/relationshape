from fastapi.testclient import TestClient

from relationshape.backend.app import app
from relationshape.backend.service import BackendService
from relationshape.backend.store import SQLiteBackendStore


def test_admin_ui_seed_and_snapshot():
    store = SQLiteBackendStore(":memory:")
    original = app.state.backend
    app.state.backend = BackendService(store, key_pepper="test-pepper")
    try:
        client = TestClient(app)

        root = client.get("/")
        assert root.status_code == 200
        assert "relationshape 后台" in root.text
        assert "/api/v1/admin/ui/snapshot" in root.text

        empty = client.get("/api/v1/admin/ui/snapshot")
        assert empty.status_code == 200
        assert empty.json()["tenants"] == []

        seeded = client.post("/api/v1/admin/demo/seed")
        assert seeded.status_code == 200
        payload = seeded.json()
        assert payload["tenants"]
        assert payload["api_keys"]
        assert payload["capability_configs"]
        assert payload["devices"]
        assert payload["faq_items"]
        assert payload["overview"]["active_devices_24h"] >= 1

        relationships = client.get("/api/v1/admin/relationships")
        assert relationships.status_code == 200
        assert "users" in relationships.json()
    finally:
        app.state.backend = original
        store.close()
