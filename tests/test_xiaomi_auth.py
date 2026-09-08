from types import SimpleNamespace

import pytest

from micast.xiaomi import auth as auth_module
from micast.xiaomi.auth import XiaomiAuth


@pytest.mark.asyncio
async def test_miot_is_not_created_from_micoapi_only_tokens(monkeypatch):
    auth = XiaomiAuth()
    auth._token_store = SimpleNamespace(
        load=lambda: {"userId": "1", "micoapi": ("security", "token")}
    )

    async def must_not_build(_tokens):
        raise AssertionError("micoapi-only tokens must not build a MIoT account")

    monkeypatch.setattr(auth, "_build_account", must_not_build)
    assert await auth.ensure_miot_service() is None


@pytest.mark.asyncio
async def test_miot_uses_an_isolated_account_when_xiaomiio_token_exists(monkeypatch):
    tokens = {
        "userId": "1",
        "micoapi": ("mina-security", "mina-token"),
        "xiaomiio": ("miot-security", "miot-token"),
    }
    auth = XiaomiAuth()
    auth._token_store = SimpleNamespace(load=lambda: tokens)
    auth._account = object()
    isolated = object()

    async def build_account(value):
        assert value is tokens
        return isolated

    monkeypatch.setattr(auth, "_build_account", build_account)
    monkeypatch.setattr(
        auth_module, "MiIOService", lambda account: SimpleNamespace(account=account)
    )

    service = await auth.ensure_miot_service()
    assert service.account is isolated
    assert auth._miot_account is isolated
    assert auth._miot_account is not auth._account
