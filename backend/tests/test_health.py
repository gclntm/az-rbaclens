from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_stage_and_version():
    client = TestClient(app, base_url="http://localhost")
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert "stage" in body
    assert "dry_run" not in body  # the setting is gone: the app never changes Azure
    assert body["status"] in {"ok", "degraded"}

    from app.version import __version__

    assert body["version"] == __version__
