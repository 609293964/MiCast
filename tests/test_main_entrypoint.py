import sys
from types import SimpleNamespace

from micast import __main__ as entrypoint


def test_unix_socket_mode_uses_uds_without_resolving_tcp_port(monkeypatch, tmp_path):
    socket_path = tmp_path / "micast.sock"
    fake_app = object()
    calls = []

    monkeypatch.setenv("MICAST_UNIX_SOCKET", str(socket_path))
    monkeypatch.setattr(entrypoint.settings, "port", 3000)
    monkeypatch.setattr(entrypoint, "resolve_port", lambda *_: (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))
    monkeypatch.setitem(sys.modules, "micast.main", SimpleNamespace(app=fake_app))

    entrypoint.main()

    assert calls == [(fake_app, {"uds": str(socket_path), "log_level": "info"})]


def test_tcp_mode_keeps_host_and_resolved_port(monkeypatch):
    fake_app = object()
    calls = []

    monkeypatch.delenv("MICAST_UNIX_SOCKET", raising=False)
    monkeypatch.setattr(entrypoint.settings, "port", 3000)
    monkeypatch.setattr(entrypoint, "resolve_port", lambda *_: 3456)
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))
    monkeypatch.setitem(sys.modules, "micast.main", SimpleNamespace(app=fake_app))

    entrypoint.main()

    assert calls == [
        (fake_app, {"host": entrypoint.settings.host, "port": 3456, "log_level": "info"})
    ]
