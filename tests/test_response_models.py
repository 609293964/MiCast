"""Core read endpoints publish stable response schemas in OpenAPI."""

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from micast.config import Settings
from micast.main import app
from micast.paths import APP_BASE_PATH
from micast.routes import config as config_routes
from micast.routes import tuning as tuning_routes
from micast.routes.models import (
    AudioConfigResponse,
    ConfigResponse,
    TuningStateResponse,
)


def test_openapi_declares_core_response_schemas():
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]

    for model in (AudioConfigResponse, ConfigResponse, TuningStateResponse):
        assert model.__name__ in schemas

    paths = spec["paths"]
    base = f"{APP_BASE_PATH}/api"
    audio_get = paths[f"{base}/config/audio"]["get"]
    assert audio_get["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": f"#/components/schemas/{AudioConfigResponse.__name__}"
    }
    config_get = paths[f"{base}/config"]["get"]
    assert config_get["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": f"#/components/schemas/{ConfigResponse.__name__}"
    }
    tuning_get = paths[f"{base}/tuning/{{did}}"]["get"]
    assert tuning_get["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": f"#/components/schemas/{TuningStateResponse.__name__}"
    }


def test_core_read_payloads_validate_against_response_models(monkeypatch):
    """response_model must accept the real payloads, not just declare schemas."""
    fake = Settings()
    monkeypatch.setattr(config_routes, "settings", fake)
    monkeypatch.setattr(tuning_routes, "settings", fake)
    monkeypatch.setattr(Settings, "save_to_file", lambda self: None)
    bridge = MagicMock()

    app_under_test = FastAPI()
    app_under_test.include_router(config_routes.install(bridge))
    app_under_test.include_router(tuning_routes.install(None, None))
    client = TestClient(app_under_test)

    audio = client.get("/api/config/audio")
    assert audio.status_code == 200
    AudioConfigResponse.model_validate(audio.json())

    full = client.get("/api/config")
    assert full.status_code == 200
    ConfigResponse.model_validate(full.json())

    tuning = client.get("/api/tuning/speaker-a")
    assert tuning.status_code == 200
    TuningStateResponse.model_validate(tuning.json())
