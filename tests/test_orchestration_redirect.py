import httpx
import pytest

from micast.orchestration import DesiredReceiver, OrchestratorClient


def test_orchestrator_requires_url_and_token():
    assert OrchestratorClient("http://nas.local", "").configured is False
    assert OrchestratorClient("", "secret").configured is False
    assert OrchestratorClient("http://nas.local", "secret").configured is True


@pytest.mark.asyncio
async def test_orchestrator_redirect_policy_is_same_host(monkeypatch):
    requests = []

    async def handler(request):
        requests.append(request)
        if request.url.port is None:
            return httpx.Response(
                302, headers={"location": "http://nas.local:5666/v1/receivers/reconcile"}
            )
        return httpx.Response(200, json={"receivers": []})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    result = await OrchestratorClient("http://nas.local", "secret").reconcile(
        [DesiredReceiver("one", "one", "One", "airplay2")]
    )
    assert result == []
    assert [request.url.port for request in requests] == [None, 5666]


@pytest.mark.asyncio
async def test_orchestrator_rejects_cross_host_redirect(monkeypatch):
    async def handler(request):
        return httpx.Response(302, headers={"location": "http://other.local/steal"})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    with pytest.raises(RuntimeError, match="其他主机"):
        await OrchestratorClient("http://nas.local", "secret").reconcile([])


@pytest.mark.asyncio
async def test_orchestrator_health_checks_expected_endpoint(monkeypatch):
    seen = []

    async def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    assert await OrchestratorClient("http://nas.local/", "secret").health() == {"status": "ok"}
    assert seen == ["http://nas.local/health"]


@pytest.mark.asyncio
async def test_capabilities_are_verified(monkeypatch):
    async def handler(request):
        return httpx.Response(
            200,
            json={
                "api_version": "1",
                "protocols": ["airplay2"],
                "instance_mode": "dynamic",
                "max_instances": 8,
                "features": {},
            },
        )

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    capabilities = await OrchestratorClient("http://nas.local", "secret").capabilities()
    assert capabilities["verified"] is True
    assert capabilities["max_instances"] == 8


@pytest.mark.asyncio
async def test_old_service_is_limited_to_one_compatible_entry(monkeypatch):
    async def handler(request):
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    capabilities = await OrchestratorClient("http://nas.local", "secret").capabilities()
    assert capabilities["verified"] is False
    assert capabilities["max_instances"] == 1
